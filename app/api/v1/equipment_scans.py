import asyncio
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from fastapi.routing import APIRoute
from limits import parse
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.equipment_scan_catalog import CatalogError, SQLCatalogRepository
from app.core.equipment_scan_provider import MAX_RECEIVED_BYTES
from app.core.equipment_scan_service import ScanError, attempt_status, scan
from app.core.rate_limit import user_limiter
from app.core.security.deps import get_current_user
from app.models.user import User
from app.schemas.equipment_scan import EquipmentSearchPage, ExerciseGuidance, ExerciseOptionsPage, ScanAttemptStatus, ScanErrorResponse, ScanResult


class ScanRoute(APIRoute):
    def get_route_handler(self):
        original = super().get_route_handler()

        async def handle(request):
            try:
                return await original(request)
            except ScanError as error:
                caught = error
            except CatalogError as error:
                caught = ScanError(error.code, status=error.status)
            except RequestValidationError:
                caught = ScanError("scan_invalid_request")
            except HTTPException as error:
                caught = ScanError("scan_authentication_required" if error.status_code == 401 else "scan_invalid_request", status=error.status_code)
            except Exception:
                caught = ScanError("scan_temporarily_unavailable")
            headers = {"Retry-After": str(caught.detail.retry_after_seconds)} if caught.detail.retry_after_seconds else None
            return JSONResponse(status_code=caught.status, content=ScanErrorResponse(error=caught.detail).model_dump(mode="json"), headers=headers)
        return handle


router = APIRouter(route_class=ScanRoute, tags=["equipment discovery"])
UPLOAD_TIMEOUT_SECONDS = 8.0


@router.post("/equipment-scans", response_model=ScanResult)
async def create_equipment_scan(request: Request, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    if request.query_params:
        raise ScanError("scan_invalid_request")
    if not user_limiter.limiter.hit(parse("10/minute"), "equipment-scan-upload", str(user.id)):
        raise ScanError("scan_quota_exceeded", retry_after=60)
    try:
        key = UUID(request.headers.get("Idempotency-Key", ""))
    except ValueError:
        raise ScanError("scan_invalid_request") from None
    content_type = request.headers.get("Content-Type", "").split(";", 1)[0].strip().lower()
    if content_type not in {"image/jpeg", "image/png"}:
        raise ScanError("scan_media_type_unsupported")
    # Authorize before buffering/decoding an upload; scan() rechecks before admission.
    from app.core.equipment_scan_service import require_scan_access
    owner = str(user.id)
    require_scan_access(db, owner)
    db.commit()
    received = bytearray()
    async def read_body():
        async for chunk in request.stream():
            if len(received) + len(chunk) > MAX_RECEIVED_BYTES:
                raise ScanError("scan_image_too_large")
            received.extend(chunk)
    try:
        try:
            await asyncio.wait_for(read_body(), UPLOAD_TIMEOUT_SECONDS)
        except asyncio.TimeoutError:
            raise ScanError("scan_invalid_request") from None
        return await scan(db, owner, key, bytes(received), content_type)
    finally:
        received.clear()


@router.get("/equipment-scans/attempts/{attempt_key}", response_model=ScanAttemptStatus)
def get_scan_attempt(attempt_key: UUID, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    return attempt_status(db, str(user.id), attempt_key)


@router.get("/equipment/search", response_model=EquipmentSearchPage)
def search_equipment(q: str = Query("", max_length=100), limit: int = Query(20, ge=1, le=20), cursor: str = Query(None, max_length=2048), user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    return SQLCatalogRepository(db).search(q, limit, cursor)


@router.get("/equipment/{equipment_id}/exercise-options", response_model=ExerciseOptionsPage)
def equipment_exercise_options(equipment_id: str, limit: int = Query(20, ge=1, le=20), cursor: str = Query(None, max_length=2048), available_equipment_ids: str = Query("", max_length=4096), user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    available = available_equipment_ids.split(",") if available_equipment_ids else []
    if len(available) > 32 or len(set(available)) != len(available) or any(not value or len(value) > 120 for value in available) or len(equipment_id) > 120:
        raise ScanError("scan_invalid_request")
    return SQLCatalogRepository(db).options(equipment_id, available, limit, cursor)


@router.get("/exercises/{exercise_id}/guidance", response_model=ExerciseGuidance)
def exercise_guidance(exercise_id: str, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    if len(exercise_id) > 120:
        raise ScanError("scan_invalid_request")
    return SQLCatalogRepository(db).guidance(exercise_id)
