from typing import Optional
from datetime import datetime, timedelta, timezone

from fastapi import HTTPException
from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.settings import settings
from app.models.feature_flags import FeatureFlagAudit, FeatureFlagConfig
from app.schemas.feature_flags import FeatureFlagPatch


def flag_environment() -> str:
    environment = settings.APP_ENV.strip().lower()
    return "production" if environment == "prod" else environment


def can_manage_flags(user_id: str) -> bool:
    return str(user_id) in settings.FEATURE_FLAG_ADMIN_USER_IDS


def require_flag_admin(user_id: str) -> None:
    if not can_manage_flags(user_id):
        raise HTTPException(status_code=403, detail="You do not have permission to manage feature flags")


def _utc(value: Optional[datetime]) -> Optional[datetime]:
    if value is None:
        return None
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value.astimezone(timezone.utc)


def _state(row: Optional[FeatureFlagConfig]) -> dict:
    return {
        "flags": {
            "subscriptions_purchases_enabled": bool(row and row.subscriptions_purchases_enabled),
            "subscriptions_enforcement_enabled": bool(row and row.subscriptions_enforcement_enabled),
        },
        "subscriptions_launch_at": _utc(row.subscriptions_launch_at) if row else None,
        "launch_locked": bool(row and row.launch_locked),
    }


def _snapshot(row: Optional[FeatureFlagConfig], user_id: str, environment: str) -> dict:
    state = _state(row)
    launch_at = state["subscriptions_launch_at"]
    return {
        "environment": environment,
        "version": row.version if row else 0,
        **state,
        "transition_ends_at": launch_at + timedelta(days=30) if launch_at else None,
        "can_manage_flags": can_manage_flags(user_id),
        "updated_at": _utc(row.updated_at) if row else None,
    }


def _history(db: Session, environment: str, version: int) -> list[dict]:
    rows = db.scalars(
        select(FeatureFlagAudit).where(
            FeatureFlagAudit.environment == environment, FeatureFlagAudit.version <= version
        )
        .order_by(FeatureFlagAudit.version.desc()).limit(20)
    ).all()
    return [
        {"version": row.version, "changed_at": _utc(row.changed_at),
         "changed_by": row.changed_by, "before": row.before, "after": row.after}
        for row in rows
    ]


def get_feature_flags(db: Session, user_id: str, *, admin: bool = False) -> dict:
    if admin:
        require_flag_admin(user_id)
    environment = flag_environment()
    row = db.get(FeatureFlagConfig, environment, populate_existing=True)
    result = _snapshot(row, user_id, environment)
    if admin:
        result["history"] = _history(db, environment, result["version"])
    return result


def _audit_state(state: dict) -> dict:
    launch = state["subscriptions_launch_at"]
    return {**state, "subscriptions_launch_at": launch.isoformat() if launch else None}


def update_feature_flags(db: Session, user_id: str, patch: FeatureFlagPatch) -> dict:
    require_flag_admin(user_id)
    environment = flag_environment()
    row = db.scalar(
        select(FeatureFlagConfig).where(FeatureFlagConfig.environment == environment)
        .with_for_update().execution_options(populate_existing=True)
    )
    version = row.version if row else 0
    if patch.expected_version != version:
        raise HTTPException(status_code=409, detail="Feature flags changed. Reload before saving again")
    before = _state(row)
    after = {**before, "flags": {**before["flags"], **patch.flags.model_dump(exclude_unset=True)}}
    if "subscriptions_launch_at" in patch.model_fields_set:
        if before["launch_locked"] and patch.subscriptions_launch_at != before["subscriptions_launch_at"]:
            raise HTTPException(status_code=422, detail="The launch date is frozen after enforcement first starts")
        after["subscriptions_launch_at"] = patch.subscriptions_launch_at
    now = datetime.now(timezone.utc)
    if after["flags"]["subscriptions_enforcement_enabled"]:
        if not after["flags"]["subscriptions_purchases_enabled"]:
            raise HTTPException(status_code=422, detail="Enable purchases before enabling enforcement")
        if after["subscriptions_launch_at"] is None or after["subscriptions_launch_at"] > now:
            raise HTTPException(status_code=422, detail="Enforcement requires a launch date at or before now")
        after["launch_locked"] = True
    if before == after:
        return {**_snapshot(row, user_id, environment), "history": _history(db, environment, version)}
    values = {
        **after["flags"], "subscriptions_launch_at": after["subscriptions_launch_at"],
        "launch_locked": after["launch_locked"], "version": version + 1, "updated_at": now,
    }
    try:
        if row is None:
            row = FeatureFlagConfig(environment=environment, **values)
            db.add(row)
            db.flush()
        else:
            changed = db.execute(
                update(FeatureFlagConfig).where(
                    FeatureFlagConfig.environment == environment, FeatureFlagConfig.version == version
                ).values(**values).execution_options(synchronize_session=False)
            )
            if changed.rowcount != 1:
                db.rollback()
                raise HTTPException(status_code=409, detail="Feature flags changed. Reload before saving again")
            db.refresh(row)
        db.add(FeatureFlagAudit(
            environment=environment, version=version + 1, changed_at=now,
            changed_by=str(user_id), before=_audit_state(before), after=_audit_state(after),
        ))
        db.flush()
        result = {**_snapshot(row, user_id, environment), "history": _history(db, environment, version + 1)}
        db.commit()
        return result
    except IntegrityError as error:
        db.rollback()
        raise HTTPException(status_code=409, detail="Feature flags changed. Reload before saving again") from error
    except Exception:
        db.rollback()
        raise
