"""Versioned exercise content and reviewed media.

Revision ID: s4t5u6v7w8x9
Revises: r3s4t5u6v7w8
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

revision = "s4t5u6v7w8x9"
down_revision = "r3s4t5u6v7w8"
branch_labels = None
depends_on = None


def upgrade():
    for name in ("content_version", "published_revision_id", "movement_pattern", "resistance_modality", "difficulty", "laterality", "tracking_mode"):
        op.add_column("exercises", sa.Column(name, sa.String(), nullable=True))
    for name in ("structured_content", "load_profile", "published_media", "legacy_snapshot"):
        op.add_column("exercises", sa.Column(name, JSONB(), nullable=True))
    op.add_column("exercises", sa.Column("publication_status", sa.String(), nullable=False, server_default="legacy"))
    op.add_column("exercises", sa.Column("generation_eligible", sa.Boolean(), nullable=False, server_default=sa.text("true")))
    op.add_column("equipment", sa.Column("aliases", JSONB(), nullable=False, server_default=sa.text("'[]'::jsonb")))
    equipment_aliases = {
        "dumbbells": ["dumbbell", "db", "dbs"],
        "olympic_barbell": ["barbell", "olympic bar"],
        "plates": ["weight plates"],
        "kettlebells": ["kettlebell"],
        "pull_up_bar": ["pullup bar", "chin up bar"],
        "lat_pulldown_cable": ["lat pulldown machine"],
        "row_cable": ["seated cable row"],
        "fly_machine": ["pec deck"],
        "hi_lo_pull_cable": ["adjustable cable"],
    }
    equipment = sa.table("equipment", sa.column("id", sa.String()), sa.column("aliases", JSONB()))
    for equipment_id, aliases in equipment_aliases.items():
        op.execute(equipment.update().where(equipment.c.id == equipment_id).values(aliases=aliases))
    op.add_column("exercise_questions", sa.Column("content_version", sa.String(), nullable=True))
    op.execute("""UPDATE exercises e SET legacy_snapshot =
      (to_jsonb(e) - 'legacy_snapshot') || jsonb_build_object(
        'provenance', 'legacy_unreviewed',
        'equipment_ids', COALESCE((SELECT jsonb_agg(ee.equipment_id ORDER BY ee.equipment_id) FROM exercise_equipment ee WHERE ee.exercise_id=e.id), '[]'::jsonb),
        'aliases', COALESCE((SELECT jsonb_agg(ea.alias ORDER BY ea.alias) FROM exercise_aliases ea WHERE ea.exercise_id=e.id), '[]'::jsonb))
      WHERE e.source='seed' AND e.owner_user_id IS NULL""")
    op.execute("""CREATE FUNCTION protect_exercise_legacy_snapshot() RETURNS trigger AS $$
    BEGIN
      IF OLD.legacy_snapshot IS NOT NULL AND OLD.legacy_snapshot IS DISTINCT FROM NEW.legacy_snapshot THEN
        RAISE EXCEPTION 'Legacy exercise snapshot is immutable';
      END IF;
      RETURN NEW;
    END; $$ LANGUAGE plpgsql""")
    op.execute("CREATE TRIGGER exercise_legacy_snapshot_immutable BEFORE UPDATE ON exercises FOR EACH ROW EXECUTE FUNCTION protect_exercise_legacy_snapshot()")
    op.create_table("exercise_revisions",
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column("exercise_id", sa.String(), sa.ForeignKey("exercises.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("content_hash", sa.String(64), nullable=False),
        sa.Column("payload", JSONB(), nullable=False),
        sa.Column("created_by", sa.String(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("submitted_at", sa.DateTime(timezone=True)),
        sa.UniqueConstraint("exercise_id", "content_hash"))
    op.create_index("ix_exercise_revisions_exercise_id", "exercise_revisions", ["exercise_id"])
    op.create_table("exercise_reviews",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("revision_id", sa.String(), sa.ForeignKey("exercise_revisions.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("content_hash", sa.String(64), nullable=False),
        sa.Column("reviewer", sa.String(), nullable=False),
        sa.Column("role", sa.String(), nullable=False),
        sa.Column("decision", sa.String(), nullable=False),
        sa.Column("comments", sa.Text(), nullable=False),
        sa.Column("artifact_hash", sa.String(64), unique=True),
        sa.Column("reviewed_at", sa.DateTime(timezone=True)),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.CheckConstraint("role IN ('trainer', 'publisher')", name="ck_exercise_review_role"),
        sa.CheckConstraint("decision IN ('approved', 'rejected')", name="ck_exercise_review_decision"))
    op.create_index("ix_exercise_reviews_revision_id", "exercise_reviews", ["revision_id"])
    op.create_table("exercise_publications",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("exercise_id", sa.String(), sa.ForeignKey("exercises.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("revision_id", sa.String(), sa.ForeignKey("exercise_revisions.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("operator", sa.String(), nullable=False),
        sa.Column("release_id", sa.String(), nullable=False),
        sa.Column("action", sa.String(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()))
    op.create_table("exercise_media_assets",
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column("exercise_id", sa.String(), sa.ForeignKey("exercises.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("content_hash", sa.String(64), nullable=False),
        sa.Column("storage_key", sa.String(), nullable=False),
        sa.Column("poster_key", sa.String()),
        sa.Column("mime_type", sa.String(), nullable=False),
        sa.Column("metadata_json", JSONB(), nullable=False),
        sa.Column("production_method", sa.String(), nullable=False),
        sa.Column("rights_documentation", sa.Text()),
        sa.Column("technical_validated", sa.Boolean(), nullable=False),
        sa.Column("approval_hash", sa.String(64), nullable=False),
        sa.Column("trainer_reviewer", sa.String()),
        sa.Column("publication_reviewer", sa.String()),
        sa.Column("approved_at", sa.DateTime(timezone=True)),
        sa.Column("published_url", sa.String()),
        sa.Column("poster_url", sa.String()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint("exercise_id", "approval_hash"))
    op.create_index("ix_exercise_media_assets_exercise_id", "exercise_media_assets", ["exercise_id"])
    op.create_table("exercise_media_publications",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("asset_id", sa.String(), sa.ForeignKey("exercise_media_assets.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("operator", sa.String(), nullable=False),
        sa.Column("release_id", sa.String(), nullable=False),
        sa.Column("action", sa.String(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()))
    op.create_table("exercise_media_reviews",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("asset_id", sa.String(), sa.ForeignKey("exercise_media_assets.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("approval_hash", sa.String(64), nullable=False),
        sa.Column("reviewer", sa.String(), nullable=False),
        sa.Column("role", sa.String(), nullable=False),
        sa.Column("decision", sa.String(), nullable=False),
        sa.Column("comments", sa.Text(), nullable=False),
        sa.Column("artifact_hash", sa.String(64), unique=True),
        sa.Column("reviewed_at", sa.DateTime(timezone=True)),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.CheckConstraint("role IN ('trainer', 'publisher')", name="ck_exercise_media_review_role"),
        sa.CheckConstraint("decision IN ('approved', 'rejected')", name="ck_exercise_media_review_decision"))
    op.create_index("ix_exercise_media_reviews_asset_id", "exercise_media_reviews", ["asset_id"])
    # Submitted content and audit decisions must not silently change after review.
    op.execute("""CREATE FUNCTION protect_exercise_revision() RETURNS trigger AS $$
    BEGIN
      IF OLD.payload IS DISTINCT FROM NEW.payload OR OLD.content_hash IS DISTINCT FROM NEW.content_hash
         OR OLD.exercise_id IS DISTINCT FROM NEW.exercise_id OR OLD.created_by IS DISTINCT FROM NEW.created_by
         OR OLD.id IS DISTINCT FROM NEW.id OR OLD.created_at IS DISTINCT FROM NEW.created_at
         OR (OLD.submitted_at IS NOT NULL AND OLD.submitted_at IS DISTINCT FROM NEW.submitted_at) THEN
        RAISE EXCEPTION 'Exercise revisions are immutable; import a new revision';
      END IF;
      RETURN NEW;
    END; $$ LANGUAGE plpgsql""")
    op.execute("CREATE TRIGGER exercise_revision_immutable BEFORE UPDATE ON exercise_revisions FOR EACH ROW EXECUTE FUNCTION protect_exercise_revision()")
    op.execute("""CREATE FUNCTION protect_exercise_media() RETURNS trigger AS $$
    BEGIN
      IF ROW(OLD.id, OLD.created_at, OLD.exercise_id, OLD.content_hash, OLD.storage_key, OLD.poster_key, OLD.mime_type,
             OLD.metadata_json, OLD.production_method, OLD.rights_documentation, OLD.technical_validated, OLD.approval_hash)
         IS DISTINCT FROM
         ROW(NEW.id, NEW.created_at, NEW.exercise_id, NEW.content_hash, NEW.storage_key, NEW.poster_key, NEW.mime_type,
             NEW.metadata_json, NEW.production_method, NEW.rights_documentation, NEW.technical_validated, NEW.approval_hash) THEN
        RAISE EXCEPTION 'Media submission is immutable; register a new asset';
      END IF;
      RETURN NEW;
    END; $$ LANGUAGE plpgsql""")
    op.execute("CREATE TRIGGER exercise_media_immutable BEFORE UPDATE ON exercise_media_assets FOR EACH ROW EXECUTE FUNCTION protect_exercise_media()")
    op.execute("""CREATE FUNCTION reject_exercise_audit_mutation() RETURNS trigger AS $$
    BEGIN RAISE EXCEPTION 'Exercise review and publication audit records are immutable'; END;
    $$ LANGUAGE plpgsql""")
    for table in ("exercise_reviews", "exercise_publications", "exercise_media_publications", "exercise_media_reviews"):
        op.execute(f"CREATE TRIGGER {table}_immutable BEFORE UPDATE OR DELETE ON {table} FOR EACH ROW EXECUTE FUNCTION reject_exercise_audit_mutation()")
    op.execute("CREATE TRIGGER exercise_revisions_no_delete BEFORE DELETE ON exercise_revisions FOR EACH ROW EXECUTE FUNCTION reject_exercise_audit_mutation()")


def downgrade():
    op.execute("DROP TRIGGER IF EXISTS exercise_legacy_snapshot_immutable ON exercises")
    op.execute("DROP FUNCTION IF EXISTS protect_exercise_legacy_snapshot()")
    op.execute("DROP TRIGGER IF EXISTS exercise_revisions_no_delete ON exercise_revisions")
    for table in ("exercise_reviews", "exercise_publications", "exercise_media_publications", "exercise_media_reviews"):
        if sa.inspect(op.get_bind()).has_table(table):
            op.execute(f"DROP TRIGGER IF EXISTS {table}_immutable ON {table}")
    op.execute("DROP FUNCTION IF EXISTS reject_exercise_audit_mutation()")
    op.execute("DROP TRIGGER exercise_media_immutable ON exercise_media_assets")
    op.execute("DROP FUNCTION protect_exercise_media()")
    op.execute("DROP TRIGGER exercise_revision_immutable ON exercise_revisions")
    op.execute("DROP FUNCTION protect_exercise_revision()")
    for table in ("exercise_media_reviews", "exercise_media_publications", "exercise_media_assets", "exercise_publications", "exercise_reviews", "exercise_revisions"):
        if sa.inspect(op.get_bind()).has_table(table):
            op.drop_table(table)
    op.drop_column("exercise_questions", "content_version")
    op.drop_column("equipment", "aliases")
    for name in ("content_version", "published_revision_id", "movement_pattern", "resistance_modality", "difficulty", "laterality", "tracking_mode", "structured_content", "load_profile", "published_media", "legacy_snapshot", "publication_status", "generation_eligible"):
        if name in {column["name"] for column in sa.inspect(op.get_bind()).get_columns("exercises")}:
            op.drop_column("exercises", name)
