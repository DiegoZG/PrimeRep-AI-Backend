from typing import Optional
from datetime import datetime, timedelta, timezone
from uuid import UUID

from fastapi import HTTPException
from sqlalchemy import func, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.settings import settings
from app.models.feature_flags import FeatureFlagAudit, FeatureFlagConfig, FeatureFlagUserOverride
from app.models.user import User
from app.schemas.feature_flags import FeatureFlagPatch, UserFeatureFlagPatch

FLAG_COLUMNS = {
    "subscriptions_purchases_enabled": "subscriptions_purchases_audience",
    "subscriptions_enforcement_enabled": "subscriptions_enforcement_audience",
}


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
        "audiences": {flag: getattr(row, column) if row else "everyone" for flag, column in FLAG_COLUMNS.items()},
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


def _config(db: Session, environment: str, *, write: bool = False) -> Optional[FeatureFlagConfig]:
    # All writers lock this row, so a shared lock keeps multi-query snapshots coherent.
    return db.scalar(
        select(FeatureFlagConfig).where(FeatureFlagConfig.environment == environment)
        .with_for_update(read=not write).execution_options(populate_existing=True)
    )


def _overrides(row: Optional[FeatureFlagUserOverride]) -> dict:
    return {flag: getattr(row, flag) if row else None for flag in FLAG_COLUMNS}


def _override_row(db: Session, environment: str, user_id: str) -> Optional[FeatureFlagUserOverride]:
    return db.get(FeatureFlagUserOverride, (environment, user_id), populate_existing=True)


def _effective(state: dict, overrides: dict) -> dict:
    result = {
        flag: state["flags"][flag] and (
            overrides[flag] if overrides[flag] is not None else state["audiences"][flag] == "everyone"
        )
        for flag in FLAG_COLUMNS
    }
    result["subscriptions_enforcement_enabled"] &= result["subscriptions_purchases_enabled"]
    return result


def _history(db: Session, environment: str, version: int) -> list[dict]:
    rows = db.scalars(
        select(FeatureFlagAudit).where(
            FeatureFlagAudit.environment == environment, FeatureFlagAudit.version <= version
        )
        .order_by(FeatureFlagAudit.version.desc()).limit(20)
    ).all()
    return [
        {"version": row.version, "changed_at": _utc(row.changed_at),
         "changed_by": row.changed_by, "scope": row.scope, "target_user_id": row.target_user_id,
         "before": row.before, "after": row.after}
        for row in rows
    ]


def get_feature_flags(db: Session, user_id: str, *, admin: bool = False) -> dict:
    if admin:
        require_flag_admin(user_id)
    environment = flag_environment()
    row = _config(db, environment)
    result = _snapshot(row, user_id, environment)
    if admin:
        result["history"] = _history(db, environment, result["version"])
    else:
        override = _override_row(db, environment, str(user_id)) if row else None
        result["flags"] = _effective(_state(row), _overrides(override))
        result.pop("audiences")
    return result


def _audit_state(state: dict) -> dict:
    launch = state["subscriptions_launch_at"]
    return {**state, "subscriptions_launch_at": launch.isoformat() if launch else None}


def _check_version(row: Optional[FeatureFlagConfig], version: int) -> None:
    if version != (row.version if row else 0):
        raise HTTPException(status_code=409, detail="Feature flags changed. Reload before saving again")


def _persist_config(db: Session, environment: str, row: Optional[FeatureFlagConfig], state: dict, now: datetime) -> FeatureFlagConfig:
    version = row.version if row else 0
    values = {
        **state["flags"], **{column: state["audiences"][flag] for flag, column in FLAG_COLUMNS.items()},
        "subscriptions_launch_at": state["subscriptions_launch_at"],
        "launch_locked": state["launch_locked"], "version": version + 1, "updated_at": now,
    }
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
            raise HTTPException(status_code=409, detail="Feature flags changed. Reload before saving again")
        db.refresh(row)
    return row


def update_feature_flags(db: Session, user_id: str, patch: FeatureFlagPatch) -> dict:
    require_flag_admin(user_id)
    environment = flag_environment()
    row = _config(db, environment, write=True)
    version = row.version if row else 0
    _check_version(row, patch.expected_version)
    before = _state(row)
    after = {
        **before, "flags": {**before["flags"], **patch.flags.model_dump(exclude_unset=True)},
        "audiences": {**before["audiences"], **patch.audiences.model_dump(exclude_unset=True)},
    }
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
    try:
        row = _persist_config(db, environment, row, after, now)
        db.add(FeatureFlagAudit(
            environment=environment, version=version + 1, changed_at=now,
            changed_by=str(user_id), scope="global", before=_audit_state(before), after=_audit_state(after),
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


def _target(db: Session, user_id: str, *, lock: bool = False) -> dict:
    query = select(User.id, User.email, User.preferred_name).where(User.id == user_id)
    if lock:
        query = query.with_for_update(read=True)
    user = db.execute(query).mappings().one_or_none()
    if user is None:
        raise HTTPException(status_code=404, detail="User not found")
    return dict(user)


def _user_snapshot(db: Session, row: Optional[FeatureFlagConfig], environment: str, user: dict) -> dict:
    state = _state(row)
    overrides = _overrides(_override_row(db, environment, user["id"]) if row else None)
    launch = state["subscriptions_launch_at"]
    return {
        "environment": environment, "version": row.version if row else 0, "user": user,
        "overrides": overrides, "effective_flags": _effective(state, overrides),
        "global_flags": state["flags"], "audiences": state["audiences"],
        "subscriptions_launch_at": launch, "transition_ends_at": launch + timedelta(days=30) if launch else None,
        "launch_locked": state["launch_locked"],
    }


def get_user_feature_flags(db: Session, admin_id: str, user_id: str) -> dict:
    require_flag_admin(admin_id)
    environment = flag_environment()
    row = _config(db, environment)
    return _user_snapshot(db, row, environment, _target(db, user_id))


def lookup_user_feature_flags(db: Session, admin_id: str, identifier: str) -> dict:
    require_flag_admin(admin_id)
    identifier = identifier.strip()
    try:
        user_id = str(UUID(identifier))
    except ValueError:
        user_id = db.scalar(select(User.id).where(func.lower(User.email) == identifier.lower()))
        if user_id is None:
            raise HTTPException(status_code=404, detail="User not found")
    return get_user_feature_flags(db, admin_id, user_id)


def list_user_feature_flags(db: Session, admin_id: str, limit: int, offset: int) -> dict:
    require_flag_admin(admin_id)
    environment = flag_environment()
    row = _config(db, environment)
    if row is None:
        return {"environment": environment, "version": 0, "items": [], "total": 0, "limit": limit, "offset": offset}
    query = select(
        FeatureFlagUserOverride, User.id, User.email, User.preferred_name, func.count().over().label("total")
    ).join(User, User.id == FeatureFlagUserOverride.user_id).where(FeatureFlagUserOverride.environment == environment)
    rows = db.execute(query.order_by(User.id).limit(limit).offset(offset)).all()
    total = rows[0].total if rows else db.scalar(select(func.count()).select_from(
        FeatureFlagUserOverride
    ).where(FeatureFlagUserOverride.environment == environment))
    return {
        "environment": environment, "version": row.version, "total": total, "limit": limit, "offset": offset,
        "items": [
            {"user": {"id": user_id, "email": email, "preferred_name": name}, "overrides": _overrides(item),
             "effective_flags": _effective(_state(row), _overrides(item))}
            for item, user_id, email, name, _ in rows
        ],
    }


def update_user_feature_flags(db: Session, admin_id: str, user_id: str, patch: UserFeatureFlagPatch) -> dict:
    require_flag_admin(admin_id)
    environment = flag_environment()
    row = _config(db, environment, write=True)
    user = _target(db, user_id, lock=True)
    _check_version(row, patch.expected_version)
    override = _override_row(db, environment, user_id) if row else None
    before = _overrides(override)
    after = {**before, **patch.overrides.model_dump(exclude_unset=True)}
    if before == after:
        return _user_snapshot(db, row, environment, user)
    now = datetime.now(timezone.utc)
    try:
        row = _persist_config(db, environment, row, _state(row), now)
        if all(value is None for value in after.values()):
            db.delete(override)
        elif override is None:
            db.add(FeatureFlagUserOverride(environment=environment, user_id=user_id, **after))
        else:
            for flag, value in after.items():
                setattr(override, flag, value)
        db.add(FeatureFlagAudit(
            environment=environment, version=row.version, changed_at=now, changed_by=str(admin_id),
            scope="user", target_user_id=user_id, before={"overrides": before}, after={"overrides": after},
        ))
        db.flush()
        result = _user_snapshot(db, row, environment, user)
        db.commit()
        return result
    except IntegrityError as error:
        db.rollback()
        raise HTTPException(status_code=409, detail="Feature flags changed. Reload before saving again") from error
    except Exception:
        db.rollback()
        raise
