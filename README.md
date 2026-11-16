# OffSecGym

OffSecGym is a testbed for measuring how autonomous security agents find, prove, and report vulnerabilities. It starts an isolated practice application with known flaws, lets an agent explore through a controlled HTTP gateway, and checks the agent’s findings against hidden ground truth.

For example, a member of one workspace might be able to read another workspace’s invoice. The agent has to find the behavior and cite the request and response that demonstrate it. A validator independently checks the actor, the invoice’s owner, and the expected access policy. A patched copy starts with the same users and data, so researchers can also see whether the agent reports a flaw after it has been fixed.

**Research question.** With the same model, target, visible information, and compute budget, how do memory, worker scheduling, and reporting tools change what an agent can prove?

[Run a first experiment](#quickstart) · [See the architecture](#how-it-works) · [Explore the ranges](#ranges) · [Read the results](#what-the-experiments-found)

## Quickstart

Start with the scripted SaaS experiment. It exercises the gateway, validator, and scoring path without a model API key.

| What you want to do | What you need |
| --- | --- |
| Install the CLI and validate YAML | Linux, macOS, or WSL2 on Windows, plus Python 3.12–3.14 and [uv](https://docs.astral.sh/uv/) |
| Start a range | A running Docker engine with Docker Compose v2 |
| Run and score an experiment | A dedicated PostgreSQL database. The example below starts one in Docker. |
| Try a live model | A supported model, provider API key, and a model experiment config |

Run the shell commands from the root of the checkout. On Windows, use a WSL2 shell with Docker access and clone into the Linux filesystem, where the runtime can use Unix file locks and permissions.

### 1. Install and validate a spec

```sh
git clone https://github.com/ydnyshhh/offsec-gym.git
cd offsec-gym
uv sync --locked --extra dev
uv run offsecgym spec validate examples/saas-range.yaml
```

Validation checks the YAML without starting containers. To check that Docker is ready to run a range, use `docker info` and `docker compose version`.

### 2. Start PostgreSQL and run the scripted pair

Use the loopback-only database below, or set `OFFSECGYM_DATABASE_URL` to your own dedicated database using a `postgresql+asyncpg://` URL.

```sh
docker run --rm -d --name offsecgym-readme-db \
  -e POSTGRES_USER=offsecgym -e POSTGRES_PASSWORD=offsecgym \
  -e POSTGRES_DB=offsecgym -p 127.0.0.1:55432:5432 postgres:17
export OFFSECGYM_DATABASE_URL='postgresql+asyncpg://offsecgym:offsecgym@127.0.0.1:55432/offsecgym'
docker exec offsecgym-readme-db pg_isready -h 127.0.0.1 -U offsecgym -d offsecgym
```

Once `pg_isready` reports that PostgreSQL is accepting connections, apply the migrations and run the pair.

```sh
uv run alembic upgrade head
uv run offsecgym experiment run experiments/configs/scripted-saas.yaml --paired
```

The CLI prints two run IDs and their evaluations. Expect five distinct validated roots on the vulnerable build and zero findings on the fully patched build. This checks that the harness can discover and verify its known test cases. The runner tears down both range instances.

```sh
# Replace RUN_ID with either ID printed by the experiment.
uv run offsecgym experiment trace RUN_ID
```

When you’re done inspecting the traces, `docker stop offsecgym-readme-db` removes the disposable database container and its trace data. Keep the database running or use a persistent database if you want to revisit those traces later.

### 3. Explore a range or try a model

| Task | Command |
| --- | --- |
| Start a standalone SaaS instance | `uv run offsecgym range start examples/saas-range.yaml --seed 42` |
| Inspect its state and metadata | `uv run offsecgym range status INSTANCE_ID` and `uv run offsecgym range metadata INSTANCE_ID` |
| Reset it to its seeded state | `uv run offsecgym range reset INSTANCE_ID` |
| Remove the instance | `uv run offsecgym range destroy INSTANCE_ID` |

Replace `INSTANCE_ID` with the ID returned by `range start`. These commands manage the target lifecycle independently of the experiment database. To try Hello or enterprise change control, use their spec paths from the [ranges table](#ranges).

For a live model run, copy the [monolithic example](experiments/configs/monolithic-saas.yaml) to a local YAML file, replace the model placeholder with a model available on your provider, and set its key in your environment (`OPENAI_API_KEY` or `OPENROUTER_API_KEY`, matching `model.provider`). Review the action, model-call, token, and wall-time budgets before running it.

```sh
uv run offsecgym spec validate local-model.yaml --kind experiment
uv run offsecgym experiment run local-model.yaml --paired
```

See the [experiment spec](docs/experiment-spec.md) for provider settings, price fields, and supported agent options. Generated state and restricted evidence live under `.offsecgym/` by default. Set `OFFSECGYM_STATE_DIR` to use another directory. Use a separate disposable database for tests as described in [Development](docs/development.md).

## How it works

1. **Provision.** The experiment runner builds and starts an isolated Docker Compose range from a seed and patch configuration.
2. **Explore.** The agent sends typed requests through an audited gateway. The gateway checks route, identity, and budget before invoking a fixed HTTP worker inside the range network. Target ports stay unpublished.
3. **Prove.** The agent submits a candidate finding with action and evidence IDs. The independent validator checks the cited evidence against the **oracle**, the hidden description of which flaws are present and what proves them. Some state-changing findings also require replay in a fresh instance.
4. **Score.** Each validated root cause counts once per run. The evaluator tracks rejected findings, missed roots, and duplicate submissions separately. Root recall is the share of active flaws the agent validated. Patched runs have zero active flaws and are used to check false findings.

Read the diagram from the runner at the top, across the request path, then down to validation and scoring. PostgreSQL stores the ordered trace and usage data. Restricted HTTP evidence is stored locally and referenced by ID and hash. The oracle stays on the controller side.

[![OffSecGym architecture showing the experiment runner, audited gateway, isolated Docker range, validator, and scoring](docs/diagrams/offsec-gym-architecture.png)](docs/diagrams/offsec-gym-architecture.png)

[Architecture reference](docs/architecture.md) · [Safety boundary](docs/safety.md) · [Evaluation rules](docs/evaluation.md)

An experiment can use a scripted solver, a single model agent, or a coordinator that delegates bounded tasks to short-lived workers. A single agent can carry its conversation transcript or retrieve structured facts and prior actions from an event-backed memory.

## Ranges

A **range** is a synthetic application and its seeded starting data. Vulnerable and patched variants of a matched seed start with the same fixture, so a result can be tied to the behavior the patch changes.

| Range | What runs inside | What it tests | Example spec |
| --- | --- | --- | --- |
| **Hello** | Stateless HTTP service and a fixed HTTP worker | Startup, request dispatch, redirects, and Docker isolation | [hello-range.yaml](examples/hello-range.yaml) |
| **AcmeCloud SaaS (Range A)** | Multi-tenant API backed by SQLite | Five roots covering cross-tenant document, invoice, and ticket reads, public invoice metadata, and a member refund. The v2 scenario varies assignments and clues. | [saas-range.yaml](examples/saas-range.yaml) · [patched](examples/saas-range-patched.yaml) |
| **Enterprise change control (Range B)** | Identity and change services, an approval cache, a job worker, and a private PostgreSQL database | Three state-changing roots covering self-approval, approval with a revoked role, and a queued deployment executing after cancellation | [enterprise-change-control.yaml](examples/enterprise-change-control.yaml) |

The range diagram starts with the shared compiler and instance lifecycle. It then shows Hello’s health response, how an AcmeCloud document can expose a foreign invoice ID, and how the enterprise worker checks queued jobs. In Range B, each dispatched request advances a logical tick, so polling can affect when a deployment becomes due.

<details>
<summary>View the range internals diagram</summary>

[![OffSecGym range internals showing the shared build lifecycle, Hello, AcmeCloud SaaS, and enterprise change control](docs/diagrams/offsec-gym-range-internals.png)](docs/diagrams/offsec-gym-range-internals.png)

</details>

[Range lifecycle](docs/runtime.md) · [SaaS internals](docs/saas-range.md) · [Enterprise internals](docs/enterprise-change-control-range.md)

## What the experiments found

The published live studies below use high-reasoning Kimi K3. They vary how the agent uses memory, delegates work, or builds and reports evidence. The scripted solver and fake-provider tests verify the harness.

| Study | What it compares | What the study observed |
| --- | --- | --- |
| [M6.4 · Worker scheduling](docs/diagnostics/m64-confirmatory-results.md) | Fixed sequential, matched parallel, and opportunity-aware workers on paired SaaS seeds | 179 of 180 feasible runs had valid scores. At shared budgets, parallel runs averaged 124 s versus 419 s for fixed sequential. Opportunity-aware scheduling made 40k–80k token runs feasible. No worker policy had a clear recall advantage. |
| [M6.5 · Single-agent control](docs/diagnostics/m65-common-bootstrap-control-results.md) | A structured monolithic agent running on the same seeds, budgets, and bootstrap as the earlier workers | Across 100 score-valid runs, the vulnerable arm averaged 76% root recall at 120k tokens and 74% at 160k, above the historical worker arms. Collection took place later. Duplicate and patched false findings persisted. |
| [M6.5.1](docs/diagnostics/m651-witness-recovery-v2-results.md) and [M6.5.2 · Reporting context](docs/diagnostics/m652-reporting-context-v1-results.md) | A read-only reporter with extra compute, followed by a separate comparison of fresh and continued contexts with equal allowances | The extra reporter recovered 4 of 9 missed roots with complete proof, all on one seed. With equal allowances, fresh context recovered 11/19 eligible roots and continuation recovered 15/19. The paired interval included no difference. |
| [M6.6 v2 · Witness tools](docs/diagnostics/m66-confirmatory-v2-results.md) | An evidence ledger and reminders versus control in both range families | Complete SaaS refund proofs increased from 7/60 to 38/60. Enterprise went from 0/180 to 1/180 across its three roots. These are complete-proof counts. Validation and scoring are separate stages. |
| [M6.7.3](docs/diagnostics/m673-interface-decision-audit.md) and [M6.8.2 · Interface audits](docs/diagnostics/m682-reference-grounding-audit.md) | Read-only analyses of a stopped enterprise feasibility study | Agents missed follow-up reads and spent cancellation windows on unrelated reads. The original tool schema rejected all 18 experiment-start proposals. A later audit found 12 references with verified public grounding that failed the typed-reference grammar. |

One recurring failure is the gap between doing the right action and reporting it. In M6.4, the traces contained proof-capable actions for 131 root opportunities without a corresponding submission. One worker read a paid invoice, successfully refunded it, read the refunded state, and filed no refund finding. The trace shows what the worker did. It leaves open what the model recognized.

The latest work audits where experiment proposals fail between syntax, public grounding, activation, and later use. The [research plan](docs/research-plan.md) and [prospective experiments](docs/diagnostics/m673-prospective-experiments.md) describe follow-ups on reference formats, enterprise timing, and evidence collection. See [implementation status](docs/implementation-status.md) for the milestone history.

## Repository guide

| Path | What to look for |
| --- | --- |
| [`src/offsecgym/`](src/offsecgym/) | CLI, range compilers and runtime, gateway, agents, memory, validator, evaluator, and event storage |
| [`examples/`](examples/) and [`experiments/configs/`](experiments/configs/) | Standalone range specs and runnable experiment recipes |
| [`experiments/manifests/`](experiments/manifests/), [`experiments/results/`](experiments/results/), and [`docs/diagnostics/`](docs/diagnostics/) | Frozen study designs, aggregate results, and audit reports |
| [`migrations/`](migrations/) and [`tests/`](tests/) | PostgreSQL schema migrations and unit, integration, and Docker range tests |

OffSecGym is licensed under [Apache-2.0](LICENSE).
