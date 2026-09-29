# OffSecGym

OffSecGym is a research platform for studying autonomous security agents in isolated,
synthetic cyber ranges. The range and evaluation infrastructure are the core product;
models and orchestration strategies are interchangeable subjects of study.

Licensed under Apache-2.0; see [LICENSE](LICENSE).

Milestones 2 and 2.5 add a seeded multi-tenant SaaS range with five security properties,
isolated instance credentials, typed hidden ground truth and attack graphs, generation-bound
evidence, and live oracle tests. The platform does not yet call a model or evaluate agent findings.

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
