from fastapi import APIRouter, Depends, HTTPException, Response, status
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.equipment_weights_service import (
    get_equipment_weight_arrays,
    upsert_equipment_weights,
)
from app.core.security.deps import get_current_user
from app.core.user_service import delete_user
from app.core.push_token_service import register_push_token, unregister_push_token
from app.models.user import User
from app.schemas.user import UserPreferencesRequest, UserResponse
from app.schemas.equipment_weights import (
    EquipmentWeightsPayload,
    EquipmentWeightsResponse,
)
from app.schemas.push_token import (
    PushTokenDeletePayload,
    PushTokenPayload,
    PushTokenResponse,
)

router = APIRouter(prefix="/users", tags=["users"])


@router.get("/me", response_model=UserResponse)
def read_me(
    current_user: User = Depends(get_current_user),
):
    return current_user


@router.delete("/me", status_code=status.HTTP_204_NO_CONTENT)
def delete_me(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    delete_user(db, current_user)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get("/me/equipment-weights", response_model=EquipmentWeightsResponse)
def read_equipment_weights(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    dumbbell_weights, plate_weights = get_equipment_weight_arrays(
        db, str(current_user.id)
    )
    return EquipmentWeightsResponse(
        dumbbell_weights=dumbbell_weights,
        plate_weights=plate_weights,
    )


@router.put("/me/equipment-weights", response_model=EquipmentWeightsResponse)
def replace_equipment_weights(
    body: EquipmentWeightsPayload,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    weights = upsert_equipment_weights(
        db,
        str(current_user.id),
        body.dumbbell_weights,
        body.plate_weights,
    )
    return EquipmentWeightsResponse(
        dumbbell_weights=weights.dumbbell_weights,
        plate_weights=weights.plate_weights,
    )


@router.patch("/me/preferences", response_model=UserResponse)
def update_preferences(
    body: UserPreferencesRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Toggle AI coach insights. Requires a premium subscription."""
    if current_user.subscription_tier != "premium":
        raise HTTPException(
            status_code=403,
            detail="AI Coach Insights require a Premium subscription.",
        )
    current_user.coach_insights_enabled = body.coach_insights_enabled
    db.commit()
    db.refresh(current_user)
    return current_user


@router.post("/me/push-token", response_model=PushTokenResponse)
def upsert_push_token(
    body: PushTokenPayload,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    push_token = register_push_token(
        db,
        user_id=str(current_user.id),
        token=body.token,
        platform=body.platform,
    )
    return PushTokenResponse(token=push_token.token, platform=push_token.platform)


@router.delete("/me/push-token", status_code=status.HTTP_204_NO_CONTENT)
def delete_push_token(
    body: PushTokenDeletePayload,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    unregister_push_token(db, user_id=str(current_user.id), token=body.token)
    return Response(status_code=status.HTTP_204_NO_CONTENT)
