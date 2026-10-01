"""Add atomic elastic task leases alongside frozen M6.2.3 escrows.

Revision ID: 0006_elastic_task_budget
Revises: 0005_worker_budget_escrow
"""

import sqlalchemy as sa
from alembic import op

revision = "0006_elastic_task_budget"
down_revision = "0005_worker_budget_escrow"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "worker_budget_accounts",
        sa.Column("elastic", sa.Boolean(), nullable=False, server_default=sa.false()),
    )
    op.add_column("worker_budget_accounts", sa.Column("kind", sa.String(32)))
    op.add_column("worker_budget_accounts", sa.Column("token_cap", sa.BigInteger()))
    op.add_column("worker_budget_accounts", sa.Column("model_call_cap", sa.Integer()))
    op.create_index(
        "uq_elastic_task_kind",
        "worker_budget_accounts",
        ["run_id", "kind"],
        unique=True,
        postgresql_where=sa.text("elastic = true"),
    )


def downgrade() -> None:
    op.drop_index("uq_elastic_task_kind", table_name="worker_budget_accounts")
    op.drop_column("worker_budget_accounts", "model_call_cap")
    op.drop_column("worker_budget_accounts", "token_cap")
    op.drop_column("worker_budget_accounts", "kind")
    op.drop_column("worker_budget_accounts", "elastic")
