# M6.8.1.1 detached persistence qualification

**Result:** The scripted, no-model path persisted normalized synthetic experiment state through the existing PostgreSQL transaction code. The live autonomous-agent treatment remains unqualified.

The canonical aggregate is [`m681-detached-persistence-qualification.json`](m681-detached-persistence-qualification.json), SHA-256 `690beb372ba06632a4bd983f6ed00f235284698f4fe68ba9f6164fb3c3aa20f4`. The historical aggregate is [`m681-reference-failure-taxonomy.json`](m681-reference-failure-taxonomy.json), SHA-256 `22a037fbf244ae6017f69fed4562b44abf87c6dc534404cd189b76f16719afd3`.

## Observed stages

| Stage | Observation |
| --- | --- |
| Strict control | Canonical UUID accepted; typed wrapper rejected with the same typed-schema error as before, before any event write. |
| Detached normalization | Frozen grammar accepted a supported typed reference; its UUID was unchanged after `StartExperimentArgs` validation. No source or object lookup occurred. |
| State transaction | `ExperimentStateStore.change` wrote authoritative `ExperimentStateChanged` events through `PostgresEventStore.run_transaction`. Five state events were observed in the completed synthetic lifecycle. |
| Later retrieval | A fresh SQLAlchemy engine and `PostgresEventStore` read the same run's events; a separate scripted `get_active_experiment` turn retrieved the unchanged experiment ID, target UUID, plan, and hypothesis before completion. |
| Lifecycle | Zero, one, and three model-declared completed steps were accepted for a three-step plan; completion and separate-run abandonment persisted. Completion wrote no task-verdict event. |
| Isolation and failure | An unrelated run had no experiment events; cross-run mutation failed; terminal mutation, duplicate active start, invalid progress, and rejected syntax created no extra state event. Concurrent duplicate starts serialized with one success and one existing-active conflict. |

Two equivalent clean synthetic rounds produced byte-identical canonical scientific projections, SHA-256 `4c5f3ce240736a84f13192d9e0375f99e9cb5085aea69891e92a427ed0e797f0`. The projection excludes generated run IDs, experiment IDs, event IDs, causation IDs, and timestamps; it retains actual state transitions, sequence numbers, target UUIDs, and planned steps. Real event integrity hashes were recorded separately in an ignored mode-0600 private file. No event identity was changed to obtain deterministic output.

Verification: 844 unit tests passed with the pinned private input mounted; two isolated PostgreSQL integration tests passed; repository-wide Ruff lint and format checks passed. The PostgreSQL database was a disposable local test container named `offsecgym-m6811-pg` with the repository migrations applied. No provider, controller, gateway, range, or external target was used.

These observations establish syntactic validation and detached persistence only. They do not establish public provenance, object correspondence, correct target choice, useful planning, task success, live agent behavior, or a prospective model effect.
