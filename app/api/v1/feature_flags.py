from fastapi import APIRouter, Depends, HTTPException
from fastapi.exceptions import RequestValidationError
from fastapi.routing import APIRoute
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.feature_flag_service import get_feature_flags, update_feature_flags
from app.core.security.deps import get_current_user
from app.models.user import User
from app.schemas.feature_flags import AdminFeatureFlagSnapshot, FeatureFlagPatch, FeatureFlagSnapshot


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
def read_flags(db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    return get_feature_flags(db, str(current_user.id))


@router.get("/admin/feature-flags", response_model=AdminFeatureFlagSnapshot)
def read_admin_flags(db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    return get_feature_flags(db, str(current_user.id), admin=True)


@router.patch("/admin/feature-flags", response_model=AdminFeatureFlagSnapshot)
def patch_flags(body: FeatureFlagPatch, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    return update_feature_flags(db, str(current_user.id), body)
