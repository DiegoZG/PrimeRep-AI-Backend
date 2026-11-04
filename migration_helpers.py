import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


def _columns(op, table):
    return {column["name"] for column in sa.inspect(op.get_bind()).get_columns(table)}


def ensure_session_arrangements(op):
    columns = _columns(op, "workout_sessions")
    if "effective_workout" not in columns:
        op.add_column("workout_sessions", sa.Column("effective_workout", postgresql.JSONB(), nullable=True))
    if "arrangement_revision" not in columns:
        op.add_column("workout_sessions", sa.Column("arrangement_revision", sa.Integer(), server_default="0", nullable=False))
    if "payload_fingerprint" not in _columns(op, "workout_session_operations"):
        op.add_column("workout_session_operations", sa.Column("payload_fingerprint", sa.String(64), nullable=True))


def ensure_feature_flags(op):
    tables = set(sa.inspect(op.get_bind()).get_table_names())
    if "feature_flag_configs" not in tables:
        op.create_table(
            "feature_flag_configs",
            sa.Column("environment", sa.String(32), primary_key=True),
            sa.Column("version", sa.Integer(), nullable=False),
            sa.Column("subscriptions_purchases_enabled", sa.Boolean(), nullable=False, server_default=sa.false()),
            sa.Column("subscriptions_enforcement_enabled", sa.Boolean(), nullable=False, server_default=sa.false()),
            sa.Column("subscriptions_launch_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("launch_locked", sa.Boolean(), nullable=False, server_default=sa.false()),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
            sa.CheckConstraint("version > 0", name="ck_feature_flag_version_positive"),
            sa.CheckConstraint("NOT subscriptions_enforcement_enabled OR (subscriptions_purchases_enabled AND subscriptions_launch_at IS NOT NULL AND launch_locked)", name="ck_feature_flag_enforcement_ready"),
            sa.CheckConstraint("NOT launch_locked OR subscriptions_launch_at IS NOT NULL", name="ck_feature_flag_locked_launch"),
        )
    if "feature_flag_audits" not in tables:
        op.create_table(
            "feature_flag_audits",
            sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
            sa.Column("environment", sa.String(32), sa.ForeignKey("feature_flag_configs.environment"), nullable=False),
            sa.Column("version", sa.Integer(), nullable=False),
            sa.Column("changed_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("changed_by", sa.String(), sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True),
            sa.Column("before", sa.JSON(), nullable=False), sa.Column("after", sa.JSON(), nullable=False),
            sa.UniqueConstraint("environment", "version", name="uq_feature_flag_audit_version"),
        )


def ensure_flag_targeting(op):
    ensure_feature_flags(op)
    columns = _columns(op, "feature_flag_configs")
    for name in ("subscriptions_purchases_audience", "subscriptions_enforcement_audience"):
        if name not in columns:
            suffix = "purchases" if "purchases" in name else "enforcement"
            op.add_column("feature_flag_configs", sa.Column(name, sa.String(16),
                sa.CheckConstraint(f"{name} IN ('everyone', 'selected')", name=f"ck_feature_flag_{suffix}_audience"),
                nullable=False, server_default="everyone"))
    checks = {constraint["name"] for constraint in sa.inspect(op.get_bind()).get_check_constraints("feature_flag_configs")}
    for suffix in ("purchases", "enforcement"):
        name = f"ck_feature_flag_{suffix}_audience"
        if name not in checks:
            with op.batch_alter_table("feature_flag_configs") as batch:
                batch.create_check_constraint(name, f"subscriptions_{suffix}_audience IN ('everyone', 'selected')")
    columns = _columns(op, "feature_flag_audits")
    with op.batch_alter_table("feature_flag_audits") as batch:
        if "scope" not in columns:
            batch.add_column(sa.Column("scope", sa.String(16), nullable=False, server_default="global"))
        if "target_user_id" not in columns:
            batch.add_column(sa.Column("target_user_id", sa.String(), nullable=True))
            batch.create_foreign_key("fk_feature_flag_audit_target", "users", ["target_user_id"], ["id"], ondelete="SET NULL")
        checks = {constraint["name"] for constraint in sa.inspect(op.get_bind()).get_check_constraints("feature_flag_audits")}
        if "ck_feature_flag_audit_scope" not in checks:
            batch.create_check_constraint("ck_feature_flag_audit_scope", "scope IN ('global', 'user')")
    if "feature_flag_user_overrides" not in sa.inspect(op.get_bind()).get_table_names():
        op.create_table(
            "feature_flag_user_overrides",
            sa.Column("environment", sa.String(32), sa.ForeignKey("feature_flag_configs.environment", ondelete="CASCADE"), primary_key=True),
            sa.Column("user_id", sa.String(), sa.ForeignKey("users.id", ondelete="CASCADE"), primary_key=True),
            sa.Column("subscriptions_purchases_enabled", sa.Boolean(), nullable=True),
            sa.Column("subscriptions_enforcement_enabled", sa.Boolean(), nullable=True),
            sa.CheckConstraint("subscriptions_purchases_enabled IS NOT NULL OR subscriptions_enforcement_enabled IS NOT NULL", name="ck_feature_flag_override_has_rule"),
        )
