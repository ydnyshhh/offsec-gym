"""Persist admission holds that protect future worker trajectories.

Revision ID: 0007_opportunity_holds
Revises: 0006_elastic_task_budget
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0007_opportunity_holds"
down_revision = "0006_elastic_task_budget"
branch_labels = None
depends_on = None


def upgrade() -> None:
    uuid = postgresql.UUID(as_uuid=True)
    op.create_table(
        "task_budget_holds",
        sa.Column("run_id", uuid, sa.ForeignKey("runs.run_id"), primary_key=True),
        sa.Column("task_id", uuid, primary_key=True),
        sa.Column("worker_id", uuid, nullable=False),
        sa.Column("kind", sa.String(32), nullable=False),
        sa.Column("phase", sa.String(16), nullable=False),
        sa.Column("token_hold", sa.BigInteger(), nullable=False),
        sa.Column("model_call_hold", sa.Integer(), nullable=False),
        sa.Column("action_hold", sa.Integer(), nullable=False),
        sa.Column("http_hold", sa.Integer(), nullable=False),
        sa.Column("active", sa.Boolean(), nullable=False),
        sa.UniqueConstraint("run_id", "kind", name="uq_task_budget_hold_kind"),
    )


def downgrade() -> None:
    op.drop_table("task_budget_holds")
