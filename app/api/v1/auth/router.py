from datetime import datetime, timezone
from fastapi import (
    APIRouter,
    BackgroundTasks,
    Depends,
    HTTPException,
    Request,
    Response,
    status,
)
from sqlalchemy.orm import Session
from starlette.concurrency import run_in_threadpool

from jose import JWTError

from app.core.database import get_db
from app.core.user_service import get_user_by_email, create_user, get_user_by_id
from app.core.security.passwords import hash_password, verify_password
from app.core.security.jwt import (
    create_access_token,
    create_refresh_token,
    decode_refresh_token,
)
from app.core.onboarding_service import upsert_onboarding
from app.core.equipment_weights_service import upsert_equipment_weights
from app.schemas.equipment_weights import EquipmentWeightsPayload
from app.schemas.auth import (
    LoginRequest,
    PasswordResetAcceptedResponse,
    PasswordResetConfirmRequest,
    PasswordResetRequest,
    RefreshRequest,
    RefreshResponse,
    SignUpRequest,
    TokenResponse,
)
from app.core.refresh_token_service import revoke_refresh_token
from app.core.rate_limit import limiter
from app.core.email_service import (
    EmailSender,
    deliver_email,
    get_email_sender,
    password_changed_email,
    password_reset_email,
)
from app.core.legal import PRIVACY_VERSION, TERMS_VERSION
from app.core.password_reset_service import (
    InvalidPasswordResetToken,
    confirm_password_reset,
    request_password_reset,
)
from app.core.response_timing import (
    MinimumResponseBudget,
    get_password_reset_response_budget,
)

router = APIRouter(prefix="/auth", tags=["auth"])


@router.post("/signup", response_model=TokenResponse, status_code=201)
@limiter.limit("3/minute")
def signup(request: Request, payload: SignUpRequest, db: Session = Depends(get_db)):
    acceptance = payload.legal_acceptance
    if (
        not acceptance.accepted
        or acceptance.terms_version != TERMS_VERSION
        or acceptance.privacy_version != PRIVACY_VERSION
    ):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="Current Terms and Privacy Policy must be accepted",
        )

    existing = get_user_by_email(db, payload.email)
    if existing:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Email already in use",
        )

    try:
        user = create_user(
            db,
            email=payload.email,
            preferred_name=payload.preferred_name,
            last_name=payload.last_name,
            password_hash=hash_password(payload.password),
            commit=False,
        )
        user.terms_accepted_version = acceptance.terms_version
        user.privacy_accepted_version = acceptance.privacy_version
        user.legal_accepted_at = datetime.now(timezone.utc)

        if payload.onboarding is not None:
            upsert_onboarding(
                db, str(user.id), payload.onboarding.model_dump(exclude_unset=True), commit=False
            )
            if (
                payload.onboarding.dumbbellWeights is not None
                or payload.onboarding.plateWeights is not None
            ):
                weights = EquipmentWeightsPayload(
                    dumbbell_weights=payload.onboarding.dumbbellWeights or [],
                    plate_weights=payload.onboarding.plateWeights or [],
                )
                upsert_equipment_weights(
                    db,
                    str(user.id),
                    weights.dumbbell_weights,
                    weights.plate_weights,
                    commit=False,
                )
            user.has_completed_onboarding = True
            db.add(user)
        db.commit()
        db.refresh(user)
    except Exception:
        db.rollback()
        raise

    access_token = create_access_token(subject=user.id, auth_version=user.auth_version)
    refresh_token = create_refresh_token(subject=user.id, auth_version=user.auth_version)
    return TokenResponse(access_token=access_token, refresh_token=refresh_token)


@router.post("/login", response_model=TokenResponse)
@limiter.limit("5/minute")
def login(request: Request, payload: LoginRequest, db: Session = Depends(get_db)):
    user = get_user_by_email(db, payload.email)

    if not user or not verify_password(payload.password, user.password_hash):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid email or password",
        )

    access_token = create_access_token(subject=user.id, auth_version=user.auth_version)
    refresh_token = create_refresh_token(subject=user.id, auth_version=user.auth_version)
    return TokenResponse(access_token=access_token, refresh_token=refresh_token)


@router.post("/refresh", response_model=RefreshResponse)
def refresh(payload: RefreshRequest, db: Session = Depends(get_db)):
    try:
        payload_data = decode_refresh_token(payload.refresh_token)
    except JWTError:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid refresh token",
        )

    user_id = payload_data.get("sub")
    jti = payload_data.get("jti")
    exp = payload_data.get("exp")
    if not user_id or not jti or not exp:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid refresh token",
        )

    user = get_user_by_id(db, user_id)
    if not user:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="User not found",
        )

    token_auth_version = payload_data.get("auth_version", 0)
    if (
        not isinstance(token_auth_version, int)
        or isinstance(token_auth_version, bool)
        or token_auth_version != user.auth_version
    ):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid refresh token",
        )

    expires_at = datetime.fromtimestamp(exp, tz=timezone.utc)
    if not revoke_refresh_token(db, jti=jti, user_id=str(user.id), expires_at=expires_at):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Refresh token has been revoked",
        )
    return RefreshResponse(
        access_token=create_access_token(subject=user.id, auth_version=user.auth_version),
        refresh_token=create_refresh_token(subject=user.id, auth_version=user.auth_version),
    )


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
def logout(payload: RefreshRequest, db: Session = Depends(get_db)):
    try:
        data = decode_refresh_token(payload.refresh_token)
    except JWTError:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid refresh token",
        )

    user_id, jti, exp = data.get("sub"), data.get("jti"), data.get("exp")
    if not user_id or not jti or not exp:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid refresh token",
        )

    # A deleted account has no parent row for a revocation record. Logout is
    # still idempotent because deletion already invalidates the token.
    if get_user_by_id(db, user_id):
        revoke_refresh_token(
            db,
            jti=jti,
            user_id=user_id,
            expires_at=datetime.fromtimestamp(exp, tz=timezone.utc),
        )
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post(
    "/password-reset/request",
    response_model=PasswordResetAcceptedResponse,
    status_code=status.HTTP_202_ACCEPTED,
)
@limiter.limit("3/15 minutes")
async def request_password_reset_endpoint(
    request: Request,
    background_tasks: BackgroundTasks,
    payload: PasswordResetRequest,
    db: Session = Depends(get_db),
    email_sender: EmailSender = Depends(get_email_sender),
    response_budget: MinimumResponseBudget = Depends(
        get_password_reset_response_budget
    ),
):
    started_at = response_budget.start()
    try:
        delivery = await run_in_threadpool(
            request_password_reset,
            db,
            str(payload.email),
        )
    finally:
        await response_budget.wait(started_at)
    if delivery is not None:
        message = password_reset_email(
            delivery.recipient,
            delivery.preferred_name,
            delivery.reset_url,
            delivery.reset_id,
        )
        background_tasks.add_task(deliver_email, email_sender, message)
    return PasswordResetAcceptedResponse(
        message="If an account exists for that email, a password reset link has been sent."
    )


@router.post("/password-reset/confirm", status_code=status.HTTP_204_NO_CONTENT)
@limiter.limit("5/15 minutes")
def confirm_password_reset_endpoint(
    request: Request,
    background_tasks: BackgroundTasks,
    payload: PasswordResetConfirmRequest,
    db: Session = Depends(get_db),
    email_sender: EmailSender = Depends(get_email_sender),
):
    try:
        delivery = confirm_password_reset(db, payload.token, payload.new_password)
    except InvalidPasswordResetToken:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="This password reset link is invalid or has expired",
        )

    message = password_changed_email(
        delivery.recipient,
        delivery.preferred_name,
        delivery.reset_id,
    )
    background_tasks.add_task(deliver_email, email_sender, message)
    return Response(status_code=status.HTTP_204_NO_CONTENT)
