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

## Partially implemented

- The gateway enforces action and HTTP request counts. Token, cost, concurrency, and wall
  time ceilings need the experiment controller in later milestones.
- Event export, projections beyond run sequencing, and crash reconciliation are pending.
- Rate limit state is in memory and suitable for a single controller process. Durable,
  distributed reservations are pending.

## Next milestone

Milestone 3: scripted oracle agent, deterministic finding validator, and evaluator using
the same gateway and evidence contracts as later model agents.

## Known architectural debt

- Browser traffic and arbitrary agent network access require a separate mediated design.
- Persistent experiment run manifest and managed artifact store are still pending; the
  range currently uses local instance manifests and evidence files.
- Build and instance operations assume one trusted local controller; concurrent controllers
  and crash recovery need coordination and reconciliation.
- The first SaaS API is one service with an in-process SQLite fixture; service-level
  distribution and structural scenario mutation remain future range work.

## Failing tests

None known. The local suite includes a disposable PostgreSQL integration test and a real
Docker range test; hosted CI status is checked on each pushed commit.
