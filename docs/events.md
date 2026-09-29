# Event stream v1

`TraceEvent` is append-only, typed, and versioned. `event_id` is stable; `run_id` scopes the
stream; `sequence_number` is assigned transactionally by the store; `occurred_at` requires
a timezone. `actor`, `correlation_id`, and `causation_id` connect commands and outcomes.
Sequence number zero is reserved for an event draft and must not be persisted.

Milestone 0 event types: `run_started`, `range_started`, `action_requested`,
`action_blocked`, `action_completed`, `budget_updated`, and `run_completed`. Later event
versions cover worker assignments, model calls, world-fact adjudication, findings, and
validation. Every event payload is parsed through a discriminated union; unknown types
or schema versions are rejected.

Request events precede external effects. An allowed action receives exactly one terminal
completion, blocked, failed, or outcome-unknown resolution. JSONL is an export of the
authoritative PostgreSQL stream. Projections are rebuildable from that stream.
