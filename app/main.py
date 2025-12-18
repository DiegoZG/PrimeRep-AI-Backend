from fastapi import FastAPI
from app.api.v1.router import api_router

app = FastAPI(
    title="PrimeRep API",
    version="1.0.0",
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
