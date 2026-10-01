# Milestone 6.2.2: matched prerequisite bootstrap

M6.2.1 is frozen [with its failed coverage result](diagnostics/kimi-k3-openrouter-m621.md).
This is a separate protocol. It keeps the M6.1.3 worker objectives, contracts,
prompt, tools, fixed packet builder, fixed worker slices, model, provider,
range, validator, and two scheduling modes. It adds the same deterministic
agent-visible discovery phase before either scheduler starts. It does not
add dependency-aware scheduling.

## Discovery boundary and provenance

The bootstrap uses the ordinary audited gateway and `EventWorldState`. It
starts from visible identity IDs and issues `GET /api/me` for each. From those
responses, it chooses a member or workspace administrator for each observed
workspace and issues only:

```
GET /api/workspaces/{workspace_id}/documents
GET /api/workspaces/{workspace_id}/invoices
GET /api/workspaces/{workspace_id}/tickets
```

It does not read object details, public previews, or refund routes. Thus it
supplies IDs and ownership relationships while leaving the vulnerability
probes to the workers. Actions, evidence, and mechanically extracted facts
retain ordinary gateway provenance. `PrerequisiteBootstrapStarted` records
the phase budget. `PrerequisiteBootstrapCompleted` records its action count,
entity counts, and a SHA-256 snapshot of canonical facts and checked
requests without run-specific event IDs. `CheckedAction` and packet evidence
mark bootstrap provenance. Worker HTTP requests also record their originating
model call ID and tool call ID, enabling direct turn-to-action attribution.

## Separate budgets

The bootstrap cap is **32 actions / 32 HTTP / zero model calls / zero model
tokens / 120 seconds**. Its actual audited use is reported separately. The
worker phase retains **60 actions / 60 HTTP / 20 model calls / 120,000 model
tokens / 600 seconds**, split into the same six fixed worker slices as M6.2.1.
The controller's combined action and HTTP ceilings are 92, while the worker
tool slices still sum to 60. Unused bootstrap allowance is not transferred
to workers. The bootstrap does not use provider credentials or hidden range
fixtures.

Configs:

- [`kimi-k3-m622-bootstrapped-sequential.yaml`](../experiments/configs/kimi-k3-m622-bootstrapped-sequential.yaml)
- [`kimi-k3-m622-bootstrapped-parallel.yaml`](../experiments/configs/kimi-k3-m622-bootstrapped-parallel.yaml)

## Predeclared one-pair diagnostic

Run one vulnerable repetition per arm with Kimi K3 high reasoning, Moonshot
AI upstream, fallback off, seed 1, range seed 42, known routes, and the
deterministic validator. This is a trace diagnostic without a score-winner or
statistical claim. Check these gates before interpreting performance:

1. Bootstrap uses only the permitted GET routes and zero model calls;
   its normalized snapshot hash is identical across arms.
2. Every document, invoice, ticket, public, and refund packet has an
   appropriate target. Document and invoice packets include identities from
   at least two distinct workspaces. Applicable bootstrap checked actions
   appear in each packet, and packet size remains bounded.
3. All six workers start and finish. No budget, reservation, provenance,
   replay, or range-generation error occurs. Sequential overlap is zero;
   parallel overlap is positive.
4. At least four of the five non-identity objectives execute a matching
   objective action in each arm. At most one worker per arm violates the
   strict contract. Record failures exactly; do not tune Kimi to pass them.
5. Record validated roots, false findings, token and HTTP use, root recall
   per token/action, time to first and last valid finding, queue wait,
   coverage, exact and manually annotated semantic duplication, reservation
   conflict, cross-worker evidence reuse, and abandoned branches. Count
   already-completed exact actions separately from active-owner conflicts.
   Direct model-turn links identify turns generating redundant actions;
   the total tokens on those turns remain an upper bound on wasted compute
   when a turn also performs useful work.

The real Docker plus PostgreSQL fake-worker check completed before freezing
this live protocol: both arms used 17 bootstrap GETs, observed 8 identities,
3 workspaces, and 3 objects of each listed type, and produced the same
snapshot hash. Packet target, checked-action, bounded-context, and replay
gates passed in that check. The live pair is the first test of whether the
unchanged model workers use this state productively.
