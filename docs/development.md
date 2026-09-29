# Development workflow

Work in small, testable increments. Update architecture documentation before a major
subsystem and `implementation-status.md` after it. Add a regression test for a discovered
bug before or alongside the fix. Run unit and lint checks locally; CI runs the same checks
plus a PostgreSQL event-store integration test and real Docker range tests.

Use `uv sync --extra dev`, `uv run pytest -q`, `uv run ruff check .`, and
`uv run ruff format --check .`. Set
`OFFSECGYM_DATABASE_URL` for Alembic and `OFFSECGYM_TEST_DATABASE_URL` for the isolated
integration database. Do not point tests at a non-disposable database.

The CI workflow has one read-only validation job on pushes and pull requests. It has no
publishing authority or model-provider credentials. Hosted CI success is established only
after a workflow runs against the exact submitted commit.
