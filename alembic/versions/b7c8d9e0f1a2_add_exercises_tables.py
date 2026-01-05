"""
add exercises tables

Revision ID: b7c8d9e0f1a2
Revises: a1b2c3d4e5f6
Create Date: 2025-01-25 12:00:00.000000

"""
from typing import Sequence, Union
import json

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


# revision identifiers, used by Alembic.
revision: str = "b7c8d9e0f1a2"
down_revision: Union[str, Sequence[str], None] = "a1b2c3d4e5f6"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


# Frozen snapshot of initial exercise seed data.
SEED_EXERCISES = [
    # ======================
    # CHEST
    # ======================
    {
        "id": "bench_press",
        "name": "Bench Press",
        "exercise_type": "strength",
        "primary_muscle": "chest",
        "secondary_muscles": ["triceps", "shoulders"],
        "required_equipment_ids": ["flat_bench", "olympic_barbell", "plates"],
        "demo_video_url": None,
        "image_url": None,
        "sort_order": 10,
    },
    {
        "id": "incline_dumbbell_press",
        "name": "Incline Dumbbell Press",
        "exercise_type": "strength",
        "primary_muscle": "chest",
        "secondary_muscles": ["shoulders", "triceps"],
        "required_equipment_ids": ["incline_bench", "dumbbells"],
        "demo_video_url": None,
        "image_url": None,
        "sort_order": 20,
    },
    {
        "id": "push_up",
        "name": "Push-Up",
        "exercise_type": "bodyweight",
        "primary_muscle": "chest",
        "secondary_muscles": ["triceps", "shoulders", "abs"],
        "required_equipment_ids": [],
        "demo_video_url": None,
        "image_url": None,
        "sort_order": 30,
    },
    {
        "id": "chest_fly_machine",
        "name": "Chest Fly Machine",
        "exercise_type": "strength",
        "primary_muscle": "chest",
        "secondary_muscles": ["shoulders"],
        "required_equipment_ids": ["fly_machine"],
        "demo_video_url": None,
        "image_url": None,
        "sort_order": 40,
    },
    {
        "id": "cable_crossover",
        "name": "Cable Crossover",
        "exercise_type": "accessory",
        "primary_muscle": "chest",
        "secondary_muscles": ["shoulders"],
        "required_equipment_ids": ["crossover_cable"],
        "demo_video_url": None,
        "image_url": None,
        "sort_order": 50,
    },
    # ======================
    # SHOULDERS
    # ======================
    {
        "id": "overhead_press",
        "name": "Overhead Press",
        "exercise_type": "strength",
        "primary_muscle": "shoulders",
        "secondary_muscles": ["triceps", "back"],
        "required_equipment_ids": ["olympic_barbell", "plates"],
        "demo_video_url": None,
        "image_url": None,
        "sort_order": 10,
    },
    {
        "id": "dumbbell_shoulder_press",
        "name": "Dumbbell Shoulder Press",
        "exercise_type": "strength",
        "primary_muscle": "shoulders",
        "secondary_muscles": ["triceps"],
        "required_equipment_ids": ["dumbbells", "vertical_bench"],
        "demo_video_url": None,
        "image_url": None,
        "sort_order": 20,
    },
    {
        "id": "lateral_raise",
        "name": "Dumbbell Lateral Raise",
        "exercise_type": "accessory",
        "primary_muscle": "shoulders",
        "secondary_muscles": [],
        "required_equipment_ids": ["dumbbells"],
        "demo_video_url": None,
        "image_url": None,
        "sort_order": 30,
    },
    {
        "id": "front_raise",
        "name": "Dumbbell Front Raise",
        "exercise_type": "accessory",
        "primary_muscle": "shoulders",
        "secondary_muscles": [],
        "required_equipment_ids": ["dumbbells"],
        "demo_video_url": None,
        "image_url": None,
        "sort_order": 40,
    },
    {
        "id": "reverse_fly",
        "name": "Reverse Fly",
        "exercise_type": "accessory",
        "primary_muscle": "shoulders",
        "secondary_muscles": ["back"],
        "required_equipment_ids": ["dumbbells"],
        "demo_video_url": None,
        "image_url": None,
        "sort_order": 50,
    },
    # ======================
    # BACK
    # ======================
    {
        "id": "deadlift",
        "name": "Deadlift",
        "exercise_type": "strength",
        "primary_muscle": "back",
        "secondary_muscles": ["glutes", "hamstrings"],
        "required_equipment_ids": ["olympic_barbell", "plates"],
        "demo_video_url": None,
        "image_url": None,
        "sort_order": 10,
    },
    {
        "id": "romanian_deadlift",
        "name": "Romanian Deadlift",
        "exercise_type": "strength",
        "primary_muscle": "hamstrings",
        "secondary_muscles": ["glutes", "back"],
        "required_equipment_ids": ["olympic_barbell", "plates"],
        "demo_video_url": None,
        "image_url": None,
        "sort_order": 20,
    },
    {
        "id": "pull_up",
        "name": "Pull-Up",
        "exercise_type": "bodyweight",
        "primary_muscle": "back",
        "secondary_muscles": ["biceps", "shoulders"],
        "required_equipment_ids": ["pull_up_bar"],
        "demo_video_url": None,
        "image_url": None,
        "sort_order": 30,
    },
    {
        "id": "lat_pulldown",
        "name": "Lat Pulldown",
        "exercise_type": "strength",
        "primary_muscle": "back",
        "secondary_muscles": ["biceps"],
        "required_equipment_ids": ["lat_pulldown_cable"],
        "demo_video_url": None,
        "image_url": None,
        "sort_order": 40,
    },
    {
        "id": "seated_row",
        "name": "Seated Cable Row",
        "exercise_type": "strength",
        "primary_muscle": "back",
        "secondary_muscles": ["biceps"],
        "required_equipment_ids": ["row_cable"],
        "demo_video_url": None,
        "image_url": None,
        "sort_order": 50,
    },
    {
        "id": "t_bar_row",
        "name": "T-Bar Row",
        "exercise_type": "strength",
        "primary_muscle": "back",
        "secondary_muscles": ["biceps"],
        "required_equipment_ids": ["plate_loaded_t_bar", "plates"],
        "demo_video_url": None,
        "image_url": None,
        "sort_order": 60,
    },
    # ======================
    # BICEPS
    # ======================
    {
        "id": "barbell_curl",
        "name": "Barbell Curl",
        "exercise_type": "accessory",
        "primary_muscle": "biceps",
        "secondary_muscles": ["forearms"],
        "required_equipment_ids": ["olympic_barbell", "plates"],
        "demo_video_url": None,
        "image_url": None,
        "sort_order": 10,
    },
    {
        "id": "dumbbell_curl",
        "name": "Dumbbell Curl",
        "exercise_type": "accessory",
        "primary_muscle": "biceps",
        "secondary_muscles": ["forearms"],
        "required_equipment_ids": ["dumbbells"],
        "demo_video_url": None,
        "image_url": None,
        "sort_order": 20,
    },
    {
        "id": "preacher_curl",
        "name": "Preacher Curl",
        "exercise_type": "accessory",
        "primary_muscle": "biceps",
        "secondary_muscles": ["forearms"],
        "required_equipment_ids": ["preacher_curl_bench", "short_bar"],
        "demo_video_url": None,
        "image_url": None,
        "sort_order": 30,
    },
    # ======================
    # TRICEPS
    # ======================
    {
        "id": "tricep_pushdown",
        "name": "Tricep Pushdown",
        "exercise_type": "accessory",
        "primary_muscle": "triceps",
        "secondary_muscles": [],
        "required_equipment_ids": ["hi_lo_pull_cable"],
        "demo_video_url": None,
        "image_url": None,
        "sort_order": 10,
    },
    {
        "id": "skull_crushers",
        "name": "Skull Crushers",
        "exercise_type": "accessory",
        "primary_muscle": "triceps",
        "secondary_muscles": [],
        "required_equipment_ids": ["ez_bar", "flat_bench"],
        "demo_video_url": None,
        "image_url": None,
        "sort_order": 20,
    },
    {
        "id": "dip",
        "name": "Parallel Bar Dip",
        "exercise_type": "bodyweight",
        "primary_muscle": "triceps",
        "secondary_muscles": ["chest", "shoulders"],
        "required_equipment_ids": ["dip_bar"],
        "demo_video_url": None,
        "image_url": None,
        "sort_order": 30,
    },
    # ======================
    # LEGS
    # ======================
    {
        "id": "squat",
        "name": "Back Squat",
        "exercise_type": "strength",
        "primary_muscle": "quads",
        "secondary_muscles": ["glutes", "hamstrings"],
        "required_equipment_ids": ["squat_rack", "olympic_barbell", "plates"],
        "demo_video_url": None,
        "image_url": None,
        "sort_order": 10,
    },
    {
        "id": "front_squat",
        "name": "Front Squat",
        "exercise_type": "strength",
        "primary_muscle": "quads",
        "secondary_muscles": ["glutes", "hamstrings"],
        "required_equipment_ids": ["squat_rack", "olympic_barbell", "plates"],
        "demo_video_url": None,
        "image_url": None,
        "sort_order": 20,
    },
    {
        "id": "leg_press",
        "name": "Leg Press",
        "exercise_type": "strength",
        "primary_muscle": "quads",
        "secondary_muscles": ["glutes"],
        "required_equipment_ids": ["leg_press_machine_selectorized"],
        "demo_video_url": None,
        "image_url": None,
        "sort_order": 30,
    },
    {
        "id": "leg_extension",
        "name": "Leg Extension",
        "exercise_type": "strength",
        "primary_muscle": "quads",
        "secondary_muscles": [],
        "required_equipment_ids": ["leg_extension_machine"],
        "demo_video_url": None,
        "image_url": None,
        "sort_order": 40,
    },
    {
        "id": "leg_curl",
        "name": "Leg Curl",
        "exercise_type": "strength",
        "primary_muscle": "hamstrings",
        "secondary_muscles": [],
        "required_equipment_ids": ["leg_curl_machine"],
        "demo_video_url": None,
        "image_url": None,
        "sort_order": 50,
    },
    {
        "id": "walking_lunge",
        "name": "Walking Lunge",
        "exercise_type": "strength",
        "primary_muscle": "quads",
        "secondary_muscles": ["glutes", "hamstrings"],
        "required_equipment_ids": ["dumbbells"],
        "demo_video_url": None,
        "image_url": None,
        "sort_order": 60,
    },
    # ======================
    # GLUTES
    # ======================
    {
        "id": "hip_thrust",
        "name": "Hip Thrust",
        "exercise_type": "strength",
        "primary_muscle": "glutes",
        "secondary_muscles": ["hamstrings"],
        "required_equipment_ids": ["flat_bench", "olympic_barbell", "plates"],
        "demo_video_url": None,
        "image_url": None,
        "sort_order": 10,
    },
    {
        "id": "glute_bridge",
        "name": "Glute Bridge",
        "exercise_type": "bodyweight",
        "primary_muscle": "glutes",
        "secondary_muscles": ["hamstrings"],
        "required_equipment_ids": [],
        "demo_video_url": None,
        "image_url": None,
        "sort_order": 20,
    },
    # ======================
    # CALVES
    # ======================
    {
        "id": "standing_calf_raise",
        "name": "Standing Calf Raise",
        "exercise_type": "accessory",
        "primary_muscle": "calves",
        "secondary_muscles": [],
        "required_equipment_ids": ["calf_raise_machine"],
        "demo_video_url": None,
        "image_url": None,
        "sort_order": 10,
    },
    {
        "id": "seated_calf_raise",
        "name": "Seated Calf Raise",
        "exercise_type": "accessory",
        "primary_muscle": "calves",
        "secondary_muscles": [],
        "required_equipment_ids": ["plate_loaded_seated_calf", "plates"],
        "demo_video_url": None,
        "image_url": None,
        "sort_order": 20,
    },
    # ======================
    # ABS
    # ======================
    {
        "id": "plank",
        "name": "Plank",
        "exercise_type": "bodyweight",
        "primary_muscle": "abs",
        "secondary_muscles": ["shoulders"],
        "required_equipment_ids": [],
        "demo_video_url": None,
        "image_url": None,
        "sort_order": 10,
    },
    {
        "id": "crunch_machine",
        "name": "Crunch Machine",
        "exercise_type": "strength",
        "primary_muscle": "abs",
        "secondary_muscles": [],
        "required_equipment_ids": ["ab_crunch_machine"],
        "demo_video_url": None,
        "image_url": None,
        "sort_order": 20,
    },
    {
        "id": "hanging_leg_raise",
        "name": "Hanging Leg Raise",
        "exercise_type": "bodyweight",
        "primary_muscle": "abs",
        "secondary_muscles": ["quads"],
        "required_equipment_ids": ["pull_up_bar"],
        "demo_video_url": None,
        "image_url": None,
        "sort_order": 30,
    },
    {
        "id": "ab_wheel_rollout",
        "name": "Ab Wheel Roll-Out",
        "exercise_type": "strength",
        "primary_muscle": "abs",
        "secondary_muscles": ["shoulders"],
        "required_equipment_ids": ["ab_wheel"],
        "demo_video_url": None,
        "image_url": None,
        "sort_order": 40,
    },
    # ======================
    # CARDIO
    # ======================
    {
        "id": "rowing_machine",
        "name": "Rowing Machine",
        "exercise_type": "cardio",
        "primary_muscle": "cardio",
        "secondary_muscles": ["back", "full_body"],
        "required_equipment_ids": ["rowing_machine_cardio"],
        "demo_video_url": None,
        "image_url": None,
        "sort_order": 10,
    },
    {
        "id": "stationary_bike",
        "name": "Stationary Bike",
        "exercise_type": "cardio",
        "primary_muscle": "cardio",
        "secondary_muscles": ["quads", "hamstrings"],
        "required_equipment_ids": ["stationary_bike"],
        "demo_video_url": None,
        "image_url": None,
        "sort_order": 20,
    },
    {
        "id": "treadmill_run",
        "name": "Treadmill Run",
        "exercise_type": "cardio",
        "primary_muscle": "cardio",
        "secondary_muscles": ["full_body"],
        "required_equipment_ids": ["treadmill"],
        "demo_video_url": None,
        "image_url": None,
        "sort_order": 30,
    },
    {
        "id": "air_bike",
        "name": "Air Bike",
        "exercise_type": "cardio",
        "primary_muscle": "cardio",
        "secondary_muscles": ["full_body"],
        "required_equipment_ids": ["air_bike"],
        "demo_video_url": None,
        "image_url": None,
        "sort_order": 40,
    },
    {
        "id": "ski_erg",
        "name": "SkiErg",
        "exercise_type": "cardio",
        "primary_muscle": "cardio",
        "secondary_muscles": ["shoulders", "back"],
        "required_equipment_ids": ["ski_erg"],
        "demo_video_url": None,
        "image_url": None,
        "sort_order": 50,
    },
]


def upgrade() -> None:
    """Upgrade schema: create exercises and related tables, then seed data."""
    op.create_table(
        "exercises",
        sa.Column("id", sa.String(), primary_key=True, nullable=False),
        sa.Column("name", sa.String(), nullable=False),
        sa.Column("exercise_type", sa.String(), nullable=False),
        sa.Column("primary_muscle", sa.String(), nullable=False),
        sa.Column(
            "secondary_muscles",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
        sa.Column("demo_video_url", sa.String(), nullable=True),
        sa.Column("image_url", sa.String(), nullable=True),
        sa.Column(
            "is_active",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("true"),
        ),
        sa.Column(
            "source",
            sa.String(),
            nullable=False,
            server_default=sa.text("'seed'"),
        ),
        sa.Column(
            "owner_user_id",
            sa.String(),
            sa.ForeignKey("users.id"),
            nullable=True,
        ),
        sa.Column(
            "sort_order",
            sa.Integer(),
            nullable=False,
            server_default=sa.text("0"),
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
    )

    op.create_index(
        "ix_exercises_primary_muscle",
        "exercises",
        ["primary_muscle"],
        unique=False,
    )
    op.create_index(
        "ix_exercises_exercise_type",
        "exercises",
        ["exercise_type"],
        unique=False,
    )
    op.create_index(
        "ix_exercises_is_active",
        "exercises",
        ["is_active"],
        unique=False,
    )

    op.create_table(
        "exercise_equipment",
        sa.Column(
            "exercise_id",
            sa.String(),
            sa.ForeignKey("exercises.id", ondelete="CASCADE"),
            primary_key=True,
            nullable=False,
        ),
        sa.Column(
            "equipment_id",
            sa.String(),
            sa.ForeignKey("equipment.id", ondelete="RESTRICT"),
            primary_key=True,
            nullable=False,
        ),
    )
    op.create_index(
        "ix_exercise_equipment_equipment_id",
        "exercise_equipment",
        ["equipment_id"],
        unique=False,
    )

    op.create_table(
        "user_exercise_favorites",
        sa.Column(
            "user_id",
            sa.String(),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            primary_key=True,
            nullable=False,
        ),
        sa.Column(
            "exercise_id",
            sa.String(),
            sa.ForeignKey("exercises.id", ondelete="CASCADE"),
            primary_key=True,
            nullable=False,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
    )
    op.create_index(
        "ix_user_exercise_favorites_user_id",
        "user_exercise_favorites",
        ["user_id"],
        unique=False,
    )

    # Seed data
    connection = op.get_bind()

    for item in SEED_EXERCISES:
        connection.execute(
            sa.text(
                """
                INSERT INTO exercises (
                    id,
                    name,
                    exercise_type,
                    primary_muscle,
                    secondary_muscles,
                    demo_video_url,
                    image_url,
                    is_active,
                    source,
                    sort_order
                )
                VALUES (
                    :id,
                    :name,
                    :exercise_type,
                    :primary_muscle,
                    :secondary_muscles,
                    :demo_video_url,
                    :image_url,
                    :is_active,
                    :source,
                    :sort_order
                )
                ON CONFLICT (id) DO NOTHING
                """
            ),
            {
                "id": item["id"],
                "name": item["name"],
                "exercise_type": item["exercise_type"],
                "primary_muscle": item["primary_muscle"],
                "secondary_muscles": json.dumps(item["secondary_muscles"]),
                "demo_video_url": item["demo_video_url"],
                "image_url": item["image_url"],
                "is_active": True,
                "source": "seed",
                "sort_order": item.get("sort_order", 0),
            },
        )

        for equipment_id in item.get("required_equipment_ids", []):
            exists = connection.execute(
                sa.text(
                    "SELECT 1 FROM equipment WHERE id = :equipment_id"
                ),
                {"equipment_id": equipment_id},
            ).scalar()

            if not exists:
                raise Exception(
                    f"Seed exercise '{item['id']}' references missing equipment id '{equipment_id}'"
                )

            connection.execute(
                sa.text(
                    """
                    INSERT INTO exercise_equipment (exercise_id, equipment_id)
                    VALUES (:exercise_id, :equipment_id)
                    """
                ),
                {
                    "exercise_id": item["id"],
                    "equipment_id": equipment_id,
                },
            )


def downgrade() -> None:
    """Downgrade schema: drop favorites, join table, and exercises."""
    op.drop_index("ix_user_exercise_favorites_user_id", table_name="user_exercise_favorites")
    op.drop_table("user_exercise_favorites")

    op.drop_index("ix_exercise_equipment_equipment_id", table_name="exercise_equipment")
    op.drop_table("exercise_equipment")

    op.drop_index("ix_exercises_is_active", table_name="exercises")
    op.drop_index("ix_exercises_exercise_type", table_name="exercises")
    op.drop_index("ix_exercises_primary_muscle", table_name="exercises")
    op.drop_table("exercises")


