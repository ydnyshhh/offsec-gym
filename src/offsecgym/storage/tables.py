"""PostgreSQL control-plane tables."""

from sqlalchemy import (
    BigInteger,
    Boolean,
    Column,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    MetaData,
    String,
    Table,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID

metadata = MetaData()

runs = Table(
    "runs",
    metadata,
    Column("run_id", UUID(as_uuid=True), primary_key=True),
    Column("last_sequence", BigInteger, nullable=False, default=0),
    Column("created_at", DateTime(timezone=True), nullable=False),
)

events = Table(
    "events",
    metadata,
    Column("event_id", UUID(as_uuid=True), primary_key=True),
    Column("run_id", UUID(as_uuid=True), ForeignKey("runs.run_id"), nullable=False),
    Column("sequence_number", BigInteger, nullable=False),
    Column("type", String(80), nullable=False),
    Column("occurred_at", DateTime(timezone=True), nullable=False),
    Column("payload", JSONB, nullable=False),
    UniqueConstraint("run_id", "sequence_number", name="uq_events_run_sequence"),
)

Index("ix_events_run_type", events.c.run_id, events.c.type)

run_usage = Table(
    "run_usage",
    metadata,
    Column("run_id", UUID(as_uuid=True), ForeignKey("runs.run_id"), primary_key=True),
    Column("budget_hash", String(64)),
    Column("used_actions", Integer, nullable=False, default=0),
    Column("used_http_requests", Integer, nullable=False, default=0),
    Column("used_model_calls", Integer, nullable=False, default=0),
    Column("used_tokens", BigInteger, nullable=False, default=0),
    Column("reserved_tokens", BigInteger, nullable=False, default=0),
    Column("used_cost_microusd", BigInteger, nullable=False, default=0),
    Column("reserved_cost_microusd", BigInteger, nullable=False, default=0),
    Column("spawned_workers", Integer, nullable=False, default=0),
    Column("active_workers", Integer, nullable=False, default=0),
    Column("last_dispatch_at", DateTime(timezone=True)),
)

action_reservations = Table(
    "action_reservations",
    metadata,
    Column("run_id", UUID(as_uuid=True), ForeignKey("runs.run_id"), primary_key=True),
    Column("action_id", UUID(as_uuid=True), primary_key=True),
    Column("fingerprint", String(64), nullable=False),
    Column("worker_id", UUID(as_uuid=True)),
    Column("task_id", UUID(as_uuid=True)),
    Column("released_at", DateTime(timezone=True)),
)
Index(
    "uq_action_active_fingerprint",
    action_reservations.c.run_id,
    action_reservations.c.fingerprint,
    unique=True,
    postgresql_where=action_reservations.c.released_at.is_(None),
)

model_reservations = Table(
    "model_reservations",
    metadata,
    Column("run_id", UUID(as_uuid=True), ForeignKey("runs.run_id"), primary_key=True),
    Column("call_id", UUID(as_uuid=True), primary_key=True),
    Column("worker_id", UUID(as_uuid=True)),
    Column("task_id", UUID(as_uuid=True)),
    Column("reserved_tokens", BigInteger, nullable=False),
    Column("reserved_input_tokens", BigInteger),
    Column("reserved_output_tokens", BigInteger),
    Column("request_bytes", BigInteger),
    Column("reserved_cost_microusd", BigInteger, nullable=False),
    Column("settled_at", DateTime(timezone=True)),
)

worker_slots = Table(
    "worker_slots",
    metadata,
    Column("run_id", UUID(as_uuid=True), ForeignKey("runs.run_id"), primary_key=True),
    Column("worker_id", UUID(as_uuid=True), primary_key=True),
    Column("task_id", UUID(as_uuid=True), nullable=False),
    Column("status", String(24), nullable=False),
    Column("lease_expires_at", DateTime(timezone=True)),
    Column("last_heartbeat_at", DateTime(timezone=True)),
)

worker_budget_accounts = Table(
    "worker_budget_accounts",
    metadata,
    Column("run_id", UUID(as_uuid=True), ForeignKey("runs.run_id"), primary_key=True),
    Column("worker_id", UUID(as_uuid=True), primary_key=True),
    Column("task_id", UUID(as_uuid=True), nullable=False),
    Column("elastic", Boolean, nullable=False, default=False),
    Column("kind", String(32)),
    Column("token_cap", BigInteger),
    Column("model_call_cap", Integer),
    Column("token_limit", BigInteger, nullable=False),
    Column("model_call_limit", Integer, nullable=False),
    Column("action_limit", Integer, nullable=False),
    Column("http_limit", Integer, nullable=False),
    Column("cost_limit_microusd", BigInteger),
    Column("used_tokens", BigInteger, nullable=False, default=0),
    Column("reserved_tokens", BigInteger, nullable=False, default=0),
    Column("used_model_calls", Integer, nullable=False, default=0),
    Column("used_actions", Integer, nullable=False, default=0),
    Column("used_http_requests", Integer, nullable=False, default=0),
    Column("used_cost_microusd", BigInteger, nullable=False, default=0),
    Column("reserved_cost_microusd", BigInteger, nullable=False, default=0),
)
Index(
    "uq_elastic_task_kind",
    worker_budget_accounts.c.run_id,
    worker_budget_accounts.c.kind,
    unique=True,
    postgresql_where=worker_budget_accounts.c.elastic.is_(True),
)
