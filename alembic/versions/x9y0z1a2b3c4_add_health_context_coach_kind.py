"""Allow health context items in the private Coach feed."""

from alembic import op


revision = "x9y0z1a2b3c4"
down_revision = "w8x9y0z1a2b3"
branch_labels = None
depends_on = None


def upgrade():
    op.drop_constraint("ck_coach_feed_items_kind", "coach_feed_items", type_="check")
    op.create_check_constraint(
        "ck_coach_feed_items_kind",
        "coach_feed_items",
        "kind IN ('progression', 'recovery', 'missed_workout', 'personal_record', 'consistency', 'program_review', 'upcoming_workout', 'health_context')",
    )


def downgrade():
    op.execute("DELETE FROM coach_feed_items WHERE kind = 'health_context'")
    op.drop_constraint("ck_coach_feed_items_kind", "coach_feed_items", type_="check")
    op.create_check_constraint(
        "ck_coach_feed_items_kind",
        "coach_feed_items",
        "kind IN ('progression', 'recovery', 'missed_workout', 'personal_record', 'consistency', 'program_review', 'upcoming_workout')",
    )
