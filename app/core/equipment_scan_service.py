import asyncio
import hashlib
import hmac
import time
import threading

from starlette.concurrency import run_in_threadpool
import uuid
from datetime import datetime, timedelta, timezone

from sqlalchemy import func

from app.core import ai_budget_service as budget
from app.core.equipment_scan_catalog import SQLCatalogRepository, resolve
from app.core.equipment_scan_provider import OpenAIRecognitionAdapter, ScanProviderError, normalize_image, settings_version
from app.core.feature_flag_service import flag_environment, get_feature_flags
from app.core.settings import settings
from app.core.subscription_service import get_subscription
from app.models.equipment_scan import EquipmentScanAttempt, EquipmentScanQuota
from app.models.subscription import SubscriptionTransitionGrant
from app.models.user import User
from app.schemas.equipment_scan import ScanAttemptStatus, ScanErrorDetail, ScanResult

MESSAGES = {
    "scan_authentication_required": (401, "Sign in to use equipment discovery."),
    "scan_disabled": (403, "Equipment scanning is not available. You can still search manually."),
    "scan_access_required": (403, "A verified subscription, trial or valid access grant is required for scanning."),
    "scan_access_unknown": (503, "Subscription access could not be checked. Try again or use manual search."),
    "scan_in_progress": (409, "Another equipment scan is still processing. Check its result or use manual search."),
    "scan_result_stale": (409, "This scan result is no longer current. Search manually or explicitly take a new photo."),
    "scan_idempotency_conflict": (409, "That attempt key was already used for a different photo."),
    "scan_attempt_not_found": (404, "This equipment scan could not be found."),
    "scan_image_too_large": (413, "Choose one photo of 5 MiB or less."),
    "scan_media_type_unsupported": (415, "Choose one JPEG or PNG photo."),
    "scan_image_invalid": (422, "Choose one valid, single-frame photo of one machine."),
    "scan_image_not_allowed": (422, "This photo cannot be used. Choose a different machine photo or search manually."),
    "scan_quota_exceeded": (429, "The scanning attempt limit has been reached. Try later or search manually."),
    "scan_budget_exceeded": (429, "Scanning is temporarily at its application usage limit. Search manually or try later."),
    "scan_provider_unavailable": (503, "Equipment scanning is temporarily unavailable. Search manually or try later."),
    "scan_provider_timeout": (504, "The scan did not finish in time. Check its result or search manually."),
    "scan_provider_outcome_unknown": (503, "The external scan outcome is unknown. Check its result or search manually."),
    "scan_temporarily_unavailable": (503, "Equipment scanning is temporarily unavailable. Search manually or try later."),
    "scan_invalid_request": (422, "The equipment request is invalid. Refresh and try again."),
    "scan_not_found": (404, "This equipment or exercise is not available."),
}


class ScanError(Exception):
    def __init__(self, code, *, attempt_key=None, retry_after=None, request_id=None, status=None):
        self.status = status or MESSAGES[code][0]
        self.detail = ScanErrorDetail(code=code, detail=MESSAGES[code][1], request_id=request_id or uuid.uuid4(), retry_after_seconds=retry_after, attempt_key=attempt_key)


def utc(value):
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value.astimezone(timezone.utc)


def require_scan_access(db, user_id, *, now=None):
    now = now or datetime.now(timezone.utc)
    user = db.get(User, user_id, populate_existing=True)
    if not user:
        raise ScanError("scan_attempt_not_found")
    flags = get_feature_flags(db, user_id)["flags"]
    if not flags.get("equipment_scanning_enabled", False):
        raise ScanError("scan_disabled")
    snapshot = get_subscription(db, user_id, now=now)
    grant = db.get(SubscriptionTransitionGrant, (flag_environment(), user_id), populate_existing=True)
    if snapshot["has_premium"] or (grant and utc(grant.expires_at) > now):
        return
    validity = max((utc(value) for value in (snapshot["expires_at"], snapshot["grace_expires_at"]) if value), default=None)
    verified = utc(snapshot["verified_at"]) if snapshot["verified_at"] else None
    known_absence = snapshot["verification_status"] == "verified" and verified and (
        snapshot["billing_status"] == "none" or (validity and validity <= now and verified >= validity)
    )
    if known_absence:
        raise ScanError("scan_access_required")
    raise ScanError("scan_access_unknown", retry_after=5)


def _fingerprint(image, user_id, environment):
    scope = (environment + ":" + user_id + ":" + settings_version() + ":").encode()
    return hmac.new(settings.JWT_REFRESH_SECRET.encode(), scope + image, hashlib.sha256).hexdigest()


def _valid_receipt(row, versions, now):
    return utc(row.expires_at) > now and row.versions == versions.model_dump() and row.settings_version == settings_version()


def _lookup(db, user_id, key):
    return db.query(EquipmentScanAttempt).filter_by(user_id=user_id, environment=flag_environment(), attempt_key=str(key)).first()


def _processing_error(row):
    if row.status == "unknown":
        remaining = max(1, min(900, int((utc(row.expires_at) - datetime.now(timezone.utc)).total_seconds())))
        return ScanError("scan_provider_outcome_unknown", attempt_key=row.attempt_key, retry_after=remaining, request_id=row.id)
    return ScanError("scan_in_progress", attempt_key=row.attempt_key, retry_after=2, request_id=row.id)


def _admit(db, user_id, key, fingerprint, versions, *, now):
    environment = flag_environment()
    db.commit()
    with db.begin():
        budget.budget_lock(db, environment)
        if not db.get(User, user_id):
            raise ScanError("scan_attempt_not_found")
        require_scan_access(db, user_id, now=now)
        db.query(EquipmentScanAttempt).filter(EquipmentScanAttempt.environment == environment, EquipmentScanAttempt.status == "processing", EquipmentScanAttempt.deadline_at <= now).update({EquipmentScanAttempt.status: "unknown"}, synchronize_session=False)
        previous = _lookup(db, user_id, key)
        if previous:
            if previous.fingerprint != fingerprint:
                raise ScanError("scan_idempotency_conflict", request_id=previous.id)
            if not _valid_receipt(previous, versions, now):
                raise ScanError("scan_result_stale", request_id=previous.id)
            if previous.status == "completed":
                return previous, None
            if previous.status in {"processing", "unknown"}:
                raise _processing_error(previous)
            raise ScanError(previous.error_code or "scan_provider_unavailable", attempt_key=previous.attempt_key, request_id=previous.id)
        active = db.query(EquipmentScanAttempt).filter(EquipmentScanAttempt.environment == environment, EquipmentScanAttempt.user_id == user_id, EquipmentScanAttempt.status.in_(["processing", "unknown"]), EquipmentScanAttempt.expires_at > now).first()
        if active:
            raise _processing_error(active)
        repeated = db.query(EquipmentScanAttempt).filter(EquipmentScanAttempt.environment == environment, EquipmentScanAttempt.user_id == user_id, EquipmentScanAttempt.fingerprint == fingerprint, EquipmentScanAttempt.status == "completed", EquipmentScanAttempt.expires_at > now).order_by(EquipmentScanAttempt.created_at).first()
        if repeated and _valid_receipt(repeated, versions, now):
            alias = EquipmentScanAttempt(id=str(uuid.uuid4()), user_id=user_id, environment=environment, attempt_key=str(key), fingerprint=fingerprint, versions=repeated.versions, settings_version=repeated.settings_version, status="completed", result_json=repeated.result_json, created_at=now, expires_at=repeated.expires_at, deadline_at=now, finished_at=now)
            db.add(alias)
            return alias, None
        concurrency = db.query(func.count()).select_from(EquipmentScanAttempt).filter(EquipmentScanAttempt.environment == environment, EquipmentScanAttempt.status == "processing", EquipmentScanAttempt.deadline_at > now).scalar()
        if not 1 <= settings.EQUIPMENT_SCAN_GLOBAL_CONCURRENCY <= 100 or concurrency >= settings.EQUIPMENT_SCAN_GLOBAL_CONCURRENCY:
            raise ScanError("scan_quota_exceeded", retry_after=15)
        recent = db.query(func.count()).select_from(EquipmentScanAttempt).filter(EquipmentScanAttempt.environment == environment, EquipmentScanAttempt.user_id == user_id, EquipmentScanAttempt.reservation_id.is_not(None), EquipmentScanAttempt.created_at > now - timedelta(minutes=1)).scalar()
        day = now.strftime("%Y-%m-%d")
        quota = db.get(EquipmentScanQuota, (user_id, environment, day))
        daily_count = quota.attempts if quota else 0
        if not 1 <= settings.EQUIPMENT_SCAN_ATTEMPTS_PER_MINUTE <= 100 or not 1 <= settings.EQUIPMENT_SCAN_ATTEMPTS_PER_DAY <= 10000:
            raise ScanError("scan_temporarily_unavailable")
        if recent >= settings.EQUIPMENT_SCAN_ATTEMPTS_PER_MINUTE or daily_count >= settings.EQUIPMENT_SCAN_ATTEMPTS_PER_DAY:
            raise ScanError("scan_quota_exceeded", retry_after=60 if recent >= settings.EQUIPMENT_SCAN_ATTEMPTS_PER_MINUTE else 86400)
        try:
            reservation = budget.reserve("equipment_scan", settings.OPENAI_VISION_MODEL, 1_050_000, 768, db=db, now=now)
        except budget.AIBudgetUnavailable:
            raise ScanError("scan_budget_exceeded", retry_after=60) from None
        row = EquipmentScanAttempt(id=str(uuid.uuid4()), user_id=user_id, environment=environment, attempt_key=str(key), fingerprint=fingerprint, versions=versions.model_dump(), settings_version=settings_version(), status="processing", reservation_id=reservation.id, created_at=now, expires_at=now + timedelta(minutes=15), deadline_at=now + timedelta(seconds=min(25, settings.EQUIPMENT_SCAN_TIMEOUT_SECONDS)))
        db.add(row)
        if quota:
            quota.attempts += 1
        else:
            db.add(EquipmentScanQuota(user_id=user_id, environment=environment, period=day, attempts=1, expires_at=now + timedelta(days=2)))
        db.flush()
        return row, reservation


def _finish_error(db, identifier, code, *, unknown=False):
    db.rollback()
    row = db.get(EquipmentScanAttempt, identifier)
    if row:
        row.status, row.error_code, row.finished_at = "unknown" if unknown else "failed", code, datetime.now(timezone.utc)
        db.commit()


_NORMALIZATION_SLOTS = threading.BoundedSemaphore(2)


def _normalize_worker(raw, content_type):
    if not _NORMALIZATION_SLOTS.acquire(blocking=False):
        raise ScanProviderError("scan_temporarily_unavailable")
    try:
        return normalize_image(raw, content_type)
    finally:
        _NORMALIZATION_SLOTS.release()


async def scan(db, user_id, key, raw, content_type, *, adapter=None):
    require_scan_access(db, user_id)
    db.commit()
    if adapter is None and not settings.OPENAI_API_KEY:
        raise ScanError("scan_provider_unavailable")
    if not 1 <= settings.EQUIPMENT_SCAN_TIMEOUT_SECONDS <= 25 or settings.OPENAI_VISION_REASONING not in {"none", "low"}:
        raise ScanError("scan_temporarily_unavailable")
    try:
        image = await run_in_threadpool(_normalize_worker, raw, content_type)
    except ScanProviderError as error:
        raise ScanError(error.code, status=error.status) from None
    repository = SQLCatalogRepository(db)
    versions, cohort = repository.versions(), repository.pilot()
    row, reservation = _admit(db, user_id, key, _fingerprint(image, user_id, flag_environment()), versions, now=datetime.now(timezone.utc))
    identifier = row.id
    if reservation is None:
        return ScanResult.model_validate(row.result_json)
    db.commit()
    adapter = adapter or OpenAIRecognitionAdapter()
    recognition_started = False
    started = time.monotonic()
    try:
        def remaining():
            value = settings.EQUIPMENT_SCAN_TIMEOUT_SECONDS - (time.monotonic() - started)
            if value <= 0:
                raise ScanProviderError("scan_provider_timeout", 504)
            return value
        await adapter.moderate(image, min(8.0, remaining()))
        require_scan_access(db, user_id)
        db.commit()
        recognition_started = True
        observed, usage = await adapter.recognize(image, cohort, remaining())
        budget.settle(reservation, usage, bind=db.get_bind())
        require_scan_access(db, user_id)
        repository = SQLCatalogRepository(db)
        latest = repository.versions()
        if observed.subject == "single_equipment":
            candidates = resolve(observed.observations, repository)
            reason = None if candidates else "unsupported_equipment"
        else:
            candidates, reason = [], observed.subject
        final_versions = repository.versions()
        if latest != final_versions:
            raise ScanError("scan_result_stale", attempt_key=key)
        result = ScanResult(scan_id=identifier, status="candidates" if candidates else "no_match", requires_confirmation=bool(candidates), versions=final_versions, candidates=candidates, reason=reason)
        current = db.get(EquipmentScanAttempt, identifier)
        if current is None or not db.get(User, user_id):
            raise ScanError("scan_attempt_not_found")
        if current.settings_version != settings_version():
            raise ScanError("scan_result_stale", attempt_key=key)
        current.result_json, current.versions, current.status, current.finished_at = result.model_dump(mode="json"), final_versions.model_dump(), "completed", datetime.now(timezone.utc)
        db.commit()
        return result
    except BaseException as error:
        budget.settle(reservation, getattr(error, "usage", None), known_zero=not recognition_started, bind=db.get_bind())
        code = error.detail.code if isinstance(error, ScanError) else error.code if isinstance(error, ScanProviderError) else "scan_provider_outcome_unknown" if recognition_started else "scan_provider_unavailable"
        unknown = recognition_started and code in {"scan_provider_outcome_unknown", "scan_provider_timeout", "scan_provider_unavailable"}
        _finish_error(db, identifier, code, unknown=unknown)
        if isinstance(error, asyncio.CancelledError):
            raise
        raise ScanError(code, attempt_key=key, retry_after=900 if unknown else None, request_id=identifier, status=error.status if isinstance(error, (ScanError, ScanProviderError)) else None) from None
    finally:
        image = b""


def attempt_status(db, user_id, key):
    require_scan_access(db, user_id)
    row = _lookup(db, user_id, key)
    if not row:
        raise ScanError("scan_attempt_not_found")
    if not _valid_receipt(row, SQLCatalogRepository(db).versions(), datetime.now(timezone.utc)):
        raise ScanError("scan_result_stale", request_id=row.id)
    if row.status == "processing" and utc(row.deadline_at) <= datetime.now(timezone.utc):
        row.status, row.error_code = "unknown", "scan_provider_outcome_unknown"
        db.commit()
    result = ScanResult.model_validate(row.result_json) if row.status == "completed" else None
    error = ScanError(row.error_code or "scan_provider_outcome_unknown", attempt_key=key, request_id=row.id).detail if row.status in {"failed", "unknown"} else None
    return ScanAttemptStatus(attempt_key=key, status=row.status, retry_after_seconds=2 if row.status == "processing" else None, result=result, error=error)


def export_scan_attempts(db, user_id):
    now = datetime.now(timezone.utc)
    for row in db.query(EquipmentScanAttempt).filter(EquipmentScanAttempt.user_id == user_id, EquipmentScanAttempt.expires_at > now).order_by(EquipmentScanAttempt.created_at):
        yield {"attempt_key": row.attempt_key, "environment": row.environment, "status": row.status, "created_at": row.created_at.isoformat(), "expires_at": row.expires_at.isoformat(), "finished_at": row.finished_at.isoformat() if row.finished_at else None, "result": row.result_json, "error_code": row.error_code}


def prune(db, *, now=None):
    now = now or datetime.now(timezone.utc)
    attempts = db.query(EquipmentScanAttempt).filter(EquipmentScanAttempt.environment == flag_environment(), EquipmentScanAttempt.expires_at <= now).delete(synchronize_session=False)
    quotas = db.query(EquipmentScanQuota).filter(EquipmentScanQuota.environment == flag_environment(), EquipmentScanQuota.expires_at <= now).delete(synchronize_session=False)
    db.commit()
    return attempts, quotas
