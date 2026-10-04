# Research plan

## Completed evidence

The initial plan called for a scripted oracle, paired vulnerable/patched SaaS
ranges, a monolithic baseline, structured state, and ephemeral workers. Those
components now exist. The first confirmatory worker-policy matrix is complete:
[M6.4](diagnostics/m64-confirmatory-results.md) collected 180 feasible cells
across ten `tenant_boundary_v2` seeds, three worker policies, paired patch
states, and five token budgets. Of those cells, 179 were score valid; the one
provider failure was retained without retry. The matrix and analysis are
frozen at commit `50c3374`.

The common-feasible 120k–160k recall comparison does not identify a winning
worker policy. Parallel execution shortened observed latency; opportunity-aware
admission made 40k–80k trajectories feasible and reduced exact repeated
actions. The post-collection root-stage ledger found many proof-capable
actions that did not become submitted findings. Its proof classification is
descriptive and does not show that the model understood the evidence. Refund
also failed at readiness, execution, witness, and submission stages.

The separately approved [M6.5 common-bootstrap monolithic
control](diagnostics/m65-common-bootstrap-control-results.md) completed all
100 cells on those same ten seeds. All were score valid. Its monolithic agent
had higher observed root recall than each historical worker arm at the common
120k–160k budgets, while still producing duplicate submissions and patched
false findings. The bootstrap snapshot matched the worker protocol for every
seed and patch state. The comparison remains historical rather than a new
held-out, concurrently randomized architecture test.

The separate [M6.5.1 witness-recovery assay](diagnostics/m651-witness-recovery-v2-results.md)
completed 20 new paired cells. A fresh read-only reporter recovered four of
nine proof-capable roots missed by the integrated probe, but all four came
from one seed and the seed-bootstrap interval spans 0%–100%. The reporter
used additional compute, made many same-root submissions, and sometimes
could not reach a later call because its context exceeded preflight budget.
For refund, the probe often executed the unauthorized transition but rarely
constructed a complete ordered before/action/after witness. This assay is
frozen; its failed v1 pilot and bounded v2 results remain separate records.

## Next questions

1. [M6.5.2](milestone-6.5.2.md) should test a fresh context against an
   equally funded continuation from the **same immutable probing prefix**.
   Give both arms the same read-only evidence and reporting tools; preserve
   the exact probe carry state only in the continuation arm. This targets
   proof-to-finding conversion without changing exploration or adding
   compute to just one arm. Build branch isolation and replayable checkpoints
   before a costed pilot.
2. [M6.6](milestone-6.6.md) should test a generic, event-backed
   before/action/after witness ledger against the same exploration policy
   without that scaffold. This targets successful-action-to-complete-proof
   conversion, especially for temporal authorization tests. Keep the
   intervention oracle-free and include patched false-finding checks.
3. Add a second state-changing range family before claiming the witness
   mechanism generalizes beyond the current synthetic SaaS workflow.

Keep model, bootstrap, validator, range visibility, budget, provider endpoint,
and stopping rules explicit in every new manifest. Preserve distinct root
recall, duplicate submissions, false findings, latency, token/cost use, and
score-invalid failures separately. Both follow-ups are designs, not approved
model collections. No new model collection starts without a costed, frozen
protocol and approval.
