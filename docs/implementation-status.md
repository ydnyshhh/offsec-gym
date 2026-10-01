# Implementation status

## Implemented

- Python package, lockfile, schema and interface foundations, CLI spec validation.
- Typed initial trace events and PostgreSQL append/read store with first migration.
- Architecture, safety, schema, validation, evaluation, and research documents.
- Deterministic `hello/health_check` Compose bundle; isolated, resource-limited instance
  lifecycle with health checks, ownership labels, reset, and teardown.
- Audited GET-only HTTP gateway with destination and path policy, action/request budgets,
  local rate limit, response evidence, and blocked/completed/failed events.
- Docker acceptance tests for external egress, cross-instance isolation, redirect behavior,
  lifecycle, and cleanup.
- Seeded AcmeCloud SaaS API with login, workspace membership, documents, invoices, refunds,
  support tickets, and role boundaries.
- Five paired security properties across vulnerable and patched builds. Public fixtures
  match exactly; hidden ground truth and attack graph live outside the Docker build.
- Per-instance credentials, public identity roster, identity-scoped gateway actions, and
  live oracle tests for intended weaknesses and non-vulnerable decoys.
- Typed versioned ground-truth and attack-graph manifests with stable UUIDv5 IDs and
  authorization, field-exposure, and state-transition expectations.
- Explicit build and instance APIs; generation-bound action events, request artifacts, and
  response evidence; build and hidden-oracle SHA-256 integrity checks; bounded recursive
  JSON action bodies.
- Per-property selective SaaS patch mode, concurrent target requests with safe SQLite
  access, and separate controller and agent-visible metadata contracts.
- Contract tests across six seeds and real Docker tests for selective patches, concurrent
  requests, request redaction, provenance, and lifecycle generations.
- Explicit v2 range-start events, effective selective-variant controller metadata,
  build-bound oracle lookup, semantic proof requirements, and classified gateway failures.
- Event-backed scripted SaaS solver that discovers targets through the same gateway and
  agent-visible context used by future agents; it has no access to hidden oracle files.
- Provenance-first deterministic validator with typed proof handlers and fresh-instance
  replay for the refund state transition.
- Root-cause-deduplicated evaluator, explicit score validity, and paired scripted
  experiment CLI with PostgreSQL event persistence.
- Semantic witness validation across fixture identities and assets; controller-bound
  finding submissions; replay run lifecycle and trace references; terminal run outcomes
  for agent, budget, infrastructure, and validation failures.
- Selectively patched scripted experiment acceptance test and canonical experiment hash.
- Initial model-turn contract, OpenAI Responses adapter, strict typed model
  tools, monolithic agent, model usage events, and provider/cancellation score states.
- Fake-provider acceptance test through a live Docker SaaS range and deterministic
  validation; CLI support for 1–20 diagnostic repetitions.
- Restricted per-call model request/response artifacts with event hashes and replay
  verification; explicit output-token cap and known-routes diagnostic visibility factor.
- Explicit model-call budget, bounded and redacted provider HTTP-error artifacts, and
  run-bound v3 validation results/events with legacy v2 read support.
- Event-backed WorldState with v4 evidence-link labels that do not claim semantic truth,
  provenance-checked claims, contradiction links, controller-only supersession, coverage,
  trust-ranked deduplicated retrieval, and structured-memory model tools.
- Structured monolithic diagnostic config, worldview CLI inspection, and Docker acceptance
  test through the existing gateway, validator, and evaluator.
- Controller-extracted identity/object facts, canonical finding categories, bounded
  document/ticket excerpts, a recent entity/evidence working set, and exact `get_entity`
  lookup. The M5.3 three-run diagnostic is recorded separately.
- Pinned checked-identity facts, a 32-entry exact request fingerprint index,
  compact checked-action rendering, and a combined automatic-context/world-tool
  carryover ceiling. Both M5.4 three-run smoke batches are recorded.
- M5.4 frozen comparison baseline at `0f27533`, with both failed gates and
  accepted limitations kept in the corrected diagnostic.
- PostgreSQL run transactions for shared WorldState writes and atomic coverage
  claims; persistent action-fingerprint reservations, action/HTTP/model/token/cost
  accounting, a declared immutable global budget, worker slots, and
  reconstructable controller lifecycle events.
- Concurrent fake-worker PostgreSQL acceptance tests across independent event
  store instances. They verify unique active reservations, bounded action/model/
  worker counts, and reconstruction from the event stream.
- M6.1 fixed sequential worker decomposition, bounded event-recorded packets,
  typed debriefs, worker-attributed findings, matched Kimi control/worker configs,
  and trace-derived orchestration metrics. Fake-provider PostgreSQL and Docker
  tests verify the lifecycle and one validated cross-worker state handoff.
- The matched M6.1 live diagnostic is recorded for three monolithic controls
  and three sequential-worker runs on the current atomic controller. All six
  traces scored validly, stayed within global budgets, and replayed controller
  counters. Worker runs had earlier first valid findings but lower root-cause
  recall and weak late-worker exploration; see the diagnostic for exact traces.
- M6.1.1 introduces protected future-worker model reservations, rolling
  spendable budgets, a smaller packet-aware worker context, objective-aware
  packet ranking, checked POST body hashes/previews, and coordinator-owned
  coverage. Its three-run smoke gates are predeclared separately.
- The M6.1.1 three-run smoke passed six-worker model-call opportunity,
  input-size, and controller integrity gates, but missed objective-specific
  HTTP action, identity/workspace packet relevance, and refund exploration
  gates. The exact failures remain in its diagnostic.

## Partially implemented

- Model token and cost reservations use a conservative request-size preflight
  estimate, now calibrated as configurable bytes per token plus a margin and
  recorded with split input/output estimates and signed errors. They settle to
  provider-reported usage. Hidden tokenizer overhead or
  a provider exceeding its output cap can still make the actual use exceed a
  preflight reservation. Wall time remains enforced at the experiment runner.
- Event export and crash reconciliation are pending. The M6 controller accounting
  and ownership state has a replay projection.
- Request artifacts can remain unreferenced if event append fails. Add artifact indexing
  and reconciliation before claiming complete crash recovery.
- WorldState still rebuilds facts and coverage from a full run event scan, but
  PostgreSQL writes now serialize under the run row lock. Indexed projections
  remain future work.
- Action and coverage reservations need controller crash reconciliation. An
  interrupted owner can leave an active reservation until explicit recovery.
- Model-authored evidence links are not semantic entailment. Mechanically verified
  response-field observations and a controlled memory-only comparison remain research work.
- A managed artifact store and full experiment run manifests remain future controller work.
- The model-turn protocol still uses OpenAI Responses item shapes. Normalize model output
  and opaque continuation state before adding a second provider.

## Next milestone

Correct identity/workspace packet selection and ticket route discovery in a
separately versioned follow-up, then inspect retrieval-only worker turns.
The [M6.1.1 diagnostic](diagnostics/kimi-k3-openrouter-m611.md) has failed
mechanistic gates, so sequential orchestration is not frozen for M6.2. The
original [M6.1 diagnostic](diagnostics/kimi-k3-openrouter-m61.md) remains frozen.

## Known architectural debt

- Browser traffic and arbitrary agent network access require a separate mediated design.
- Persistent experiment run manifest and managed artifact store are still pending; the
  range currently uses local instance manifests and evidence files.
- Historical offline revalidation after a reset needs immutable run-to-build/generation
  bindings rather than the current mutable instance manifest.
- The live scripted range test uses an in-memory event store; PostgreSQL event persistence
  is tested separately. A full Docker plus PostgreSQL path remains future acceptance work.
- Build and instance operations assume one trusted local controller; concurrent controllers
  and crash recovery need coordination and reconciliation.
- The range runtime still uses a process-local instance guard around gateway
  dispatch and reset. M6.2 needs a cross-process reset/dispatch barrier before
  concurrent workers share a live instance.
- Existing Milestone 2 generated `.offsecgym` manifests require a fresh state directory
  and rebuild after stopping/destroying old live instances.
- The first SaaS API is one service with an in-process SQLite fixture; service-level
  distribution and structural scenario mutation remain future range work.
- The OpenAI adapter uses a synchronous request in a worker thread. Coroutine
  cancellation does not stop an in-flight HTTP request; use a cancellable async client
  before strict wall-time or cost comparisons. Cache and reasoning usage subcounts remain
  in raw response artifacts but are not projected into summary metrics.

## Failing tests

None known. The local suite includes a disposable PostgreSQL integration test and a real
Docker range test; hosted CI status is checked on each pushed commit.
