"""Store isolated reporting branches over an immutable probe prefix.

Revision ID: 0008_reporting_branches
Revises: 0007_opportunity_holds
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0008_reporting_branches"
down_revision = "0007_opportunity_holds"
branch_labels = None
depends_on = None


def upgrade() -> None:
    uuid = postgresql.UUID(as_uuid=True)
    op.create_table(
        "reporting_branches",
        sa.Column("branch_id", uuid, primary_key=True),
        sa.Column("source_run_id", uuid, sa.ForeignKey("runs.run_id"), nullable=False),
        sa.Column("arm", sa.String(16), nullable=False),
        sa.Column("checkpoint_id", uuid, nullable=False),
        sa.Column("source_sequence", sa.BigInteger(), nullable=False),
        sa.Column("source_sha256", sa.String(64), nullable=False),
        sa.Column("last_sequence", sa.BigInteger(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("source_run_id", "arm", name="uq_reporting_source_arm"),
    )
    op.create_table(
        "reporting_branch_events",
        sa.Column(
            "branch_id", uuid, sa.ForeignKey("reporting_branches.branch_id"), primary_key=True
        ),
        sa.Column("sequence_number", sa.BigInteger(), primary_key=True),
        sa.Column("event_id", uuid, nullable=False),
        sa.Column("type", sa.String(80), nullable=False),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("payload", postgresql.JSONB(), nullable=False),
        sa.UniqueConstraint("branch_id", "event_id", name="uq_reporting_branch_event_id"),
    )


def downgrade() -> None:
    op.drop_table("reporting_branch_events")
    op.drop_table("reporting_branches")
