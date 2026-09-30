"""Atomic run usage and worker/action reservations.

Revision ID: 0002_controller_state
Revises: 0001_events
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0002_controller_state"
down_revision = "0001_events"
branch_labels = None
depends_on = None


def upgrade() -> None:
    uuid = postgresql.UUID(as_uuid=True)
    op.create_table(
        "run_usage",
        sa.Column("run_id", uuid, sa.ForeignKey("runs.run_id"), primary_key=True),
        sa.Column("budget_hash", sa.String(64)),
        sa.Column("used_actions", sa.Integer(), nullable=False),
        sa.Column("used_http_requests", sa.Integer(), nullable=False),
        sa.Column("used_model_calls", sa.Integer(), nullable=False),
        sa.Column("used_tokens", sa.BigInteger(), nullable=False),
        sa.Column("reserved_tokens", sa.BigInteger(), nullable=False),
        sa.Column("used_cost_microusd", sa.BigInteger(), nullable=False),
        sa.Column("reserved_cost_microusd", sa.BigInteger(), nullable=False),
        sa.Column("spawned_workers", sa.Integer(), nullable=False),
        sa.Column("active_workers", sa.Integer(), nullable=False),
        sa.Column("last_dispatch_at", sa.DateTime(timezone=True)),
    )
    op.create_table(
        "action_reservations",
        sa.Column("run_id", uuid, sa.ForeignKey("runs.run_id"), primary_key=True),
        sa.Column("action_id", uuid, primary_key=True),
        sa.Column("fingerprint", sa.String(64), nullable=False),
        sa.Column("worker_id", uuid),
        sa.Column("task_id", uuid),
        sa.Column("released_at", sa.DateTime(timezone=True)),
    )
    op.create_index(
        "uq_action_active_fingerprint",
        "action_reservations",
        ["run_id", "fingerprint"],
        unique=True,
        postgresql_where=sa.text("released_at IS NULL"),
    )
    op.create_table(
        "model_reservations",
        sa.Column("run_id", uuid, sa.ForeignKey("runs.run_id"), primary_key=True),
        sa.Column("call_id", uuid, primary_key=True),
        sa.Column("worker_id", uuid),
        sa.Column("task_id", uuid),
        sa.Column("reserved_tokens", sa.BigInteger(), nullable=False),
        sa.Column("reserved_cost_microusd", sa.BigInteger(), nullable=False),
        sa.Column("settled_at", sa.DateTime(timezone=True)),
    )
    op.create_table(
        "worker_slots",
        sa.Column("run_id", uuid, sa.ForeignKey("runs.run_id"), primary_key=True),
        sa.Column("worker_id", uuid, primary_key=True),
        sa.Column("task_id", uuid, nullable=False),
        sa.Column("status", sa.String(24), nullable=False),
    )


def downgrade() -> None:
    op.drop_table("worker_slots")
    op.drop_table("model_reservations")
    op.drop_index("uq_action_active_fingerprint", table_name="action_reservations")
    op.drop_table("action_reservations")
    op.drop_table("run_usage")
