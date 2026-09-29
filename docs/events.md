# Event stream and action provenance

`TraceEvent` is append-only, typed, and versioned. `event_id` is stable; `run_id` scopes the
stream; `sequence_number` is assigned transactionally by the store; `occurred_at` requires
a timezone. `actor`, `correlation_id`, and `causation_id` connect commands and outcomes.
Sequence number zero is reserved for an event draft and must not be persisted.

Initial event types: `run_started`, `range_started`, `action_requested`,
`action_blocked`, `action_completed`, `action_failed`, `budget_updated`, and
`run_completed`. Later event versions cover worker assignments, model calls,
world-fact adjudication, findings, and
validation. Every event payload is parsed through a discriminated union; unknown types
or unsupported schema versions are rejected. Legacy v1 action requests and range-start
events remain parseable. New v2 `RangeStarted` requires a build ID, instance ID, and
generation; `range_id` is only a legacy v1 field. Future reset, stop, and destroy events
must use the same explicit instance/generation vocabulary.
New v2 `ActionRequested` events require a request artifact ID, instance ID, and generation;
they also record the worker ID when present. Their path and body fields are hashes, not raw
secrets. Each action admitted with an existing instance and current generation produces an
instance-scoped, mode-0600 request artifact even when policy blocks it. Invalid instance
contexts and stale generations are rejected before any instance artifact is created.
Response evidence is a separate mode-0600 artifact linked to that
request ID and generation. Legacy events lack this provenance and cannot establish a
generation-aware validation result.

Request events precede external effects. An allowed action receives exactly one terminal
completion, blocked, failed, or outcome-unknown resolution. JSONL is an export of the
authoritative PostgreSQL stream. Projections are rebuildable from that stream. The current
gateway reads the full stream before each action for budgets and duplicates; this requires
an indexed projection and atomic reservations before multi-agent runs.
