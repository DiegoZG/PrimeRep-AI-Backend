from typing import Literal
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Response
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.legal import PRIVACY_VERSION
from app.core.health_service import HealthConflict, HealthInvalid, HealthUnavailable, disconnect_source, enable_source, read_health, update_preferences, upsert_days
from app.core.security.deps import get_current_user
from app.models.user import User
from app.models.health import HealthSource
from app.schemas.health import HealthDaysRequest, HealthDaysResult, HealthPreferencesRequest, HealthSourceOut, HealthSourceRequest, HealthStateOut


def _private_response(response: Response):
    response.headers["Cache-Control"] = "private, no-store"


router = APIRouter(prefix="/users/me/health", tags=["health"], dependencies=[Depends(_private_response)])


def _error(error: Exception) -> HTTPException:
    if isinstance(error, HealthUnavailable):
        return HTTPException(status_code=503, detail=str(error))
    if isinstance(error, HealthConflict):
        return HTTPException(status_code=409, detail=str(error))
    return HTTPException(status_code=422, detail=str(error))


@router.get("", response_model=HealthStateOut)
def get_health(db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    state = read_health(db, str(user.id))
    state["collectionAvailable"] = state["collectionAvailable"] and user.privacy_accepted_version == PRIVACY_VERSION
    return state


@router.post("/sources", response_model=HealthSourceOut)
def post_source(body: HealthSourceRequest, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    existing = db.query(HealthSource).filter_by(user_id=str(user.id), source=body.source, enabled=True).one_or_none()
    if user.privacy_accepted_version != PRIVACY_VERSION and (
        existing is None
        or (body.read_enabled and not existing.read_enabled)
        or (body.export_enabled and not existing.export_enabled)
    ):
        raise HTTPException(status_code=403, detail="Accept the updated Privacy Policy before connecting Health")
    try:
        return enable_source(db, str(user.id), body)
    except (HealthUnavailable, HealthInvalid, HealthConflict) as error:
        raise _error(error) from error


@router.put("/preferences", response_model=HealthStateOut)
def put_preferences(body: HealthPreferencesRequest, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    if body.coach_enabled and user.privacy_accepted_version != PRIVACY_VERSION:
        raise HTTPException(status_code=403, detail="Accept the updated Privacy Policy before using Health in Coach")
    try:
        return update_preferences(db, str(user.id), body)
    except (HealthUnavailable, HealthInvalid) as error:
        raise _error(error) from error


@router.put("/days", response_model=HealthDaysResult)
def put_days(body: HealthDaysRequest, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    if user.privacy_accepted_version != PRIVACY_VERSION:
        raise HTTPException(status_code=403, detail="Accept the updated Privacy Policy before syncing Health")
    try:
        return upsert_days(db, str(user.id), body)
    except (HealthUnavailable, HealthConflict, HealthInvalid) as error:
        raise _error(error) from error


@router.delete("/sources/{source_name}", status_code=204)
def delete_source(source_name: Literal["apple_health", "health_connect"], expected_connection_revision: Optional[int] = Query(None, alias="expectedConnectionRevision", ge=1), db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    try:
        disconnect_source(db, str(user.id), source_name, expected_connection_revision)
    except HealthConflict as error:
        raise _error(error) from error
    return Response(status_code=204)
