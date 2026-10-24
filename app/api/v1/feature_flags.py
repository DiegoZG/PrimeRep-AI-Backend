from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.exceptions import RequestValidationError
from fastapi.routing import APIRoute
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.feature_flag_service import (
    get_feature_flags, get_user_feature_flags, list_user_feature_flags,
    lookup_user_feature_flags, update_feature_flags, update_user_feature_flags,
)
from app.core.security.deps import get_current_user
from app.models.user import User
from app.schemas.feature_flags import (
    AdminFeatureFlagSnapshot, FeatureFlagPatch, FeatureFlagSnapshot,
    FeatureFlagUserList, FeatureFlagUserLookup, UserFeatureFlagPatch, UserFeatureFlagSnapshot,
)


class FeatureFlagRoute(APIRoute):
    def get_route_handler(self):
        handler = super().get_route_handler()

        async def handle(request):
            try:
                return await handler(request)
            except RequestValidationError as error:
                messages = [f"{'.'.join(str(part) for part in item['loc'])}: {item['msg']}" for item in error.errors()]
                raise HTTPException(status_code=422, detail="; ".join(messages)) from error

        return handle


router = APIRouter(tags=["feature-flags"], route_class=FeatureFlagRoute)


@router.get("/feature-flags", response_model=FeatureFlagSnapshot)
def read_flags(request: Request, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    if request.query_params:
        raise HTTPException(status_code=422, detail="Feature flag reads do not accept user or environment selectors")
    return get_feature_flags(db, str(current_user.id))


@router.get("/admin/feature-flags", response_model=AdminFeatureFlagSnapshot)
def read_admin_flags(db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    return get_feature_flags(db, str(current_user.id), admin=True)


@router.patch("/admin/feature-flags", response_model=AdminFeatureFlagSnapshot)
def patch_flags(body: FeatureFlagPatch, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    return update_feature_flags(db, str(current_user.id), body)


@router.post("/admin/feature-flags/users/lookup", response_model=UserFeatureFlagSnapshot)
def lookup_flag_user(body: FeatureFlagUserLookup, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    return lookup_user_feature_flags(db, str(current_user.id), body.identifier)


@router.get("/admin/feature-flags/users", response_model=FeatureFlagUserList)
def list_flag_users(
    limit: int = Query(20, ge=1, le=100), offset: int = Query(0, ge=0),
    db: Session = Depends(get_db), current_user: User = Depends(get_current_user),
):
    return list_user_feature_flags(db, str(current_user.id), limit, offset)


@router.get("/admin/feature-flags/users/{user_id}", response_model=UserFeatureFlagSnapshot)
def read_flag_user(user_id: UUID, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    return get_user_feature_flags(db, str(current_user.id), str(user_id))


@router.patch("/admin/feature-flags/users/{user_id}", response_model=UserFeatureFlagSnapshot)
def patch_flag_user(
    user_id: UUID, body: UserFeatureFlagPatch,
    db: Session = Depends(get_db), current_user: User = Depends(get_current_user),
):
    return update_user_feature_flags(db, str(current_user.id), str(user_id), body)
