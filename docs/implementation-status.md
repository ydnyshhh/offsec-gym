# Implementation status

## Implemented

- Python package, lockfile, schema and interface foundations, CLI spec validation.
- Typed initial trace events and PostgreSQL append/read store with first migration.
- Architecture, safety, schema, validation, evaluation, and research documents.

## Partially implemented

- Budget is a validated input contract; runtime reservation/accounting is pending.
- Range lifecycle commands exist as explicit Milestone 1 placeholders.
- Event export, projections beyond run sequencing, and crash reconciliation are pending.

## Next milestone

Milestone 1: contained Compose hello range, functional HTTP ActionGateway, lifecycle
commands, health checks, containment tests, and deterministic teardown.

## Known architectural debt

- Gateway/browser implementation and hidden-oracle access control must be proven by tests.
- Persistent run manifest and artifact storage are designed but not implemented.
- Hosted CI behavior is unverified until the workflow runs on a pushed commit.

## Failing tests

None known. The local suite passed with a disposable PostgreSQL container (10 tests).
