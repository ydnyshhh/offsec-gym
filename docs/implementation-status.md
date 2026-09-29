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

## Partially implemented

- The gateway enforces action and HTTP request counts. Token, cost, concurrency, and wall
  time ceilings need the experiment controller in later milestones.
- Event export, projections beyond run sequencing, and crash reconciliation are pending.
- Rate limit state is in memory and suitable for a single controller process. Durable,
  distributed reservations are pending.

## Next milestone

Milestone 2: first synthetic security-property range with vulnerable and patched
variants, explicit identities, and a hidden oracle.

## Known architectural debt

- Browser traffic and arbitrary agent network access require a separate mediated design.
- Persistent experiment run manifest and managed artifact store are still pending; the
  range currently uses local instance manifests and evidence files.
- Build and instance operations assume one trusted local controller; concurrent controllers
  and crash recovery need coordination and reconciliation.

## Failing tests

None known. The local suite includes a disposable PostgreSQL integration test and a real
Docker range test; hosted CI status is checked on each pushed commit.
