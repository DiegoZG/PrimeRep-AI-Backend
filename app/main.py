from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from slowapi import _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded
from app.api.v1.router import api_router
from app.core.settings import settings
from app.core.rate_limit import limiter

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

# Versioned API
app.include_router(api_router, prefix="/v1")

@app.get("/health")
def health_check():
    return {
        "status": "ok",
        "service": "primerep-api",
        "version": "1.0.0",
    }
