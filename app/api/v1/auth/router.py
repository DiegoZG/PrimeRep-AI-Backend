from datetime import datetime, timezone
from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from sqlalchemy.orm import Session

from jose import JWTError

from app.core.database import get_db
from app.core.user_service import get_user_by_email, create_user, get_user_by_id
from app.core.security.passwords import hash_password, verify_password
from app.core.security.jwt import create_access_token, create_refresh_token, decode_refresh_token
from app.core.onboarding_service import upsert_onboarding
from app.core.equipment_weights_service import upsert_equipment_weights
from app.schemas.equipment_weights import EquipmentWeightsPayload
from app.schemas.auth import SignUpRequest, LoginRequest, TokenResponse, RefreshRequest, RefreshResponse
from app.core.refresh_token_service import revoke_refresh_token
from app.core.rate_limit import limiter

router = APIRouter(prefix="/auth", tags=["auth"])


@router.post("/signup", response_model=TokenResponse, status_code=201)
@limiter.limit("3/minute")
def signup(request: Request, payload: SignUpRequest, db: Session = Depends(get_db)):
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

    access_token = create_access_token(subject=user.id)
    refresh_token = create_refresh_token(subject=user.id)
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

    access_token = create_access_token(subject=user.id)
    refresh_token = create_refresh_token(subject=user.id)
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

    expires_at = datetime.fromtimestamp(exp, tz=timezone.utc)
    if not revoke_refresh_token(db, jti=jti, user_id=str(user.id), expires_at=expires_at):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Refresh token has been revoked")
    return RefreshResponse(
        access_token=create_access_token(subject=user.id),
        refresh_token=create_refresh_token(subject=user.id),
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
