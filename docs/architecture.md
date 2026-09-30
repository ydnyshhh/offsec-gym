# Architecture

## Purpose and boundaries

OffSecGym measures complete autonomous-agent systems in synthetic ranges. The environment,
action boundary, hidden oracle, trace, and evaluator are research infrastructure. Models and
orchestrators are replaceable experimental inputs.

The controller owns experiment lifecycle and resource accounting. The range compiler owns
fixtures, ground truth, and paired vulnerable/patched builds. The range runtime owns only
provisioning and teardown. The ActionGateway is the sole target-facing tool executor. Agents
submit observations and candidate findings, but cannot read hidden ground truth or mark their
own findings validated. The validator and evaluator have separate privileged access to the
oracle. Each subsystem exchanges versioned typed contracts from `offsecgym.schemas`.

```text
ExperimentSpec -> controller -> RangeRuntime -> isolated range
                        |                 ^
                        v                 |
                   Orchestrator -> Agent -> ActionGateway
                        |                 |
                        v                 v
                    WorldState        event stream
                        |                 |
                        +----> validator -> evaluator -> exports
                                  ^
                            hidden ground truth
```

## First deliverable and dependencies

Milestone 0 provides contracts, event persistence, docs, CLI validation, migrations, and CI.
Milestone 1 supplies a contained hello range and functional gateway. Milestone 2 adds the
first SaaS security-property pairs; Milestone 3 proves them with a scripted solver and evaluator before
LLM-based experiments.

The concrete Milestone 1 runtime design is in [runtime.md](runtime.md).
The first SaaS range design is in [saas-range.md](saas-range.md).

Python 3.12 is the baseline. Pydantic validates versioned external input; Typer supplies a
discoverable CLI; SQLAlchemy with asyncpg persists events; Alembic versions the schema;
PyYAML reads declarative specs. Redis, Kubernetes, an LLM framework, and a dashboard are not
required for these milestones. Dependencies are locked in `uv.lock`.

## Control database

The `events` table is the authoritative history. `runs` currently serializes each run's
sequence numbers; it is not a substitute for events. `PostgresEventStore.append` locks the
run row, allocates a monotonically increasing sequence, and writes the event in one database
transaction. Reusing an event ID returns the existing event; future projections must update
in the same transaction and be rebuildable from events.

Current tables:

| Table | Key | Purpose |
| --- | --- | --- |
| `runs` | `run_id` | Per-run sequence allocation and creation timestamp |
| `events` | `event_id`; unique `(run_id, sequence_number)` | Typed, versioned payload and ordered history |

Later migrations add `range_instances`, `actions`, `artifacts`, `budgets`, `workers`,
`worker_tasks`, `model_calls`, `world_facts`, `hypotheses`, `coverage_claims`, `findings`,
and `validation_runs`. IDs, lifecycle states, and timestamps use relational columns;
versioned extensions use bounded JSONB. Large HTTP evidence and screenshots live in a
per-run artifact store, referenced by digest and evidence ID. Credentials never appear in
events or exported manifests.

## Key contracts

`RangeSpec` specifies the synthetic range, seed, topology, identities, and security-property
variants. `ExperimentSpec` binds a range to a model, orchestrator, memory and validation
policy, budget, and replication seed. `Budget` contains hard resource ceilings.
`ActionRequest` and `ActionResult` describe a target action and gateway decision. New
request artifacts and response evidence bind an action to an instance generation.
`TraceEvent` includes schema version, stable ID, run ID, actor, timezone-aware timestamp,
correlation/causation IDs, and store-assigned sequence number. `CandidateFinding` states a
typed expectation and references exact actions and evidence without hidden oracle IDs.
`ValidationResult` records a separate verdict and may cite a canonical property/root-cause
UUID. Controller metadata and agent-visible range context have separate types; the default
agent context does not contain an account roster.

The public protocols are `RangeRuntime`, `ActionGateway`, `Agent`, `Orchestrator`,
`Validator`, `WorldState`, and `EventStore`. The contract is intentionally small; each
concrete implementation belongs to the milestone that can test it end to end.

## Lifecycles

**Experiment:** validate and freeze specs; record code, config, and image digests; reserve a
run namespace; start and health-check the range; launch orchestrator; record every action and
resource update; validate findings; evaluate; export; stop and destroy the range; emit a
terminal run event. Cleanup runs after success, cancellation, and failure.

**Worker:** receive a bounded task and lease; retrieve relevant worldview facts; reserve
budget; invoke model and tools; submit structured observations, hypotheses, and findings;
return a structured debrief; release lease. Accidental duplication and deliberate
independent verification are tagged separately.

**Finding:** submit candidate with evidence IDs; check schema and provenance; verify the
claimed subject/object relationship and expected policy; replay a minimal proof in a reset
or cloned range when feasible; match hidden oracle; record validated, rejected, or
inconclusive. A validator outage cannot turn a candidate into a false positive.

## Failure and recovery

Terminal states are `completed`, `budget_exhausted`, `agent_failed`,
`environment_failed`, `provider_failed`, `validation_failed`, and `cancelled`. All remain
in analysis exports. Provider and infrastructure retries are bounded and recorded. Agent
decisions are not silently retried. An action with a request event and no completion after a
crash is outcome-unknown; it may be replayed only if known idempotent. Teardown uses
run-owned Compose labels and is idempotent where possible.

Milestone 4 must extend `RunEvaluation.status` to cover `provider_failed` and `cancelled`
before running model comparisons. A provider outage, 5xx response, or authentication
failure ends as `provider_failed` with `score_valid=false`. A model's malformed tool call is
an agent outcome: classify an unrecovered failure as `agent_failed` and score it, or let a
recovered run complete and score its actual findings. Controller-initiated cancellation
ends as `cancelled` with `score_valid=false`. Keep these terminal states distinct in event
streams, evaluations, and exports.

## Reproducibility

The immutable run manifest will bind Git revision, spec hashes, scenario seed, run nonce,
image digests, model/provider versions when available, prompt/tool/validator versions,
timestamps, and usage. Scenario data are deterministic under a seed; run credentials are
unique and stored separately. External model APIs may prevent bit-for-bit replay, so traces
and versions preserve what was actually observed.
