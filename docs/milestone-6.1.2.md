# Milestone 6.1.2: relationship and route correction

The [M6.1.1 smoke](diagnostics/kimi-k3-openrouter-m611.md) is frozen at
`32b17c1`, including its failed gates. This small revision changes only two
general handoff defects found by inspecting those traces:

1. Identity ranking now prefers members or workspace admins with `member_of`
   relationships to target workspaces. Its two reserved identity slots favor
   distinct workspaces, preserving a usable cross-workspace comparison.
2. The known-routes instruction lists the existing
   `GET /api/workspaces/{id}/tickets` endpoint. Previously it listed ticket
   detail reads but omitted the route needed to discover ticket IDs.

The six tasks, order, budgets, model, worker-local context, evidence contracts,
and coverage ownership remain as in M6.1.1. This is a correction to the
task-conditioned packet projection and route visibility, not a new scheduler
or an attempt to tune root-cause scores.

## Predeclared verification

Run three vulnerable worker repetitions with the same
[`kimi-k3-structured-m61-workers.yaml`](../experiments/configs/kimi-k3-structured-m61-workers.yaml)
spec. Preserve the previous three M6.1.1 runs as a separate batch. Use the
same mechanistic gates from [M6.1.1](milestone-6.1.1.md), without changing
thresholds: six workers with model calls; each objective has a relevant HTTP
action; peak input ≤10,900; no controller leaks or attribution errors; and
public/refund exploration in at least two of three runs. Additionally verify
that document and invoice packets retain two identities with distinct
workspace membership whenever the identity worker discovered at least two
such workspaces, and report ticket-list discovery or its absence in every run.

Record root-cause recall but apply no score gate. If retrieval-only worker
turns still prevent relevant HTTP actions, keep that as a failed behavioral
gate and stop packet/prompt tuning for this batch. M6.2 remains deferred until
the sequential system has a defensible, stable behavior profile.

The [three-run diagnostic](diagnostics/kimi-k3-openrouter-m612.md) is complete.
Relationship and ticket discovery checks passed, while objective-action and
refund-exploration gates remained failed. The recorded thresholds were not
changed after observing the traces.
