from datetime import date, datetime
from typing import Literal, Optional

from pydantic import BaseModel


class AchievementOut(BaseModel):
    id: str
    title: str
    description: str
    kind: Literal["workouts", "consistency"]
    threshold: int
    progress: int
    earned_at: Optional[datetime]


class WeeklyActivityOut(BaseModel):
    week_start: date
    completed_workouts: int
    is_current: bool


class AchievementsOut(BaseModel):
    total_completed: int
    current_active_weeks: int
    best_active_weeks: int
    time_zone: str
    weekly_activity: list[WeeklyActivityOut]
    items: list[AchievementOut]
