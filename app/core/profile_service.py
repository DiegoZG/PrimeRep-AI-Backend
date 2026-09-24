from __future__ import annotations

from datetime import datetime, timezone
from io import BytesIO
import uuid
import warnings

from fastapi import HTTPException
from PIL import Image, ImageOps, UnidentifiedImageError
from sqlalchemy.orm import Session

from app.core.onboarding_service import canonical_onboarding_data
from app.models.onboarding_profile import OnboardingProfile
from app.models.user import User
from app.models.user_avatar import UserAvatar
from app.schemas.profile import KG_PER_LB, ProfilePatch, ProfileResponse, WeightUnit


MAX_AVATAR_UPLOAD_BYTES = 5 * 1024 * 1024
MAX_AVATAR_PIXELS = 20_000_000
MAX_AVATAR_BYTES = 256 * 1024


def get_profile(db: Session, user: User, weight_unit: WeightUnit | None = None) -> ProfileResponse:
    onboarding = db.get(OnboardingProfile, user.id)
    avatar = db.get(UserAvatar, user.id)
    data = onboarding.data if onboarding else {}
    stored_unit = data.get("weightUnit") if data.get("weightUnit") in {"LB", "KG"} else "LB"
    display_unit = weight_unit or stored_unit
    weight = data.get("weight")
    if weight is not None and display_unit != stored_unit:
        weight = weight * KG_PER_LB if display_unit == "KG" else weight / KG_PER_LB
    updated_at = max(row.updated_at for row in (user, onboarding, avatar) if row is not None)
    return ProfileResponse(
        id=user.id, preferred_name=user.preferred_name, last_name=user.last_name,
        email=user.email, weight=weight, weight_unit=display_unit,
        age=data.get("age"), gender=data.get("gender"),
        avatar_version=avatar.version if avatar else None, updated_at=updated_at,
    )


def update_profile(db: Session, user_id: str, patch: ProfilePatch) -> ProfileResponse:
    user = db.query(User).filter(User.id == user_id).with_for_update().populate_existing().one()
    fields = patch.model_dump(exclude_unset=True)
    if not fields:
        return get_profile(db, user)
    onboarding = db.query(OnboardingProfile).filter(
        OnboardingProfile.user_id == user_id
    ).populate_existing().first()
    if onboarding is None:
        onboarding = OnboardingProfile(user_id=user_id, data={}, profile_managed_fields=[])
        db.add(onboarding)
    if "preferred_name" in fields:
        user.preferred_name = patch.preferred_name
    if "last_name" in fields:
        user.last_name = patch.last_name or None
    data = canonical_onboarding_data(onboarding, user)
    managed = set(onboarding.profile_managed_fields or [])
    for key in ("age", "gender"):
        if key in fields:
            data[key] = fields[key]
            managed.add(key)
    if "weight" in fields or "weight_unit" in fields:
        old_unit = data.get("weightUnit") if data.get("weightUnit") in {"LB", "KG"} else "LB"
        new_unit = patch.weight_unit or old_unit
        weight = fields.get("weight", data.get("weight"))
        if "weight" not in fields and weight is not None and old_unit != new_unit:
            weight = weight * KG_PER_LB if new_unit == "KG" else weight / KG_PER_LB
        data.update(weight=weight, weightUnit=new_unit)
        managed.update(("weight", "weightUnit"))
    onboarding.data = data
    onboarding.profile_managed_fields = sorted(managed)
    user.updated_at = datetime.now(timezone.utc)
    db.commit()
    return get_profile(db, user)


def normalize_avatar(raw: bytes, content_type: str) -> bytes:
    if len(raw) > MAX_AVATAR_UPLOAD_BYTES:
        raise HTTPException(status_code=413, detail="Photo must be 5 MB or smaller")
    if content_type not in {"image/jpeg", "image/png"}:
        raise HTTPException(status_code=415, detail="Choose a JPEG or PNG photo")
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("error", Image.DecompressionBombWarning)
            with Image.open(BytesIO(raw)) as original:
                if original.format not in {"JPEG", "PNG"} or Image.MIME[original.format] != content_type:
                    raise ValueError("Unsupported image content")
                if original.width * original.height > MAX_AVATAR_PIXELS:
                    raise ValueError("Image dimensions too large")
                original.verify()
            with Image.open(BytesIO(raw)) as original:
                original.load()
                oriented = ImageOps.exif_transpose(original)
                oriented.thumbnail((512, 512), Image.Resampling.LANCZOS)
                rgba = oriented.convert("RGBA")
                clean = Image.new("RGB", rgba.size, "white")
                clean.paste(rgba, mask=rgba.getchannel("A"))
                for quality in (85, 70, 55, 40):
                    output = BytesIO()
                    clean.save(output, format="JPEG", quality=quality, optimize=True)
                    result = output.getvalue()
                    if len(result) <= MAX_AVATAR_BYTES:
                        return result
    except (UnidentifiedImageError, OSError, ValueError, SyntaxError, Image.DecompressionBombError, Image.DecompressionBombWarning):
        raise HTTPException(status_code=422, detail="This photo could not be read. Choose another JPEG or PNG.") from None
    raise HTTPException(status_code=422, detail="This photo could not be resized. Choose another photo.")


def replace_avatar(db: Session, user_id: str, raw: bytes, content_type: str) -> ProfileResponse:
    data = normalize_avatar(raw, content_type)
    user = db.query(User).filter(User.id == user_id).with_for_update().populate_existing().one()
    avatar = db.get(UserAvatar, user_id)
    if avatar is None:
        avatar = UserAvatar(user_id=user_id)
        db.add(avatar)
    now = datetime.now(timezone.utc)
    avatar.data = data
    avatar.content_type = "image/jpeg"
    avatar.version = str(uuid.uuid4())
    avatar.updated_at = now
    user.updated_at = now
    db.commit()
    return get_profile(db, user)


def remove_avatar(db: Session, user_id: str) -> ProfileResponse:
    user = db.query(User).filter(User.id == user_id).with_for_update().populate_existing().one()
    avatar = db.get(UserAvatar, user_id)
    if avatar is not None:
        db.delete(avatar)
        user.updated_at = datetime.now(timezone.utc)
        db.commit()
    return get_profile(db, user)
