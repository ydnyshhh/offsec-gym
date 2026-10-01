"""Record the input and output components of model reservations.

Revision ID: 0003_model_reservation_split
Revises: 0002_controller_state
"""

import sqlalchemy as sa
from alembic import op

revision = "0003_model_reservation_split"
down_revision = "0002_controller_state"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("model_reservations", sa.Column("reserved_input_tokens", sa.BigInteger()))
    op.add_column("model_reservations", sa.Column("reserved_output_tokens", sa.BigInteger()))
    op.add_column("model_reservations", sa.Column("request_bytes", sa.BigInteger()))


def downgrade() -> None:
    op.drop_column("model_reservations", "request_bytes")
    op.drop_column("model_reservations", "reserved_output_tokens")
    op.drop_column("model_reservations", "reserved_input_tokens")
