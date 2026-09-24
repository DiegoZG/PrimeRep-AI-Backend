from typing import Literal, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import FileResponse
from sqlalchemy.orm import Session

from app.core.achievement_service import get_achievements
from app.core.database import get_db
from app.core.export_service import ExportArchive, ExportTooLarge, create_export
from app.core.rate_limit import user_limiter
from app.core.security.deps import get_current_user
from app.core.settings import settings
from app.models.user import User
from app.schemas.portability import AchievementsOut


router = APIRouter(prefix="/users", tags=["users"])


class PrivateExportResponse(FileResponse):
    def __init__(self, archive: ExportArchive):
        super().__init__(archive.path, filename=archive.filename, media_type="application/zip", headers={"Cache-Control": "no-store", "X-Content-Type-Options": "nosniff"})
        self.archive = archive

    async def __call__(self, scope, receive, send):
        try:
            await super().__call__(scope, receive, send)
        finally:
            self.archive.cleanup()


@router.get("/me/achievements", response_model=AchievementsOut)
def achievements(
    time_zone: Optional[str] = Query(None, max_length=100),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    try:
        return get_achievements(db, current_user.id, time_zone)
    except ValueError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error


@router.get("/me/export", response_class=FileResponse)
@user_limiter.limit(f"{settings.PERSONAL_EXPORTS_PER_HOUR}/hour")
def export_personal_data(
    request: Request,
    weight_unit: Literal["LB", "KG"] = "KG",
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    try:
        archive = create_export(db.get_bind(), current_user.id, current_user.auth_version, weight_unit)
    except ExportTooLarge as error:
        raise HTTPException(status_code=413, detail=str(error)) from error
    return PrivateExportResponse(archive)
