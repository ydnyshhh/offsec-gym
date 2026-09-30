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
- Event-backed WorldState with provenance-checked fact claims, adjudication, contradictions,
  corroboration, coverage, bounded relevance retrieval, and structured-memory model tools.
- Structured monolithic diagnostic config, worldview CLI inspection, and Docker acceptance
  test through the existing gateway, validator, and evaluator.

## Partially implemented

- The gateway enforces action and HTTP request counts. Token, cost, concurrency, and wall
  time ceilings need the experiment controller in later milestones.
- Event export, projections beyond run sequencing, and crash reconciliation are pending.
- Request artifacts can remain unreferenced if event append fails. Add artifact indexing
  and reconciliation before claiming complete crash recovery.
- Rate limit state is in memory and suitable for a single controller process. Durable,
  distributed reservations are pending.
- WorldState currently rebuilds from a full run event scan and serializes claims only
  within one controller process. Indexed projections, atomic cross-controller coverage
  reservations, and worker leases remain Milestone 6 work.
- A managed artifact store and full experiment run manifests remain future controller work.
- The model-turn protocol still uses OpenAI Responses item shapes. Normalize model output
  and opaque continuation state before adding a second provider.

## Next milestone

Complete live transcript and structured-memory diagnostic batches after configuring an
API credential and available model. Inspect 10–20 runs per condition for harness
failures before research comparisons. Then begin Milestone 6 worker coordination.

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
- The gateway holds a run-wide lock and scans the complete run event history before every
  action. Replace this O(N²) accounting path with durable atomic budget reservations and
  projections before multi-agent experiments.
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
