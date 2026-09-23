from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response
from sqlalchemy.orm import Session
from starlette.concurrency import run_in_threadpool

from app.core.database import get_db
from app.core.profile_service import MAX_AVATAR_UPLOAD_BYTES, get_profile, remove_avatar, replace_avatar, update_profile
from app.core.security.deps import get_current_user
from app.models.user import User
from app.models.user_avatar import UserAvatar
from app.schemas.profile import ProfilePatch, ProfileResponse, WeightUnit


router = APIRouter(prefix="/users", tags=["profile"])


@router.get("/me/profile", response_model=ProfileResponse)
def read_profile(
    response: Response,
    weight_unit: Optional[WeightUnit] = Query(default=None),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    response.headers["Cache-Control"] = "private, no-store"
    return get_profile(db, current_user, weight_unit)


@router.patch("/me/profile", response_model=ProfileResponse)
def patch_profile(
    payload: ProfilePatch,
    response: Response,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    response.headers["Cache-Control"] = "private, no-store"
    return update_profile(db, current_user.id, payload)


@router.get("/me/avatar")
def read_avatar(db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    avatar = db.get(UserAvatar, current_user.id)
    if avatar is None:
        raise HTTPException(status_code=404, detail="No profile photo")
    return Response(
        content=avatar.data, media_type=avatar.content_type,
        headers={"Cache-Control": "private, no-store", "X-Content-Type-Options": "nosniff"},
    )


@router.put("/me/avatar", response_model=ProfileResponse)
async def put_avatar(
    request: Request,
    response: Response,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    content_type = request.headers.get("content-type", "").split(";")[0].strip().lower()
    if content_type not in {"image/jpeg", "image/png"}:
        raise HTTPException(status_code=415, detail="Choose a JPEG or PNG photo")
    raw = bytearray()
    async for chunk in request.stream():
        if len(raw) + len(chunk) > MAX_AVATAR_UPLOAD_BYTES:
            raise HTTPException(status_code=413, detail="Photo must be 5 MB or smaller")
        raw.extend(chunk)
    response.headers["Cache-Control"] = "private, no-store"
    return await run_in_threadpool(replace_avatar, db, current_user.id, bytes(raw), content_type)


@router.delete("/me/avatar", response_model=ProfileResponse)
def delete_avatar(
    response: Response,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    response.headers["Cache-Control"] = "private, no-store"
    return remove_avatar(db, current_user.id)
