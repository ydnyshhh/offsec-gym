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
- M6.1.2 corrects identity selection toward distinct workspace memberships
  and exposes the existing ticket-list route in known-routes instructions.
  Its three-run verification gates are declared before execution.
- The M6.1.2 three-run smoke retained distinct identity/workspace links and
  discovered ticket entities in all runs, with no controller leaks. It still
  missed relevant HTTP actions and refund POSTs; one run had no finding.
- M6.1.3 froze the strict worker objective contract and its three-run
  diagnostic, including failed action-or-block gates.
- M6.2.0 added source-action-ordered controller facts, cross-process local
  range dispatch/reset coordination, stale worker lease recovery, and typed
  tool rejection reasons before parallel live runs.
- M6.2.1–2 froze matched sequential/parallel and bootstrapped worker
  diagnostics with their observed coverage failures.
- M6.2.3 froze the hard-partition diagnostic. Its replay and ownership gates
  passed, but fixed 20,000-token slices blocked second turns.
- M6.3.0 added elastic worker grants. Its single smoke funded multi-turn
  identity, invoice, and refund workers, but public, document, and ticket
  were denied viable trajectories; those failed gates remain frozen.
- M6.3.1 added state-based task dependencies and atomic, opportunity-aware
  admission holds. Its one smoke funded invoice, refund, public, and document
  without displacement or accounting drift. It ended `agent_failed` from
  worker behavior; the exact trace and all scheduler gates are frozen in
  [the diagnostic](diagnostics/kimi-k3-openrouter-m631.md).
- M6.4.0 adds a versioned `tenant_boundary_v2` range with varied workspace
  assignments, object placement, document/ticket wording, and decoy clues.
  Ten held-out vulnerable/fully patched pairs passed live Docker scripted
  oracle and validator checks, with five validated roots versus zero findings.
- M6.4.1–2 adds objective readiness/admission/execution metrics and a
  deterministic 300-cell worker-policy matrix. It marks 120 low-budget fixed
  worker cells structurally infeasible, leaving 180 feasible live cells.
  Twelve fake-provider worker runs passed across three arms, two patch states,
  and both feasible budget endpoints. Predeclared paired analysis checks
  complete samples, invalid scores, model revisions, and separate common
  feasible versus policy opportunity curves. Completed model-call events now
  record selected OpenRouter endpoints when response metadata supplies them.
  A bounded vulnerable/patched live pilot on non-held-out v2 seed 1101
  verified selected endpoint attribution, paired bootstrap, score validity,
  and clean event replay. The approved confirmatory collection completed all
  180 feasible cells, with 179 score-valid outcomes and one recorded provider
  failure. The [results report](diagnostics/m64-confirmatory-results.md)
  preserves paired uncertainty, patched false findings, objective stages,
  and limits on interpretation.

## Partially implemented

- Model token and cost reservations use a conservative request-size preflight
  estimate, now calibrated as configurable bytes per token plus a margin and
  recorded with split input/output estimates and signed errors. They settle to
  provider-reported usage. Hidden tokenizer overhead or
  a provider exceeding its output cap can still make the actual use exceed a
  preflight reservation. Wall time remains enforced at the experiment runner.
- Managed event export and non-worker crash reconciliation remain pending.
  The M6 controller accounting and ownership state has a replay projection.
- Request artifacts can remain unreferenced if event append fails. Add artifact indexing
  and reconciliation before claiming complete crash recovery.
- WorldState still rebuilds facts and coverage from a full run event scan, but
  PostgreSQL writes now serialize under the run row lock. Indexed projections
  remain future work.
- Worker-owned action, model, and coverage reservations have lease-based crash
  reconciliation in [M6.2.0](milestone-6.2.0.md). Generic non-worker
  reservations still need an ownership and expiry policy.
- Model-authored evidence links are not semantic entailment. Mechanically verified
  response-field observations and a controlled memory-only comparison remain research work.
- A managed artifact store and full experiment run manifests remain future controller work.
- The model-turn protocol still uses OpenAI Responses item shapes. Normalize model output
  and opaque continuation state before adding a second provider.

## Next milestone

M6.4 is closed at `50c3374`; its [results](diagnostics/m64-confirmatory-results.md)
and single unscored provider failure remain frozen. The common-budget recall
comparison did not establish a winner. The post-collection ledger instead
locates frequent action-proof-to-finding losses, especially for document,
invoice, and ticket roots, while the refund root also fails earlier stages.
Do not rerun or retune M6.4 to improve these observations.

The next [M6.5 research design](milestone-6.5.md) separates a common-bootstrap
monolithic control from a prospective witness-to-finding study on new seeds.
The [common-bootstrap control preflight](diagnostics/m65-monolithic-preflight.md)
now has a tested runner, 100-cell pinned manifest, fail-closed collector, and
predeclared historical analysis. It has not made paid model calls. The
witness-to-finding study remains a separate prospective protocol on new
seeds. Each live protocol still requires a cost review and collection
decision.

## Known architectural debt

- Browser traffic and arbitrary agent network access require a separate mediated design.
- Persistent experiment run manifest and managed artifact store are still pending; the
  range currently uses local instance manifests and evidence files.
- Historical offline revalidation after a reset needs immutable run-to-build/generation
  bindings rather than the current mutable instance manifest.
- The scripted range acceptance test uses an in-memory event store, while
  worker fake-provider acceptance and live diagnostics use Docker plus
  PostgreSQL. A unified scripted Docker/PostgreSQL acceptance path remains
  future test work.
- The range runtime coordinates dispatch/reset with an OS file lock across
  processes sharing one local state directory. Cross-host coordination is
  not supported.
- The v2 SaaS seeds vary semantic surface details and relationships within
  the same five vulnerability families. They do not test entirely new
  vulnerability configurations or service-level distribution.
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
