"""stage 3c immutable Interview Pack content identity

Revision ID: 202608260109
Revises: 202608260108
"""

import hashlib
import json
from collections import Counter

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "202608260109"
down_revision = "202608260108"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("interview_pack_versions", sa.Column("authored_version", sa.String(64)))
    op.add_column("interview_pack_versions", sa.Column("content_hash", sa.String(128)))
    packs = sa.table(
        "interview_pack_versions",
        sa.column("id", postgresql.UUID(as_uuid=True)),
        sa.column("problem_version_id", postgresql.UUID(as_uuid=True)),
        sa.column("pack_json", postgresql.JSONB()),
        sa.column("authored_version", sa.String(64)),
        sa.column("content_hash", sa.String(128)),
    )
    connection = op.get_bind()
    rows = list(
        connection.execute(
            sa.select(packs.c.id, packs.c.problem_version_id, packs.c.pack_json)
        ).mappings()
    )
    candidates = [_authored_version(row["pack_json"]) for row in rows]
    candidate_counts = Counter(
        (row["problem_version_id"], candidate)
        for row, candidate in zip(rows, candidates, strict=True)
        if candidate is not None
    )
    for row, candidate in zip(rows, candidates, strict=True):
        authored_version = (
            candidate
            if candidate is not None
            and candidate_counts[(row["problem_version_id"], candidate)] == 1
            else f"legacy:{row['id']}"
        )
        connection.execute(
            packs.update()
            .where(packs.c.id == row["id"])
            .values(
                authored_version=authored_version,
                content_hash=_canonical_hash(row["pack_json"]),
            )
        )
    op.alter_column("interview_pack_versions", "authored_version", nullable=False)
    op.alter_column("interview_pack_versions", "content_hash", nullable=False)
    op.create_unique_constraint(
        "uq_interview_pack_versions_problem_authored_version",
        "interview_pack_versions",
        ["problem_version_id", "authored_version"],
    )


def downgrade() -> None:
    op.drop_constraint(
        "uq_interview_pack_versions_problem_authored_version",
        "interview_pack_versions",
        type_="unique",
    )
    op.drop_column("interview_pack_versions", "content_hash")
    op.drop_column("interview_pack_versions", "authored_version")


def _authored_version(pack_json: object) -> str | None:
    if not isinstance(pack_json, dict):
        return None
    value = pack_json.get("version")
    return value if isinstance(value, str) and 0 < len(value) <= 64 else None


def _canonical_hash(value: object) -> str:
    encoded = json.dumps(
        _normalize_newlines(value),
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
    ).encode()
    return f"sha256:{hashlib.sha256(encoded).hexdigest()}"


def _normalize_newlines(value: object) -> object:
    if isinstance(value, str):
        return value.replace("\r\n", "\n").replace("\r", "\n")
    if isinstance(value, list):
        return [_normalize_newlines(item) for item in value]
    if isinstance(value, dict):
        return {key: _normalize_newlines(item) for key, item in value.items()}
    return value
