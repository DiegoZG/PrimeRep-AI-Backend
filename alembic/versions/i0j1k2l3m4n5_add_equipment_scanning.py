"""Add independent scanning access and separate provider reservations/receipts."""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "i0j1k2l3m4n5"
down_revision = "h9i0j1k2l3m4"
branch_labels = None
depends_on = None


def upgrade():
    op.execute("CREATE INDEX ix_equipment_scan_search ON equipment USING gin (to_tsvector('simple', name || ' ' || aliases::text))")
    op.add_column("feature_flag_configs", sa.Column("equipment_scanning_enabled", sa.Boolean(), nullable=False, server_default=sa.false()))
    op.add_column("feature_flag_configs", sa.Column("equipment_scanning_audience", sa.String(16), nullable=False, server_default="everyone"))
    op.create_check_constraint("ck_feature_flag_scanning_audience", "feature_flag_configs", "equipment_scanning_audience IN ('everyone','selected')")
    op.add_column("feature_flag_user_overrides", sa.Column("equipment_scanning_enabled", sa.Boolean()))
    op.drop_constraint("ck_feature_flag_override_has_rule", "feature_flag_user_overrides", type_="check")
    op.create_check_constraint("ck_feature_flag_override_has_rule", "feature_flag_user_overrides", "subscriptions_purchases_enabled IS NOT NULL OR subscriptions_enforcement_enabled IS NOT NULL OR subscriptions_onboarding_enabled IS NOT NULL OR workout_reliability_monitoring_enabled IS NOT NULL OR equipment_scanning_enabled IS NOT NULL")
    op.create_table("ai_spend_periods", sa.Column("environment", sa.String(32), primary_key=True), sa.Column("period", sa.String(16), primary_key=True), sa.Column("committed_micro_usd", sa.BigInteger(), nullable=False))
    op.create_table("ai_request_reservations", sa.Column("id", sa.String(36), primary_key=True), sa.Column("environment", sa.String(32), nullable=False), sa.Column("purpose", sa.String(32), nullable=False), sa.Column("model", sa.String(80), nullable=False), sa.Column("status", sa.String(16), nullable=False), sa.Column("reserved_micro_usd", sa.BigInteger(), nullable=False), sa.Column("accounted_micro_usd", sa.BigInteger()), sa.Column("input_tokens", sa.Integer()), sa.Column("output_tokens", sa.Integer()), sa.Column("reasoning_tokens", sa.Integer()), sa.Column("created_at", sa.DateTime(timezone=True), nullable=False), sa.Column("settled_at", sa.DateTime(timezone=True)))
    op.create_index("ix_ai_request_reservations_environment", "ai_request_reservations", ["environment"])
    op.create_table("equipment_scan_attempts", sa.Column("id", sa.String(36), primary_key=True), sa.Column("user_id", sa.String(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False), sa.Column("environment", sa.String(32), nullable=False), sa.Column("attempt_key", sa.String(36), nullable=False), sa.Column("fingerprint", sa.String(64)), sa.Column("versions", postgresql.JSONB(), nullable=False), sa.Column("settings_version", sa.String(64), nullable=False), sa.Column("status", sa.String(16), nullable=False), sa.Column("reservation_id", sa.String(36), sa.ForeignKey("ai_request_reservations.id", ondelete="SET NULL")), sa.Column("result_json", postgresql.JSONB()), sa.Column("error_code", sa.String(64)), sa.Column("created_at", sa.DateTime(timezone=True), nullable=False), sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False), sa.Column("deadline_at", sa.DateTime(timezone=True), nullable=False), sa.Column("finished_at", sa.DateTime(timezone=True)), sa.UniqueConstraint("environment", "user_id", "attempt_key", name="uq_equipment_scan_attempt_key"))
    op.create_index("uq_equipment_scan_active_owner", "equipment_scan_attempts", ["environment", "user_id"], unique=True, postgresql_where=sa.text("status = 'processing'"))
    op.create_index("ix_equipment_scan_fingerprint", "equipment_scan_attempts", ["environment", "user_id", "fingerprint"])
    op.create_index("ix_equipment_scan_attempts_expires_at", "equipment_scan_attempts", ["expires_at"])
    op.create_table("equipment_scan_quotas", sa.Column("user_id", sa.String(), sa.ForeignKey("users.id", ondelete="CASCADE"), primary_key=True), sa.Column("environment", sa.String(32), primary_key=True), sa.Column("period", sa.String(32), primary_key=True), sa.Column("attempts", sa.Integer(), nullable=False), sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False))
    op.create_index("ix_equipment_scan_quotas_expires_at", "equipment_scan_quotas", ["expires_at"])


def downgrade():
    op.drop_index("ix_equipment_scan_search", table_name="equipment")
    op.drop_table("equipment_scan_quotas")
    op.drop_table("equipment_scan_attempts")
    op.drop_table("ai_request_reservations")
    op.drop_table("ai_spend_periods")
    op.execute("DELETE FROM feature_flag_user_overrides WHERE subscriptions_purchases_enabled IS NULL AND subscriptions_enforcement_enabled IS NULL AND subscriptions_onboarding_enabled IS NULL AND workout_reliability_monitoring_enabled IS NULL")
    op.drop_constraint("ck_feature_flag_override_has_rule", "feature_flag_user_overrides", type_="check")
    op.create_check_constraint("ck_feature_flag_override_has_rule", "feature_flag_user_overrides", "subscriptions_purchases_enabled IS NOT NULL OR subscriptions_enforcement_enabled IS NOT NULL OR subscriptions_onboarding_enabled IS NOT NULL OR workout_reliability_monitoring_enabled IS NOT NULL")
    op.drop_column("feature_flag_user_overrides", "equipment_scanning_enabled")
    op.drop_constraint("ck_feature_flag_scanning_audience", "feature_flag_configs", type_="check")
    op.drop_column("feature_flag_configs", "equipment_scanning_audience")
    op.drop_column("feature_flag_configs", "equipment_scanning_enabled")
