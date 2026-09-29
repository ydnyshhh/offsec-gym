# ADR 0001: Local research foundations

Status: accepted for Milestones 0–3.

Use Docker Compose for local ranges and defer Kubernetes. Keep target networks per run and
make the ActionGateway the only target-facing executor. PostgreSQL events are the
authoritative history; structured tables are projections. Use deterministic state-based
validation and paired vulnerable/patched builds before model comparisons. Represent shared
world state as typed, provenance-bearing facts rather than a shared text scratchpad.

These choices directly support containment, reproducibility, independent scoring, and
swappable orchestration. Revisit only with measured bottlenecks or failed acceptance tests.
