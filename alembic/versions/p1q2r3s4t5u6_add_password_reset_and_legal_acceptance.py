"""add password reset and legal acceptance

Revision ID: p1q2r3s4t5u6
Revises: o0p1q2r3s4t5
"""
from alembic import op
import sqlalchemy as sa


revision = "p1q2r3s4t5u6"
down_revision = "o0p1q2r3s4t5"
branch_labels = None
depends_on = None


def upgrade():
    connection = op.get_bind()
    collision_count = connection.execute(
        sa.text(
            """
            SELECT count(*)
            FROM (
                SELECT lower(email)
                FROM users
                GROUP BY lower(email)
                HAVING count(*) > 1
            ) AS collisions
            """
        )
    ).scalar()
    if collision_count:
        raise RuntimeError(
            "Cannot enforce case-insensitive user email uniqueness: "
            f"found {collision_count} normalized email collision(s)"
        )

    op.execute("UPDATE users SET email = lower(email)")
    op.create_index(
        "uq_users_email_case_insensitive",
        "users",
        [sa.text("lower(email)")],
        unique=True,
    )
    op.add_column(
        "users",
        sa.Column("auth_version", sa.Integer(), server_default="0", nullable=False),
    )
    op.add_column(
        "users", sa.Column("terms_accepted_version", sa.String(), nullable=True)
    )
    op.add_column(
        "users", sa.Column("privacy_accepted_version", sa.String(), nullable=True)
    )
    op.add_column(
        "users",
        sa.Column("legal_accepted_at", sa.DateTime(timezone=True), nullable=True),
    )

    op.create_table(
        "password_reset_tokens",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("user_id", sa.String(), nullable=False),
        sa.Column("token_hash", sa.String(length=64), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("used_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_password_reset_tokens_user_id", "password_reset_tokens", ["user_id"]
    )
    op.create_index(
        "ix_password_reset_tokens_token_hash",
        "password_reset_tokens",
        ["token_hash"],
        unique=True,
    )
    op.create_index(
        "ix_password_reset_tokens_user_expires_at",
        "password_reset_tokens",
        ["user_id", "expires_at"],
    )


def downgrade():
    op.execute("DROP INDEX IF EXISTS ix_password_reset_tokens_user_expires_at")
    op.drop_index("ix_password_reset_tokens_token_hash", table_name="password_reset_tokens")
    op.drop_index("ix_password_reset_tokens_user_id", table_name="password_reset_tokens")
    op.drop_table("password_reset_tokens")
    op.drop_column("users", "legal_accepted_at")
    op.drop_column("users", "privacy_accepted_version")
    op.drop_column("users", "terms_accepted_version")
    op.drop_column("users", "auth_version")
    op.execute("DROP INDEX IF EXISTS uq_users_email_case_insensitive")
