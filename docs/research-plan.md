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

## Next questions

1. On new held-out seeds, test the conversion from trusted action evidence to
   finding submission. Start with a read-only reporter recovery assay, then
   predeclare an equal-compute comparison if recovery justifies it. Do not
   interpret extra-inference recovery as a free performance improvement.
2. Version the per-root `Ready → Admitted → Executed → Trace proof → Submitted
   → Validated` ledger prospectively. Report denominators and transition
   failures by vulnerability type, with a separate refund/state-transition
   analysis and patched false-finding checks.
3. After those tests, add a second range family with a different dependency
   structure to assess whether the orchestration observations generalize.

Keep model, bootstrap, validator, range visibility, budget, provider endpoint,
and stopping rules explicit in every new manifest. Preserve distinct root
recall, duplicate submissions, false findings, latency, token/cost use, and
score-invalid failures separately. No new model collection starts without a
costed, frozen protocol.
