from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from app.core.settings import settings
from app.models.health import HealthDay, HealthPreference, HealthSource
from app.models.coach import CoachFeedItem
from app.schemas.health import HealthDaysRequest, HealthPreferencesRequest, HealthSourceRequest


class HealthUnavailable(Exception):
    pass


class HealthConflict(Exception):
    pass


class HealthInvalid(Exception):
    pass


def _require_collection() -> None:
    if not settings.HEALTH_COLLECTION_ENABLED:
        raise HealthUnavailable("Health collection is not available yet")


def _local_today(now: datetime, time_zone: str):
    return now.astimezone(ZoneInfo(time_zone)).date()


def _cutoff(now: datetime, time_zone: str):
    return _local_today(now, time_zone) - timedelta(days=90)


def _purge(db: Session, user_id: str, now: datetime) -> None:
    zones = db.query(HealthDay.time_zone).filter(HealthDay.user_id == user_id).distinct().all()
    for (time_zone,) in zones:
        db.query(HealthDay).filter(HealthDay.user_id == user_id, HealthDay.time_zone == time_zone, HealthDay.local_date < _cutoff(now, time_zone)).delete(synchronize_session=False)


def purge_expired_days(db: Session, now: datetime) -> int:
    deleted = 0
    for (time_zone,) in db.query(HealthDay.time_zone).distinct().all():
        deleted += db.query(HealthDay).filter(HealthDay.time_zone == time_zone, HealthDay.local_date < _cutoff(now, time_zone)).delete(synchronize_session=False)
    db.commit()
    return deleted


def _source_out(row: HealthSource) -> dict:
    return {
        "source": row.source,
        "connectionRevision": row.connection_revision,
        "readEnabled": row.read_enabled,
        "exportEnabled": row.export_enabled,
        "connectedAt": row.connected_at,
        "lastSuccessfulSyncAt": row.last_successful_sync_at,
    }


def read_health(db: Session, user_id: str) -> dict:
    now = datetime.now(timezone.utc)
    _purge(db, user_id, now)
    sources = db.query(HealthSource).filter(HealthSource.user_id == user_id, HealthSource.enabled.is_(True)).order_by(HealthSource.source).all()
    prefs = db.get(HealthPreference, user_id)
    days = db.query(HealthDay).filter(HealthDay.user_id == user_id).order_by(HealthDay.local_date.desc(), HealthDay.source, HealthDay.time_zone).all()
    db.commit()
    return {
        "collectionAvailable": settings.HEALTH_COLLECTION_ENABLED,
        "sources": [_source_out(source) for source in sources],
        "primarySource": prefs.primary_source if prefs else None,
        "coachEnabled": prefs.coach_enabled if prefs else False,
        "days": [{"source": day.source, "localDate": day.local_date, "timeZone": day.time_zone, "steps": day.steps, "asleepMinutes": day.asleep_minutes} for day in days],
    }


def enable_source(db: Session, user_id: str, body: HealthSourceRequest) -> dict:
    _require_collection()
    if not body.read_enabled and not body.export_enabled:
        raise HealthInvalid("Enable reading or workout export")
    now = datetime.now(timezone.utc)
    try:
        inserted = db.execute(insert(HealthSource).values(user_id=user_id, source=body.source, connection_revision=1, enabled=False).on_conflict_do_nothing(index_elements=["user_id", "source"]).returning(HealthSource.user_id)).first() is not None
        row = db.query(HealthSource).filter_by(user_id=user_id, source=body.source).with_for_update().one()
        if row.enabled and row.read_enabled == body.read_enabled and row.export_enabled == body.export_enabled:
            db.commit()
            return _source_out(row)
        if not inserted:
            row.connection_revision += 1
        row.enabled = True
        row.read_enabled = body.read_enabled
        row.export_enabled = body.export_enabled
        row.connected_at = now
        row.disconnected_at = None
        row.last_successful_sync_at = None
        if not body.read_enabled:
            db.query(HealthDay).filter_by(user_id=user_id, source=body.source).delete(synchronize_session=False)
            prefs = db.get(HealthPreference, user_id)
            if prefs and prefs.primary_source == body.source:
                prefs.primary_source = None
                prefs.coach_enabled = False
        db.commit()
        return _source_out(row)
    except Exception:
        db.rollback()
        raise


def update_preferences(db: Session, user_id: str, body: HealthPreferencesRequest) -> dict:
    if body.primary_source or body.coach_enabled:
        _require_collection()
    if body.coach_enabled and not body.primary_source:
        raise HealthInvalid("Choose a primary source before enabling Coach context")
    if body.primary_source:
        source = db.query(HealthSource).filter_by(user_id=user_id, source=body.primary_source, enabled=True, read_enabled=True).with_for_update().one_or_none()
        if source is None:
            raise HealthInvalid("Primary source must be connected for reading")
    try:
        db.execute(insert(HealthPreference).values(user_id=user_id).on_conflict_do_nothing(index_elements=["user_id"]))
        prefs = db.query(HealthPreference).filter_by(user_id=user_id).with_for_update().one()
        prefs.primary_source = body.primary_source
        prefs.coach_enabled = body.coach_enabled
        db.commit()
        return read_health(db, user_id)
    except Exception:
        db.rollback()
        raise


def upsert_days(db: Session, user_id: str, body: HealthDaysRequest) -> dict:
    _require_collection()
    now = datetime.now(timezone.utc)
    try:
        source = db.query(HealthSource).filter_by(user_id=user_id, source=body.source).with_for_update().one_or_none()
        if source is None or not source.enabled or not source.read_enabled or source.connection_revision != body.connection_revision:
            raise HealthConflict("Health connection changed; reconnect and sync again")
        if any(day.local_date < _cutoff(now, day.time_zone) or day.local_date > _local_today(now, day.time_zone) for day in body.days):
            raise HealthInvalid("Daily summaries must be within the rolling 90-day window")
        if body.replace_window:
            zone = body.replace_window.time_zone
            if body.replace_window.start_date < _cutoff(now, zone) or body.replace_window.end_date > _local_today(now, zone):
                raise HealthInvalid("Replacement window must be within the rolling 90-day window")
            db.query(HealthDay).filter(HealthDay.user_id == user_id, HealthDay.source == body.source, HealthDay.local_date >= body.replace_window.start_date, HealthDay.local_date <= body.replace_window.end_date).delete(synchronize_session=False)
        unique_days = {day.local_date: day for day in body.days}
        for day in unique_days.values():
            db.query(HealthDay).filter(HealthDay.user_id == user_id, HealthDay.source == body.source, HealthDay.local_date == day.local_date, HealthDay.time_zone != day.time_zone).delete(synchronize_session=False)
            statement = insert(HealthDay).values(user_id=user_id, source=body.source, local_date=day.local_date, time_zone=day.time_zone, steps=day.steps, asleep_minutes=day.asleep_minutes)
            db.execute(statement.on_conflict_do_update(index_elements=["user_id", "source", "local_date", "time_zone"], set_={"steps": day.steps, "asleep_minutes": day.asleep_minutes, "updated_at": now}))
        source.last_successful_sync_at = now
        _purge(db, user_id, now)
        db.commit()
        return {"source": body.source, "connectionRevision": body.connection_revision, "acceptedDays": len(unique_days), "lastSuccessfulSyncAt": now}
    except Exception:
        db.rollback()
        raise


def disconnect_source(db: Session, user_id: str, source_name: str) -> None:
    now = datetime.now(timezone.utc)
    try:
        row = db.query(HealthSource).filter_by(user_id=user_id, source=source_name).with_for_update().one_or_none()
        if row is None or not row.enabled:
            return
        row.connection_revision += 1
        row.enabled = False
        row.read_enabled = False
        row.export_enabled = False
        row.disconnected_at = now
        row.last_successful_sync_at = None
        db.query(HealthDay).filter_by(user_id=user_id, source=source_name).delete(synchronize_session=False)
        prefs = db.get(HealthPreference, user_id)
        if prefs and prefs.primary_source == source_name:
            prefs.primary_source = None
            prefs.coach_enabled = False
        db.query(CoachFeedItem).filter(CoachFeedItem.user_id == user_id, CoachFeedItem.kind == "health_context").delete(synchronize_session=False)
        db.commit()
    except Exception:
        db.rollback()
        raise
