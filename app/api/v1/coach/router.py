from datetime import date
from typing import Literal, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from sqlalchemy.orm import Session

from app.core.coach_feed_service import (
    dismiss_item,
    get_feed,
    get_item,
    get_preferences,
    mark_read,
    update_preferences,
)
from app.core.database import get_db
from app.core.security.deps import get_current_user
from app.models.user import User
from app.schemas.coach import (
    CoachFeedItemOut,
    CoachFeedOut,
    CoachPreferenceOut,
    CoachPreferenceUpdate,
    CoachReadRequest,
)


router = APIRouter(tags=["coach"])


@router.get("/coach/feed", response_model=CoachFeedOut)
def read_feed(
    local_date: date = Query(..., alias="localDate"),
    view: Literal["now", "yesterday", "last7Days"] = Query("now"),
    time_zone: str = Query("UTC", alias="timeZone"),
    cursor: Optional[str] = None,
    limit: int = Query(20, ge=1, le=50),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    try:
        return get_feed(
            db,
            str(current_user.id),
            local_date,
            view=view,
            time_zone=time_zone,
            cursor=cursor,
            limit=limit,
        )
    except ValueError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error


@router.get("/coach/items/{item_id}", response_model=CoachFeedItemOut)
def read_item(
    item_id: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    item = get_item(db, str(current_user.id), item_id)
    if item is None:
        raise HTTPException(status_code=404, detail="Coach guidance not found.")
    return item


@router.post("/coach/items/{item_id}/read", response_model=CoachFeedItemOut)
def read_item_action(
    item_id: str,
    body: CoachReadRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    item = mark_read(db, str(current_user.id), item_id, body.reason)
    if item is None:
        raise HTTPException(status_code=404, detail="Coach guidance not found.")
    return item


@router.post("/coach/items/{item_id}/dismiss", status_code=status.HTTP_204_NO_CONTENT)
def dismiss_item_action(
    item_id: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    if not dismiss_item(db, str(current_user.id), item_id):
        raise HTTPException(status_code=404, detail="Coach guidance not found.")
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get("/users/me/coach-preferences", response_model=CoachPreferenceOut)
def read_preferences(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    return get_preferences(db, str(current_user.id))


@router.put("/users/me/coach-preferences", response_model=CoachPreferenceOut)
def replace_preferences(
    body: CoachPreferenceUpdate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    return update_preferences(db, str(current_user.id), body)
