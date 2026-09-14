"""add explore programs and activation revisions

Revision ID: q2r3s4t5u6v7
Revises: p1q2r3s4t5u6
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = "q2r3s4t5u6v7"
down_revision = "p1q2r3s4t5u6"
branch_labels = None
depends_on = None


PROGRAMS = [
    ("system-full-body-foundation", "Full Body Foundation", "A balanced three-day introduction to strength training.", "general-fitness", "beginner", 3, 35, 1),
    ("system-upper-lower-strength", "Upper/Lower Strength", "Four focused days for building whole-body strength.", "get-stronger", "intermediate", 4, 45, 2),
    ("system-push-pull-legs", "Push/Pull/Legs", "A three-day muscle-building split organized by movement pattern.", "build-muscle", "intermediate", 3, 45, 3),
    ("system-beginner-strength", "Beginner Strength", "Three straightforward sessions built around foundational lifts.", "get-stronger", "beginner", 3, 40, 4),
    ("system-minimal-equipment", "Minimal Equipment", "Efficient full-body training with little or no equipment.", "general-fitness", "beginner", 3, 25, 5),
]

DAY_SPECS = {
    "system-full-body-foundation": [
        ("Full Body A", "full_body", [("squat", 4, 6, 10, 120), ("push_up", 3, 8, 15, 75), ("seated_row", 3, 8, 12, 90)]),
        ("Full Body B", "full_body", [("romanian_deadlift", 4, 6, 10, 120), ("overhead_press", 3, 6, 10, 90), ("lat_pulldown", 3, 8, 12, 90)]),
        ("Full Body C", "full_body", [("walking_lunge", 3, 8, 12, 90), ("incline_dumbbell_press", 3, 8, 12, 90), ("seated_row", 3, 8, 12, 90)]),
    ],
    "system-upper-lower-strength": [
        ("Upper A", "upper", [("bench_press", 4, 4, 8, 150), ("seated_row", 4, 6, 10, 120), ("overhead_press", 3, 6, 10, 120), ("lat_pulldown", 3, 8, 12, 90)]),
        ("Lower A", "lower", [("squat", 4, 4, 8, 180), ("romanian_deadlift", 4, 6, 10, 150), ("leg_extension", 3, 10, 15, 75), ("seated_calf_raise", 3, 10, 15, 75)]),
        ("Upper B", "upper", [("overhead_press", 4, 4, 8, 150), ("pull_up", 4, 5, 10, 120), ("incline_dumbbell_press", 3, 8, 12, 90), ("seated_row", 3, 8, 12, 90)]),
        ("Lower B", "lower", [("deadlift", 3, 3, 6, 180), ("front_squat", 4, 5, 8, 150), ("leg_curl", 3, 10, 15, 75), ("standing_calf_raise", 3, 10, 15, 75)]),
    ],
    "system-push-pull-legs": [
        ("Push Day", "push", [("bench_press", 4, 6, 10, 120), ("overhead_press", 4, 6, 10, 120), ("lateral_raise", 3, 10, 15, 75), ("tricep_pushdown", 3, 10, 15, 75)]),
        ("Pull Day", "pull", [("deadlift", 3, 4, 8, 150), ("lat_pulldown", 4, 8, 12, 90), ("seated_row", 3, 8, 12, 90), ("dumbbell_curl", 3, 10, 15, 75)]),
        ("Leg Day", "legs", [("squat", 4, 6, 10, 150), ("romanian_deadlift", 4, 6, 10, 120), ("leg_press", 3, 8, 12, 90), ("seated_calf_raise", 3, 10, 15, 75)]),
    ],
    "system-beginner-strength": [
        ("Strength A", "full_body", [("squat", 3, 5, 8, 150), ("bench_press", 3, 5, 8, 120), ("seated_row", 3, 8, 12, 90)]),
        ("Strength B", "full_body", [("deadlift", 3, 4, 6, 180), ("overhead_press", 3, 5, 8, 120), ("lat_pulldown", 3, 8, 12, 90)]),
        ("Strength C", "full_body", [("front_squat", 3, 5, 8, 150), ("incline_dumbbell_press", 3, 6, 10, 120), ("seated_row", 3, 8, 12, 90)]),
    ],
    "system-minimal-equipment": [
        ("Minimal A", "full_body", [("glute_bridge", 3, 10, 15, 60), ("push_up", 3, 8, 15, 60), ("plank", 3, 20, 40, 60)]),
        ("Minimal B", "full_body", [("walking_lunge", 3, 8, 12, 60), ("push_up", 3, 8, 15, 60), ("hanging_leg_raise", 3, 8, 15, 60)]),
        ("Minimal C", "full_body", [("glute_bridge", 3, 10, 15, 60), ("dip", 3, 6, 12, 75), ("plank", 3, 20, 40, 60)]),
    ],
}


def upgrade():
    op.execute("CREATE EXTENSION IF NOT EXISTS pg_trgm")
    op.create_table(
        "workout_templates",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("owner_user_id", sa.String(), nullable=True),
        sa.Column("scope", sa.String(), nullable=False),
        sa.Column("name", sa.String(), nullable=False),
        sa.Column("description", sa.Text(), nullable=False, server_default=""),
        sa.Column("goal", sa.String(), nullable=False),
        sa.Column("level", sa.String(), nullable=False),
        sa.Column("frequency", sa.Integer(), nullable=False),
        sa.Column("duration_minutes", sa.Integer(), nullable=False),
        sa.Column("featured_rank", sa.Integer(), nullable=True),
        sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("is_archived", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.CheckConstraint("scope IN ('system', 'private')", name="ck_workout_templates_scope"),
        sa.CheckConstraint("goal IN ('build-muscle', 'get-stronger', 'general-fitness', 'conditioning')", name="ck_workout_templates_goal"),
        sa.CheckConstraint("level IN ('no-experience', 'beginner', 'intermediate', 'advanced')", name="ck_workout_templates_level"),
        sa.CheckConstraint("frequency BETWEEN 1 AND 7", name="ck_workout_templates_frequency"),
        sa.CheckConstraint("duration_minutes BETWEEN 15 AND 120", name="ck_workout_templates_duration"),
        sa.CheckConstraint("(scope = 'system' AND owner_user_id IS NULL) OR (scope = 'private' AND owner_user_id IS NOT NULL)", name="ck_workout_templates_owner"),
        sa.ForeignKeyConstraint(["owner_user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_workout_templates_owner", "workout_templates", ["owner_user_id"])
    op.create_index("ix_workout_templates_catalog", "workout_templates", ["scope", "is_archived", "featured_rank"])
    op.execute("CREATE INDEX ix_workout_templates_name_trgm ON workout_templates USING gin (lower(name) gin_trgm_ops)")

    op.create_table(
        "workout_template_days",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("template_id", sa.String(), nullable=False),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.Column("title", sa.String(), nullable=False),
        sa.Column("day_type", sa.String(), nullable=False),
        sa.CheckConstraint("position >= 0", name="ck_workout_template_days_position"),
        sa.ForeignKeyConstraint(["template_id"], ["workout_templates.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("template_id", "position", name="uq_workout_template_days_position"),
    )
    op.create_table(
        "workout_template_exercises",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("template_day_id", sa.String(), nullable=False),
        sa.Column("exercise_id", sa.String(), nullable=False),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.Column("block_type", sa.String(), nullable=False, server_default="main"),
        sa.Column("sets", sa.Integer(), nullable=False),
        sa.Column("reps_min", sa.Integer(), nullable=False),
        sa.Column("reps_max", sa.Integer(), nullable=False),
        sa.Column("rest_seconds", sa.Integer(), nullable=False),
        sa.Column("cue", sa.Text(), nullable=True),
        sa.CheckConstraint("position >= 0", name="ck_workout_template_exercises_position"),
        sa.CheckConstraint("sets BETWEEN 1 AND 20", name="ck_workout_template_exercises_sets"),
        sa.CheckConstraint("reps_min >= 1 AND reps_max >= reps_min AND reps_max <= 100", name="ck_workout_template_exercises_reps"),
        sa.CheckConstraint("rest_seconds BETWEEN 0 AND 900", name="ck_workout_template_exercises_rest"),
        sa.ForeignKeyConstraint(["exercise_id"], ["exercises.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["template_day_id"], ["workout_template_days.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("template_day_id", "exercise_id", name="uq_workout_template_exercises_exercise"),
        sa.UniqueConstraint("template_day_id", "position", name="uq_workout_template_exercises_position"),
    )
    op.create_table(
        "workout_template_equipment",
        sa.Column("template_id", sa.String(), nullable=False),
        sa.Column("equipment_id", sa.String(), nullable=False),
        sa.ForeignKeyConstraint(["equipment_id"], ["equipment.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["template_id"], ["workout_templates.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("template_id", "equipment_id"),
    )
    op.create_table(
        "workout_template_aliases",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("template_id", sa.String(), nullable=False),
        sa.Column("alias", sa.String(), nullable=False),
        sa.ForeignKeyConstraint(["template_id"], ["workout_templates.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("template_id", "alias", name="uq_workout_template_alias"),
    )
    op.execute("CREATE INDEX ix_workout_template_alias_trgm ON workout_template_aliases USING gin (lower(alias) gin_trgm_ops)")
    op.create_table(
        "exercise_aliases",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("exercise_id", sa.String(), nullable=False),
        sa.Column("alias", sa.String(), nullable=False),
        sa.ForeignKeyConstraint(["exercise_id"], ["exercises.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("exercise_id", "alias", name="uq_exercise_alias"),
    )
    op.execute("CREATE INDEX ix_exercise_alias_trgm ON exercise_aliases USING gin (lower(alias) gin_trgm_ops)")
    op.execute("CREATE INDEX ix_exercises_name_trgm ON exercises USING gin (lower(name) gin_trgm_ops)")

    op.create_table(
        "user_program_activations",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("user_id", sa.String(), nullable=False),
        sa.Column("template_id", sa.String(), nullable=True),
        sa.Column("template_version", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(), nullable=False, server_default="active"),
        sa.Column("effective_date", sa.Date(), nullable=False),
        sa.Column("weekdays", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("activation_snapshot", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("apply_mode", sa.String(), nullable=False),
        sa.Column("client_operation_id", sa.String(), nullable=False),
        sa.Column("request_fingerprint", sa.String(), nullable=False),
        sa.Column("requires_review", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("deactivated_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint("status IN ('active', 'scheduled', 'superseded', 'deactivated')", name="ck_user_program_activations_status"),
        sa.CheckConstraint("apply_mode IN ('now', 'next-week')", name="ck_user_program_activations_apply_mode"),
        sa.ForeignKeyConstraint(["template_id"], ["workout_templates.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("user_id", "client_operation_id", name="uq_user_program_activation_operation"),
    )
    op.create_index("ix_user_program_activations_user", "user_program_activations", ["user_id", "status"])
    op.create_index("uq_user_program_activations_one_active", "user_program_activations", ["user_id"], unique=True, postgresql_where=sa.text("status = 'active'"))
    op.create_index("uq_user_program_activations_one_scheduled", "user_program_activations", ["user_id"], unique=True, postgresql_where=sa.text("status = 'scheduled'"))

    template_table = sa.table(
        "workout_templates",
        sa.column("id", sa.String()), sa.column("scope", sa.String()), sa.column("name", sa.String()),
        sa.column("description", sa.Text()), sa.column("goal", sa.String()), sa.column("level", sa.String()),
        sa.column("frequency", sa.Integer()), sa.column("duration_minutes", sa.Integer()), sa.column("featured_rank", sa.Integer()),
    )
    op.bulk_insert(template_table, [
        {"id": item[0], "scope": "system", "name": item[1], "description": item[2], "goal": item[3], "level": item[4], "frequency": item[5], "duration_minutes": item[6], "featured_rank": item[7]}
        for item in PROGRAMS
    ])
    day_table = sa.table("workout_template_days", sa.column("id", sa.String()), sa.column("template_id", sa.String()), sa.column("position", sa.Integer()), sa.column("title", sa.String()), sa.column("day_type", sa.String()))
    exercise_table = sa.table("workout_template_exercises", sa.column("id", sa.String()), sa.column("template_day_id", sa.String()), sa.column("exercise_id", sa.String()), sa.column("position", sa.Integer()), sa.column("block_type", sa.String()), sa.column("sets", sa.Integer()), sa.column("reps_min", sa.Integer()), sa.column("reps_max", sa.Integer()), sa.column("rest_seconds", sa.Integer()))
    alias_table = sa.table("workout_template_aliases", sa.column("id", sa.String()), sa.column("template_id", sa.String()), sa.column("alias", sa.String()))
    day_rows, exercise_rows, alias_rows = [], [], []
    for template_id, name, *_ in PROGRAMS:
        alias_rows.append({"id": f"{template_id}-alias", "template_id": template_id, "alias": name.replace("/", " ").lower()})
        for day_position, (title, day_type, exercises) in enumerate(DAY_SPECS[template_id]):
            day_id = f"{template_id}-day-{day_position}"
            day_rows.append({"id": day_id, "template_id": template_id, "position": day_position, "title": title, "day_type": day_type})
            for position, (exercise_id, sets, reps_min, reps_max, rest) in enumerate(exercises):
                exercise_rows.append({"id": f"{day_id}-exercise-{position}", "template_day_id": day_id, "exercise_id": exercise_id, "position": position, "block_type": "main" if position < 2 else "accessory", "sets": sets, "reps_min": reps_min, "reps_max": reps_max, "rest_seconds": rest})
    op.bulk_insert(day_table, day_rows)
    op.bulk_insert(exercise_table, exercise_rows)
    op.bulk_insert(alias_table, alias_rows)
    op.execute("""
        INSERT INTO workout_template_equipment (template_id, equipment_id)
        SELECT DISTINCT d.template_id, ee.equipment_id
        FROM workout_template_days d
        JOIN workout_template_exercises te ON te.template_day_id = d.id
        JOIN exercise_equipment ee ON ee.exercise_id = te.exercise_id
    """)
    exercise_alias_table = sa.table("exercise_aliases", sa.column("id", sa.String()), sa.column("exercise_id", sa.String()), sa.column("alias", sa.String()))
    op.bulk_insert(exercise_alias_table, [
        {"id": "alias-bench", "exercise_id": "bench_press", "alias": "bench"},
        {"id": "alias-rdl", "exercise_id": "romanian_deadlift", "alias": "rdl"},
        {"id": "alias-ohp", "exercise_id": "overhead_press", "alias": "ohp"},
        {"id": "alias-pulldown", "exercise_id": "lat_pulldown", "alias": "pulldown"},
    ])


def downgrade():
    op.execute("DROP INDEX IF EXISTS uq_user_program_activations_one_scheduled")
    op.drop_index("uq_user_program_activations_one_active", table_name="user_program_activations")
    op.drop_index("ix_user_program_activations_user", table_name="user_program_activations")
    op.drop_table("user_program_activations")
    op.execute("DROP INDEX IF EXISTS ix_exercises_name_trgm")
    op.execute("DROP INDEX IF EXISTS ix_exercise_alias_trgm")
    op.drop_table("exercise_aliases")
    op.execute("DROP INDEX IF EXISTS ix_workout_template_alias_trgm")
    op.drop_table("workout_template_aliases")
    op.drop_table("workout_template_equipment")
    op.drop_table("workout_template_exercises")
    op.drop_table("workout_template_days")
    op.execute("DROP INDEX IF EXISTS ix_workout_templates_name_trgm")
    op.drop_index("ix_workout_templates_catalog", table_name="workout_templates")
    op.drop_index("ix_workout_templates_owner", table_name="workout_templates")
    op.drop_table("workout_templates")
