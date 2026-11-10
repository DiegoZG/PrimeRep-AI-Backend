from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import ValidationError
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session
from starlette.concurrency import run_in_threadpool

from app.core.database import get_db
from app.core.rate_limit import check_workout_diagnostics
from app.core.security.deps import get_current_user
from app.core.workout_diagnostics_service import ingest_diagnostics
from app.models.user import User
from app.schemas.workout_diagnostics import WorkoutDiagnosticBatch, WorkoutDiagnosticReceipt

router = APIRouter(tags=["workout-diagnostics"])
MAX_BODY_BYTES = 32 * 1024


@router.post("/diagnostics/workout-sync", response_model=WorkoutDiagnosticReceipt)
async def record_diagnostics(request: Request, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    await run_in_threadpool(check_workout_diagnostics, str(current_user.id))
    if request.query_params:
        raise HTTPException(422, "Diagnostics do not accept query selectors")
    raw = bytearray()
    async for chunk in request.stream():
        if len(raw) + len(chunk) > MAX_BODY_BYTES:
            raise HTTPException(413, "Diagnostic batch is too large")
        raw.extend(chunk)
    try:
        batch = WorkoutDiagnosticBatch.model_validate_json(bytes(raw))
    except ValidationError:
        raise HTTPException(422, "Invalid diagnostic batch") from None

    def store():
        try:
            receipt = ingest_diagnostics(db, str(current_user.id), batch)
            db.commit()
            return receipt
        except SQLAlchemyError:
            db.rollback()
            raise HTTPException(503, "Diagnostics are temporarily unavailable") from None

    return await run_in_threadpool(store)
