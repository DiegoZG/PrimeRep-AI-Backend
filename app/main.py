from fastapi import FastAPI, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from slowapi import _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded
from app.api.v1.router import api_router
from app.core.settings import settings
from app.core.rate_limit import limiter
from app.core.rate_limit import force_storage
from app.core.database import engine
from sqlalchemy import text
import logging
from time import perf_counter
from uuid import uuid4

app = FastAPI(
    title="PrimeRep API",
    version="1.0.0",
)

from fastapi.responses import JSONResponse
from app.core.workout_planning import WorkoutTimeBudgetTooShort, WorkoutPlanningLocked

@app.exception_handler(WorkoutTimeBudgetTooShort)
async def planning_budget_error(request, error):
    return JSONResponse(status_code=409, content={"detail": {"code": "workout_time_budget_too_short", "message": str(error), "minimumUsefulMinutes": error.minimum_useful_minutes}})

@app.exception_handler(WorkoutPlanningLocked)
async def planning_locked_error(request, error):
    return JSONResponse(status_code=409, content={"detail": {"code": "workout_planning_locked", "message": str(error)}})

app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.CORS_ALLOWED_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.middleware("http")
async def request_observability(request: Request, call_next):
    request_id = str(uuid4())
    started = perf_counter()
    status = 500
    try:
        response = await call_next(request)
        status = response.status_code
        response.headers["X-Request-ID"] = request_id
        return response
    finally:
        route = request.scope.get("route")
        endpoint = getattr(route, "path", "unmatched")
        logging.getLogger("primerep.request").info("api_request", extra={
            "endpoint": endpoint,
            "method": request.method,
            "request_id": request_id,
            "release_version": settings.RELEASE_VERSION,
            "status": status,
            "duration_ms": round((perf_counter() - started) * 1000, 2),
        })

# Versioned API
app.include_router(api_router, prefix="/v1")

@app.get("/health")
def health_check():
    return {
        "status": "ok",
        "service": "primerep-api",
        "version": "1.0.0",
        "release": settings.RELEASE_VERSION,
    }


@app.get("/ready", include_in_schema=False)
def readiness_check(response: Response):
    try:
        with engine.connect() as connection:
            connection.execute(text("SELECT 1"))
        if not force_storage.check():
            raise RuntimeError("Rate-limit storage unavailable")
    except Exception:
        response.status_code = 503
        return {"status": "unavailable"}
    return {"status": "ready"}
