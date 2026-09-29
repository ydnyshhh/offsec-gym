"""Initial event stream and run sequencer.

Revision ID: 0001_events
Revises:
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0001_events"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "runs",
        sa.Column("run_id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("last_sequence", sa.BigInteger(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_table(
        "events",
        sa.Column("event_id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "run_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("runs.run_id"), nullable=False
        ),
        sa.Column("sequence_number", sa.BigInteger(), nullable=False),
        sa.Column("type", sa.String(length=80), nullable=False),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("payload", postgresql.JSONB(), nullable=False),
        sa.UniqueConstraint("run_id", "sequence_number", name="uq_events_run_sequence"),
    )
    op.create_index("ix_events_run_type", "events", ["run_id", "type"])


def downgrade() -> None:
    op.drop_index("ix_events_run_type", table_name="events")
    op.drop_table("events")
    op.drop_table("runs")
