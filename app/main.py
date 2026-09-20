from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from slowapi import _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded
from app.api.v1.router import api_router
from app.core.settings import settings
from app.core.rate_limit import limiter
import logging
from time import perf_counter

app = FastAPI(
    title="PrimeRep API",
    version="1.0.0",
)
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
async def catalog_observability(request: Request, call_next):
    path = request.url.path
    catalog = path in {"/v1/exercises", "/v1/equipment", "/v1/explore/search"}
    if not catalog:
        return await call_next(request)
    started = perf_counter()
    status = 500
    try:
        response = await call_next(request)
        status = response.status_code
        return response
    finally:
        logging.getLogger("primerep.catalog").info("catalog_request", extra={
            "catalog_endpoint": path, "status": status,
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
    }
