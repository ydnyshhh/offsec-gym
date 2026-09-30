# Event stream and action provenance

`TraceEvent` is append-only, typed, and versioned. `event_id` is stable; `run_id` scopes the
stream; `sequence_number` is assigned transactionally by the store; `occurred_at` requires
a timezone. `actor`, `correlation_id`, and `causation_id` connect commands and outcomes.
Sequence number zero is reserved for an event draft and must not be persisted.

Initial event types: `run_started`, `range_started`, `action_requested`,
`action_blocked`, `action_completed`, `action_failed`, `budget_updated`, and
`run_completed`, `finding_submitted`, and `finding_validated`. Later event versions cover
worker assignments, model calls, and world-fact adjudication. Every event payload is
parsed through a discriminated union; unknown types
or unsupported schema versions are rejected. Legacy v1 action requests and range-start
events remain parseable when explicitly marked v1. New construction defaults to v2 for
`RangeStarted` and `ActionRequested`. New v2 `RangeStarted` requires a build ID, instance
ID, and generation; `range_id` is only a legacy v1 field. Future reset, stop, and destroy
events must use the same explicit instance/generation vocabulary.
New v2 `ActionRequested` events require a request artifact ID, instance ID, and generation;
they also record the worker ID when present. Their path and body fields are hashes, not raw
secrets. Each action admitted with an existing instance and current generation produces an
instance-scoped, mode-0600 request artifact even when policy blocks it. Invalid instance
contexts and stale generations are rejected before any instance artifact is created.
Response evidence is a separate mode-0600 artifact linked to that
request ID and generation. Legacy events lack this provenance and cannot establish a
generation-aware validation result.

The controller accepts agent-authored `FindingProposal` values and persists
`FindingSubmitted` only after binding trusted run, instance, generation, and finding IDs.
The event schema requires the event run ID to match the embedded finding. Mutable-claim
validation creates a separate replay run with its own start, range-start, action, and
completion events. `ValidationResult.replay_trace` links that run and its evidence to the
parent finding verdict.

Model turns add `ModelCallStarted`, `ModelCallCompleted`, and `ModelCallFailed`. A call ID
links start to completion or failure. Completion records provider response ID, input and
output token counts, and estimated cost when token prices are configured. Failure records
a stable reason code and optional HTTP status. Raw prompts, response bodies, and API
credentials are absent from these events. Rejected model tool calls emit
`ModelToolRejected` with a stable call reference, tool name, and error class but no
untrusted argument payload.

Request events precede external effects. An allowed action receives exactly one terminal
completion, blocked, failed, or outcome-unknown resolution. JSONL is an export of the
authoritative PostgreSQL stream. Projections are rebuildable from that stream. The current
gateway reads the full stream before each action for budgets and duplicates; this requires
an indexed projection and atomic reservations before multi-agent runs.
