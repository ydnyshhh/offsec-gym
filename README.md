# OffSecGym

OffSecGym is a research platform for studying autonomous security agents in isolated,
synthetic cyber ranges. The range and evaluation infrastructure are the core product;
models and orchestration strategies are interchangeable subjects of study.

Licensed under Apache-2.0; see [LICENSE](LICENSE).

Milestone 2 adds a seeded multi-tenant SaaS range with five paired security properties,
isolated instance credentials, hidden ground truth, an attack graph, and live oracle tests.
The platform does not yet call a model or evaluate agent findings.

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
uv run offsecgym range start examples/hello-range.yaml
uv run offsecgym range status RANGE_ID
uv run offsecgym range metadata RANGE_ID
uv run offsecgym range stop RANGE_ID
uv run offsecgym range start RANGE_ID
uv run offsecgym range reset RANGE_ID
uv run offsecgym range destroy RANGE_ID
```

The SaaS vulnerable and patched siblings use the same seed and public fixtures:

```sh
uv run offsecgym range start examples/saas-range.yaml --seed 42
uv run offsecgym range start examples/saas-range-patched.yaml
uv run offsecgym range metadata RANGE_ID
```

`range start` prints `RANGE_ID`. Builds, instance manifests, and local evidence are kept in
`.offsecgym/`; set `OFFSECGYM_STATE_DIR` to use a different directory. Metadata lists
nonsecret identity IDs and the counterfactual pair ID. Hidden oracle files remain outside
the Docker build context. See [SaaS range](docs/saas-range.md) and
[runtime](docs/runtime.md) for the range and gateway behavior.

PostgreSQL integration tests require `OFFSECGYM_TEST_DATABASE_URL` to point to an
isolated, disposable database. Apply `alembic upgrade head` before running them.

See [architecture](docs/architecture.md), [safety](docs/safety.md), and
[implementation status](docs/implementation-status.md).
