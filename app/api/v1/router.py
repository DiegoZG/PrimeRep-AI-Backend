from fastapi import APIRouter, Depends
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.api.v1.auth.router import router as auth_router
from app.api.v1.equipment.router import router as equipment_router
from app.api.v1.exercises.favorites_router import router as exercise_favorites_router
from app.api.v1.exercises.router import router as exercises_router
from app.api.v1.onboarding.router import router as onboarding_router
from app.api.v1.users.router import router as users_router
from app.core.database import get_db

api_router = APIRouter()
api_router.include_router(auth_router)
api_router.include_router(users_router)
api_router.include_router(onboarding_router)
api_router.include_router(equipment_router, prefix="/equipment", tags=["equipment"])
api_router.include_router(
    exercise_favorites_router,
    prefix="/exercises",
    tags=["exercise-favorites"],
)
api_router.include_router(exercises_router, prefix="/exercises", tags=["exercises"])


@api_router.get("/_ping")
def ping():
    return {"status": "ok", "scope": "v1"}


@api_router.get("/_db_ping")
def db_ping(db: Session = Depends(get_db)):
    result = db.execute(text("SELECT 1")).scalar()
    return {"db": "ok", "result": result}
