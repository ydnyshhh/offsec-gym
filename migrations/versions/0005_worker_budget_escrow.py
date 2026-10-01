"""Persist immutable worker compute escrows and their atomic counters.

Revision ID: 0005_worker_budget_escrow
Revises: 0004_worker_recovery_lease
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0005_worker_budget_escrow"
down_revision = "0004_worker_recovery_lease"
branch_labels = None
depends_on = None


def upgrade() -> None:
    uuid = postgresql.UUID(as_uuid=True)
    op.create_table(
        "worker_budget_accounts",
        sa.Column("run_id", uuid, sa.ForeignKey("runs.run_id"), primary_key=True),
        sa.Column("worker_id", uuid, primary_key=True),
        sa.Column("task_id", uuid, nullable=False),
        sa.Column("token_limit", sa.BigInteger(), nullable=False),
        sa.Column("model_call_limit", sa.Integer(), nullable=False),
        sa.Column("action_limit", sa.Integer(), nullable=False),
        sa.Column("http_limit", sa.Integer(), nullable=False),
        sa.Column("cost_limit_microusd", sa.BigInteger()),
        sa.Column("used_tokens", sa.BigInteger(), nullable=False),
        sa.Column("reserved_tokens", sa.BigInteger(), nullable=False),
        sa.Column("used_model_calls", sa.Integer(), nullable=False),
        sa.Column("used_actions", sa.Integer(), nullable=False),
        sa.Column("used_http_requests", sa.Integer(), nullable=False),
        sa.Column("used_cost_microusd", sa.BigInteger(), nullable=False),
        sa.Column("reserved_cost_microusd", sa.BigInteger(), nullable=False),
    )


def downgrade() -> None:
    op.drop_table("worker_budget_accounts")
