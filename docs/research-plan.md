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

The [M6.5.2 reporting-context study](diagnostics/m652-reporting-context-v1-results.md)
completed 48 cells on 24 new seed pairs after a separate feasibility pilot.
From the same frozen probe prefixes, fresh reporting recovered 11/19
proof-eligible missed roots versus 15/19 for continued context. The
fresh-minus-continuation paired interval spans −62.5 to +8.3 points; no
winner is established. Eleven prefixes lacked both valid reporting arms,
including two unscored provider failures. Post-start, versioned no-retry
attrition amendments and possible nonrandom missingness limit the
conditional estimate. The final PostgreSQL postcheck replayed all scores,
traces, and the predeclared analysis.

## Next questions

1. Interpret the completed [M6.6 v2 confirmatory result](diagnostics/m66-confirmatory-v2-results.md)
   by range family. The generic event-backed witness ledger increased
   complete-proof conversion in Range A, while the separately qualified
   Range B showed almost no conversion in either arm. The frozen study is
   complete; investigate the mechanism using its existing traces before
   proposing an independent replication.
2. Preserve the distinct patched false-finding, provider-failure, and
   score-valid matched analyses when assessing whether this witness scaffold
   generalizes beyond the synthetic ranges and pinned model endpoint.

Keep model, bootstrap, validator, range visibility, budget, provider endpoint,
and stopping rules explicit in every new manifest. Preserve distinct root
recall, duplicate submissions, false findings, latency, token/cost use, and
score-invalid failures separately. M6.6 v2 is complete; no new collection
starts without a costed, frozen protocol and separate approval. The completed
M6.5.2 dataset remains separate from the earlier M6.4 and M6.5 collections.
