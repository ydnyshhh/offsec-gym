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
New v3 `ValidationResult` records carry their parent run ID. A v3 `FindingValidated`
event requires that ID to match the event run, and scoring checks that the result and
candidate finding belong to the same run. Historical v2 results and events remain
parseable but lack this binding. Referenced-finding existence is not yet checked by the
event store.

Model turns add `ModelCallStarted`, `ModelCallCompleted`, and `ModelCallFailed`. A call ID
links start to completion or failure. Completion records provider response ID, input and
output token counts, and estimated cost when token prices are configured. Failure records
a stable reason code and optional HTTP status. Bounded JSON error bodies from HTTP
failures are saved as response artifacts when available, with the API key redacted.
New v2 start/completion events also bind
request/response artifact IDs and SHA-256 digests. The controller writes the exact provider
JSON request body and returned response under `model_calls/<run_id>/<call_id>/`, with
private directories and mode-0600 files. `ModelCallArtifacts.read_verified` checks the
record's run, call, artifact ID, and payload digest. The request artifact excludes the
Authorization header and API key. Raw prompts, response bodies, and API credentials are
absent from events; v1 model events remain readable but do not have replayable model turns.
Rejected model tool calls emit
`ModelToolRejected` with a stable call reference, tool name, and error class but no
untrusted argument payload. Its exact arguments are retained in the restricted response
artifact.
If the event-store append fails after a request artifact is written, that artifact can
remain without an event reference. The run/call path makes it discoverable; durable
artifact indexing and orphan reconciliation remain future controller work.

Milestone 5 adds `WorldFactSubmitted`, `WorldFactAdjudicated`, `CoverageClaimed`,
`CoverageUpdated`, and `ContextRetrieved`. New submissions bind a v4 `hypothesized`
fact to its run; historical v3 submissions remain parseable. V4 adjudication uses
`evidence_linked` and `multi_evidence_linked` to distinguish genuine citations from
semantic validation. One adjudication event can change several related fact states
atomically in the stream, preserving contradiction decisions. Legacy `observed` and
`corroborated` statuses retain their original event meaning as evidence-link labels.
Coverage events record ownership and lifecycle. Context retrieval records the selected
fact IDs and hashes of the query selector and rendered context; the model request
artifact retains the exact text supplied to the provider.

Request events precede external effects. An allowed action receives exactly one terminal
completion, blocked, failed, or outcome-unknown resolution. JSONL is an export of the
authoritative PostgreSQL stream. Projections are rebuildable from that stream. The current
gateway reads the full stream before each action for budgets and duplicates; this requires
an indexed projection and atomic reservations before multi-agent runs.
