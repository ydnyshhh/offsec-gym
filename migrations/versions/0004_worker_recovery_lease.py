"""Persist worker lease timestamps for deterministic crash reconciliation.

Revision ID: 0004_worker_recovery_lease
Revises: 0003_model_reservation_split
"""

import sqlalchemy as sa
from alembic import op

revision = "0004_worker_recovery_lease"
down_revision = "0003_model_reservation_split"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("worker_slots", sa.Column("lease_expires_at", sa.DateTime(timezone=True)))
    op.add_column("worker_slots", sa.Column("last_heartbeat_at", sa.DateTime(timezone=True)))
    op.create_index("ix_worker_slots_lease", "worker_slots", ["status", "lease_expires_at"])


def downgrade() -> None:
    op.drop_index("ix_worker_slots_lease", table_name="worker_slots")
    op.drop_column("worker_slots", "last_heartbeat_at")
    op.drop_column("worker_slots", "lease_expires_at")
