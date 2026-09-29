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

## Partially implemented

- The gateway enforces action and HTTP request counts. Token, cost, concurrency, and wall
  time ceilings need the experiment controller in later milestones.
- Event export, projections beyond run sequencing, and crash reconciliation are pending.
- Rate limit state is in memory and suitable for a single controller process. Durable,
  distributed reservations are pending.
- `WorldFact` has typed provenance and relationship fields; WorldState storage, retrieval,
  and adjudication are not implemented yet.
- A managed artifact store and full experiment run manifests remain future controller work.

## Next milestone

Milestone 3: scripted oracle agent, deterministic finding validator, and evaluator using
the same gateway and evidence contracts as later model agents.

## Known architectural debt

- Browser traffic and arbitrary agent network access require a separate mediated design.
- Persistent experiment run manifest and managed artifact store are still pending; the
  range currently uses local instance manifests and evidence files.
- Build and instance operations assume one trusted local controller; concurrent controllers
  and crash recovery need coordination and reconciliation.
- The gateway holds a run-wide lock and scans the complete run event history before every
  action. Replace this O(N²) accounting path with durable atomic budget reservations and
  projections before multi-agent experiments.
- Existing Milestone 2 generated `.offsecgym` manifests require a fresh state directory
  and rebuild after stopping/destroying old live instances.
- The first SaaS API is one service with an in-process SQLite fixture; service-level
  distribution and structural scenario mutation remain future range work.

## Failing tests

None known. The local suite includes a disposable PostgreSQL integration test and a real
Docker range test; hosted CI status is checked on each pushed commit.
