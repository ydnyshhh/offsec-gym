# OffSecGym

OffSecGym is a research platform for studying autonomous security agents in isolated,
synthetic cyber ranges. The range and evaluation infrastructure are the core product;
models and orchestration strategies are interchangeable subjects of study.

Licensed under Apache-2.0; see [LICENSE](LICENSE).

Milestones 2 and 2.5 add a seeded multi-tenant SaaS range with five security properties,
isolated instance credentials, typed hidden ground truth and attack graphs, generation-bound
evidence, and live oracle tests. Milestone 3 adds a scripted agent, independent deterministic
validation with isolated replay, and root-cause-based evaluation. Milestone 4 adds a
monolithic model baseline through the same gateway, finding, and scoring contracts.
Milestone 5 adds event-backed structured worldview memory and bounded context retrieval.
Milestone 5.2 adds controller-verified response facts, exact entity IDs, and canonical
finding categories; see [Milestone 5.2](docs/milestone-5.2.md).
Milestone 5.3 adds bounded document/ticket details, a recent entity/evidence working
set, and exact `get_entity` lookup; see [Milestone 5.3](docs/milestone-5.3.md).
Milestone 5.4 adds pinned checked identities, a bounded checked-action index, and
bounded worldview-tool carryover; see [Milestone 5.4](docs/milestone-5.4.md).
The corrected M5.4 code at `0f27533` is the frozen comparison baseline, with
its two failed research gates preserved in the [diagnostic](docs/diagnostics/kimi-k3-openrouter-m54.md).
Milestone 6.0 adds atomic PostgreSQL controller reservations and worker
lifecycle contracts; see [Milestone 6.0](docs/milestone-6.0.md).
Milestone 6.1 adds a deterministic sequential coordinator with bounded worker
packets, attributed findings, and typed debriefs; see
[Milestone 6.1](docs/milestone-6.1.md). The matched three-control/three-worker
[Kimi diagnostic](docs/diagnostics/kimi-k3-openrouter-m61.md) records valid
traces, earlier first findings, and lower worker root-cause recall.
[M6.1.1](docs/milestone-6.1.1.md) addresses worker budget starvation and
duplicated context with predeclared three-run smoke gates.
The [M6.1.1 smoke](docs/diagnostics/kimi-k3-openrouter-m611.md) cleared
budget and context gates but missed objective-action and refund-exploration gates.
[M6.1.2](docs/milestone-6.1.2.md) corrects workspace-aware identity selection
and the known ticket-list route under a separately predeclared verification.
The [M6.1.2 diagnostic](docs/diagnostics/kimi-k3-openrouter-m612.md) confirmed
those handoff fixes but retained failed objective-action and refund gates.
[M6.1.3](docs/milestone-6.1.3.md) adds a typed worker objective contract and
action-required turn. Its [three-run diagnostic](docs/diagnostics/kimi-k3-openrouter-m613.md)
met the refund POST and bounded-context gates but exposed three contract
failures when preparatory discovery or verification conflicted with the
detail-only route rule. The sequential baseline is frozen with these failures.

## Development

Use Python 3.12 or newer (below 3.15). With `uv`:

```sh
uv sync --extra dev
uv run offsecgym --help
uv run pytest -q
uv run ruff check .
```

Validate and run a synthetic range with Docker running:

```sh
uv run offsecgym spec validate examples/hello-range.yaml
uv run offsecgym range build examples/hello-range.yaml
uv run offsecgym range inspect-build BUILD_ID
uv run offsecgym range create BUILD_ID
uv run offsecgym range start examples/hello-range.yaml
uv run offsecgym range status INSTANCE_ID
uv run offsecgym range metadata INSTANCE_ID
uv run offsecgym range stop INSTANCE_ID
uv run offsecgym range start INSTANCE_ID
uv run offsecgym range reset INSTANCE_ID
uv run offsecgym range destroy INSTANCE_ID
```

The SaaS vulnerable and patched siblings use the same seed and public fixtures:

```sh
uv run offsecgym range start examples/saas-range.yaml --seed 42
uv run offsecgym range start examples/saas-range-patched.yaml
uv run offsecgym range metadata INSTANCE_ID
```

To run and score the scripted vulnerable/patched pair, set a dedicated PostgreSQL control
database URL, apply migrations, and run the experiment:

```sh
export OFFSECGYM_DATABASE_URL='postgresql+asyncpg://USER:PASSWORD@HOST/DB'
uv run alembic upgrade head
uv run offsecgym experiment run experiments/configs/scripted-saas.yaml --paired
```

The command prints each run ID and evaluation. Vulnerable runs should validate five distinct
root causes; patched runs should submit no vulnerability findings. The agent receives only
its projected context and gateway responses. See [Milestone 3](docs/milestone-3.md).

For the monolithic baseline, edit the model name in
[the diagnostic config](experiments/configs/monolithic-saas.yaml), set `OPENAI_API_KEY`, and
run `offsecgym experiment run experiments/configs/monolithic-saas.yaml --repetitions 10`.
The same PostgreSQL setup is required. See [Milestone 4](docs/milestone-4.md) for budget
and failure semantics.

To run the structured-memory diagnostic with the same range and model settings, use
`experiments/configs/monolithic-saas-structured.yaml`. Set its model name and the same
API/database environment variables first. `offsecgym experiment worldview RUN_ID`
prints reconstructed facts and coverage; use `--predicate` or `--kind` to filter facts.
The configs compare agent systems with different tools and prompts; their score
difference is not a memory-only ablation. See [Milestone 5](docs/milestone-5.md).

The Kimi K3 OpenRouter diagnostic configs pin the same model, high reasoning, range,
seed, surface visibility, and budget in both arms. With the control database migrated
as above, set the API key and run three vulnerable repetitions per arm:

```zsh
read -rs 'OPENROUTER_API_KEY?OpenRouter API key: '; echo
export OPENROUTER_API_KEY
uv run offsecgym experiment run experiments/configs/kimi-k3-transcript-diagnostic.yaml --repetitions 3
uv run offsecgym experiment run experiments/configs/kimi-k3-structured-diagnostic.yaml --repetitions 3
```

Each run prints an ID. Use `offsecgym experiment trace RUN_ID` to inspect its events
and `offsecgym experiment worldview RUN_ID` to inspect structured memory. These
small samples are for trace inspection, not a statistical score comparison.
See the [Kimi K3 six-run diagnostic](docs/diagnostics/kimi-k3-openrouter-m5.md)
for observed memory behavior and run IDs.

For the M5.2 follow-up, use `kimi-k3-transcript-m52.yaml` and
`kimi-k3-structured-m52.yaml` in `experiments/configs/`. Both pin the OpenRouter
upstream to `moonshotai` with fallbacks disabled. The historical M5-v1 findings
can be replayed with `offsecgym experiment revalidate RUN_ID --legacy-m5v1`.
The [M5.2 diagnostic](docs/diagnostics/kimi-k3-openrouter-m52.md) records the
matched pinned-provider traces and the predeclared progression gates.
The [M5.3 diagnostic](docs/diagnostics/kimi-k3-openrouter-m53.md) records three
structured runs and the gate misses. No 10+10 expansion was run.
The [M5.4 initial](docs/diagnostics/kimi-k3-openrouter-m54-initial.md) and
[corrected](docs/diagnostics/kimi-k3-openrouter-m54.md) three-run diagnostics
separate identity, action-history, retrieval, and policy behavior. The
corrected batch still missed two predeclared gates. Those limitations are
accepted for the frozen M5 comparison baseline; no additional Kimi tuning is planned.

`range start` prints an instance ID and generation. `range create BUILD_ID` separates
instance creation from startup; UUID-based lifecycle commands accept instance IDs only.
Builds, instance manifests, request artifacts, and evidence are kept in `.offsecgym/`;
set `OFFSECGYM_STATE_DIR` to use a different directory. Controller metadata lists
nonsecret identity IDs and the counterfactual pair ID; the default agent-visible context
does not include that roster. Hidden oracle files remain outside the Docker build context.
See [SaaS range](docs/saas-range.md) and [runtime](docs/runtime.md).

Generated Milestone 2 build and instance manifests are incompatible with this version.
Stop and destroy live instances with the earlier version before upgrading, then use a fresh
state directory and rebuild. Previously generated SaaS builds with an older hidden-oracle
schema also require rebuilding. The existing YAML examples remain valid.

PostgreSQL integration tests require `OFFSECGYM_TEST_DATABASE_URL` to point to an
isolated, disposable database. Apply `alembic upgrade head` before running them.

See [architecture](docs/architecture.md), [safety](docs/safety.md), and
[implementation status](docs/implementation-status.md).
