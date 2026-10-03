# M6.5 common-bootstrap monolithic control: completed results

## Scope

This is the completed `m65-common-bootstrap-monolithic-v1` control. It ran one
structured-memory monolithic agent on ten `tenant_boundary_v2` seeds
(1001–1010), paired vulnerable and fully patched builds, and five configured
model-token budgets: 40k, 60k, 80k, 120k, and 160k. The
[frozen manifest](../../experiments/manifests/m65-common-bootstrap-monolithic-v1.json)
specified 100 cells in randomized order, their build and config hashes, the
selected model endpoint, and the
[predeclared analysis](../../src/offsecgym/research/m65_monolithic_analysis.py).
The model was Kimi K3 with high reasoning on the `Moonshot AI` upstream.

The question is how this control compares with the already completed
[M6.4 worker-policy study](m64-confirmatory-results.md) after both receive
the same deterministic prerequisite bootstrap. The comparison is **matched
historical**, not concurrently randomized: M6.4 ran earlier, and researchers
already knew its seed outcomes before this control was collected.

## Collection and audit

All **100 cells** have one journaled start, one completion, and a distinct run
ID. All 100 evaluations are score valid: 90 ended `budget_exhausted`, eight
`agent_failed`, and two `completed`. Budget and agent failures keep their
partial findings in the score. There were no provider-failed or unscored
control cells, and no cell was silently retried.

The [read-only postcheck](../../research_ops/m65_control_postcheck.py)
verified every trace SHA-256, 40,380 contiguous event sequence numbers, run
and build identities, experiment hashes, 17 bootstrap GET requests per cell,
and the bootstrap snapshot against the matching M6.4 seed and patch state.
It also checked that controller reservations drained, all 752 completed model
calls reported `moonshotai/kimi-k3-20260715` / `Moonshot AI`, and model usage
agreed with the journal. Scoring was independently reconstructed from each
run's finding and validation events and its verified hidden oracle. An
aggregate query matched the event identities, order, and count of all 100
matrix runs to PostgreSQL. The predeclared analysis replayed byte for byte.
The public [audit summary](../../experiments/results/m65-common-bootstrap-monolithic-v1-audit.json)
contains the hashes and counts; private traces and response artifacts remain
in `.offsecgym/`.

The collector originally stopped after cell 5 because structured-memory
coverage claims were still active after a valid `RunCompleted`. The
[documented amendment](m65-monolithic-amendment-1.md) reconciled that same
run from authoritative events, without another model or range request. The
resumed collector closed any remaining monolithic coverage leases after
`RunCompleted`. Nine of the 100 traces contain such post-run bookkeeping.
This administrative difference is visible in the event stream and does not
change the agent trajectory or score.

The cells reported **6,013,236 input** and **1,122,366 output** model tokens.
At the frozen $3/M input and $15/M output price snapshot, cumulative
estimated token cost was **$34.875198**, below the approved **$138**
threshold. This is a token-price estimate, not a provider invoice.

## Predeclared outcomes

Each vulnerable build has five active root causes. The table gives mean
distinct-root recall across score-valid vulnerable runs and mean rejected
findings across patched runs. Each M6.5 entry has ten runs. A dash means the
frozen M6.4 worker policy was structurally infeasible at that budget; it is
not an observed zero score.

| Model-token budget | M6.5 monolithic recall | M6.4 fixed sequential | M6.4 matched parallel | M6.4 opportunity-aware | M6.5 patched false findings |
| ---: | ---: | ---: | ---: | ---: | ---: |
| 40k | 0.26 | — | — | 0.20 | 0.0 |
| 60k | 0.68 | — | — | 0.28 | 0.1 |
| 80k | 0.64 | — | — | 0.28 | 0.0 |
| 120k | 0.76 | 0.24 | 0.26 | 0.28 | 0.0 |
| 160k | 0.74 | 0.38 | 0.36 | 0.356* | 1.0 |

\* The M6.4 opportunity-aware 160k mean has nine score-valid vulnerable
runs; its provider-failed tenth run remains unscored and unretried.

The prespecified common-feasible comparison uses paired seed curves at 120k
and 160k. Its normalized recall AUC differences are **M6.5 monolithic minus
the historical worker arm**. The intervals are 10,000-resample seed-bootstrap
intervals from the frozen analysis code:

| Historical worker arm | Paired score-valid seed curves | Mean AUC difference | Seed-bootstrap 95% interval |
| --- | ---: | ---: | ---: |
| Fixed sequential | 10 | +0.440 | +0.350 to +0.530 |
| Matched parallel | 10 | +0.440 | +0.340 to +0.530 |
| Opportunity-aware | 9 | +0.422 | +0.311 to +0.533 |

At 40k, 60k, and 80k, only the opportunity-aware historical arm can be
paired. The corresponding monolithic-minus-worker mean recall differences
are +0.06 (interval −0.14 to +0.30), +0.40 (+0.28 to +0.50), and +0.36
(+0.16 to +0.52). The full
[aggregate analysis](../../experiments/results/m65-common-bootstrap-monolithic-v1-analysis.json)
preserves all budget and patch-state comparisons, denominators, token rates,
duplicate counts, and elapsed times.

Across the 50 vulnerable control runs, the agent submitted 415 candidate
findings. Validation accepted 343 submissions, but they represented only
**154 distinct roots**: 189 accepted submissions repeated an already found
root. Another 72 vulnerable submissions were rejected, and 96 active roots
remained undiscovered. Across the 50 patched runs, all 11 submitted findings
were rejected. Ten of those patched false findings came from three 160k
runs; the other one came from a 60k run. More compute did not remove false
findings or repeated submissions in this sample.

## Interpretation and limits

The aligned bootstrap and stable selected endpoint support a more useful
comparison than the earlier unmatched monolithic baseline. The observed
historical recall difference is large and consistent across the common
120k–160k budgets. It does **not** identify the causal effect of monolithic
versus worker orchestration by itself. The arms ran at different times and
were not randomized together; prompts, task contracts, memory exposure, and
budget-allocation behavior are parts of their different systems. Matching
seeds, nominal model-token budgets, validator, and bootstrap does not remove
those differences or external provider variation.

The patched false-finding sample is small and concentrated in a few runs.
Candidate count is not distinct-root recall; the 189 duplicate accepted
submissions show why those measures must remain separate. The control covers
one synthetic SaaS range family. It does not provide a prospective root-stage
conversion ledger or show whether a model noticed every proof-capable action.
The separate [M6.5 witness-to-finding design](../milestone-6.5.md) targets
that question on new held-out paired seeds. Its pilot and 20-cell sample are
separate from this completed control.

The control protocol and these results are frozen. The private journal has
SHA-256 `e42df35279366a9093c87221043d073810962404ae8f7d5dcd310ea9b7327e74`;
the published analysis has SHA-256
`a6a3034c4e500f5f08be0f02c7e8f3ffd1a1c1987f5ba62e80cea8112f8bb78a`.
