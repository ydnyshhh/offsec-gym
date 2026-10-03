# OffSecGym

**A testbed for measuring how autonomous security agents find and report vulnerabilities.**

OffSecGym starts an isolated, synthetic web application. An agent explores it
through a controlled request gateway. An independent validator checks the
agent's findings against hidden ground truth. The platform records actions,
model calls, evidence, and resource use so researchers can compare agent
designs on the same task.

The central question is: **with the same model, target, visibility, and compute
budget, how does the agent's memory or orchestration change what it finds?**

## What an experiment looks like

The main range is a fictional multi-tenant SaaS application. Its vulnerable
version contains five testable flaws: cross-tenant document, invoice, and
support-ticket reads; public invoice metadata exposure; and a refund action
allowed to a workspace member. A patched sibling uses the same public fixture
with those flaws removed.

For example, an agent might request an invoice while acting as a user from
another workspace. OffSecGym records the exact request and response. If the
agent submits a finding, the validator checks its cited evidence, the
user–invoice relationship, and the hidden policy. The patched sibling tests
whether the agent makes unsupported claims when the flaw is absent.

```mermaid
flowchart LR
    C[Experiment config] --> R[Synthetic range]
    C --> A[Agent]
    A --> G[Audited HTTP gateway] --> R
    G --> E[Action evidence] --> V[Independent validator]
    A --> F[Candidate findings] --> V
    O[Hidden oracle] --> V --> S[Score]
    G --> L[(Event log)]
    A --> L
    V --> L
```

The agent cannot read the hidden oracle. It can reach only explicitly
provisioned synthetic targets through the gateway. Range instances use
isolated Docker networks, and the gateway enforces route and resource limits.
See the [safety boundary](docs/safety.md).

## What you can compare

| Agent setup | What it tests |
| --- | --- |
| Scripted solver | Checks that the range, gateway, validator, and scoring path work end to end. |
| Monolithic agent with transcript memory | Gives one model the conversation history as it explores. |
| Monolithic agent with structured memory | Stores facts and checked actions in an event-backed worldview and retrieves relevant state. |
| Coordinator with ephemeral workers | Splits the task into bounded objectives and measures sequential, parallel, and budget-aware scheduling. |

PostgreSQL preserves typed events, model usage, findings, validation results,
and terminal status. Restricted request/response artifacts live in the local
state directory; events reference their hashes. Scoring counts **distinct
validated root causes**, rejected findings, missed roots, and duplicate
submissions separately. Vulnerable and patched builds share a seed and public
fixture, so patched runs also expose false findings. See
[architecture](docs/architecture.md) and [evaluation rules](docs/evaluation.md).

## Try it locally

You need Python 3.12–3.14, uv, and Docker for range commands. These commands
install dependencies and validate a spec without starting containers:

```sh
uv sync --extra dev
uv run offsecgym spec validate examples/saas-range.yaml
```

Start the synthetic SaaS range. Use the instance ID printed by `range start`
to inspect and later destroy the instance:

```sh
uv run offsecgym range start examples/saas-range.yaml --seed 42
uv run offsecgym range metadata INSTANCE_ID
uv run offsecgym range destroy INSTANCE_ID
```

To run the scripted vulnerable/patched experiment, use a dedicated PostgreSQL
database and apply migrations first:

```sh
export OFFSECGYM_DATABASE_URL='postgresql+asyncpg://USER:PASSWORD@HOST/DB'
uv run alembic upgrade head
uv run offsecgym experiment run experiments/configs/scripted-saas.yaml --paired
```

The command prints run IDs and evaluations. Inspect one recorded event stream
with `uv run offsecgym experiment trace RUN_ID`. The scripted vulnerable run
checks the harness end to end; it is not a model-performance result. Model
runs require a provider key and an experiment config. Review its budget and
token-price assumptions before making paid model calls. See the
[monolithic setup](docs/milestone-4.md) and [worker architecture](docs/milestone-6.1.md).

Generated state and restricted evidence live in `.offsecgym/` by default. Set
`OFFSECGYM_STATE_DIR` to use another directory. Use a separate disposable
database for integration tests; see [development](docs/development.md).

## Research record

The completed [M6.4 worker-policy study](docs/diagnostics/m64-confirmatory-results.md)
collected 180 feasible runs across paired ranges and token budgets; 179 were
score valid. At budgets where all three worker policies could run, the sample
did not establish a root-recall winner. Parallel workers finished faster in
the observed sample. Opportunity-aware admission made lower-budget runs
feasible. The trace analysis found many proof-capable actions that never became
submitted findings. These observations motivate the
[M6.5 research design](docs/milestone-6.5.md): a common-bootstrap monolithic
control and a separate prospective witness-to-finding study.

Read the [research plan](docs/research-plan.md) for the next questions,
[implementation status](docs/implementation-status.md) for the milestone
history, and [experiment spec](docs/experiment-spec.md) for configuration
fields. Frozen studies retain their failed gates and unscored provider failures
in the record; their results are not silently rerun or tuned.

OffSecGym is licensed under [Apache-2.0](LICENSE).
