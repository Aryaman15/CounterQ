"""stage 3c curated problem concepts

Revision ID: 202608260108
Revises: 202608260107
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "202608260108"
down_revision = "202608260107"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "concepts",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("canonical_key", sa.String(128), nullable=False, unique=True),
        sa.Column("display_name", sa.String(256), nullable=False),
        sa.Column("category", sa.String(64), nullable=False),
        sa.Column("parent_concept_id", postgresql.UUID(as_uuid=True)),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
        sa.ForeignKeyConstraint(["parent_concept_id"], ["concepts.id"], ondelete="RESTRICT"),
    )
    op.create_table(
        "concept_aliases",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("concept_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("alias", sa.String(256), nullable=False),
        sa.Column("normalized_alias", sa.String(256), nullable=False, unique=True),
        sa.Column("alias_type", sa.String(32), nullable=False),
        sa.ForeignKeyConstraint(["concept_id"], ["concepts.id"], ondelete="RESTRICT"),
    )
    op.create_table(
        "concept_relationships",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("from_concept_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("to_concept_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("relationship_type", sa.String(32), nullable=False),
        sa.ForeignKeyConstraint(["from_concept_id"], ["concepts.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["to_concept_id"], ["concepts.id"], ondelete="RESTRICT"),
        sa.UniqueConstraint("from_concept_id", "to_concept_id", "relationship_type", name="uq_concept_relationship"),
    )
    op.create_table(
        "problem_concepts",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("problem_version_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("concept_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("relevance", sa.String(32), nullable=False),
        sa.Column("expected_importance", sa.String(32)),
        sa.Column("role", sa.String(32), nullable=False),
        sa.ForeignKeyConstraint(["problem_version_id"], ["problem_versions.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["concept_id"], ["concepts.id"], ondelete="RESTRICT"),
        sa.UniqueConstraint("problem_version_id", "concept_id", name="uq_problem_concepts_version_concept"),
    )


def downgrade() -> None:
    op.drop_table("problem_concepts")
    op.drop_table("concept_relationships")
    op.drop_table("concept_aliases")
    op.drop_table("concepts")
