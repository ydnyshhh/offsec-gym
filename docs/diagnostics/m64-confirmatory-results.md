# M6.4 confirmatory worker-policy results

## Scope and provenance

This is the completed, frozen `m64-v2-worker-primary-1` study. Its question is
how fixed sequential workers, matched parallel workers, and opportunity-aware
sequential workers behave across ten paired `tenant_boundary_v2` seeds and
worker-token budgets. Worker prompts, contracts, task minima, utility weights,
range visibility, provider request, validator, and budget ceilings were not
changed during collection.

- [Frozen manifest](../../experiments/manifests/m64-v2-worker-primary-1.json):
  seeds 1001–1010; vulnerable and fully patched variants; 40k, 60k, 80k,
  120k, and 160k token budgets; source commit `4de2249`.
- Collector checkout: `7540a45`; its [exact-head CI](https://github.com/ydnyshhh/offsec-gym/actions/runs/36980197474)
  passed before the first confirmatory call. Collection ran from
  2026-10-02 08:11:59 to 22:26:13 UTC, about 14.24 hours elapsed.
- [Predeclared analysis](../../src/offsecgym/research/m64_analysis.py) and
  [aggregate output](../../experiments/results/m64-v2-worker-primary-1-analysis.json)
  retain the prespecified denominators, seed pairing, and 10,000-resample
  seed-bootstrap intervals. The
  [objective-stage ledger](../../src/offsecgym/research/m64_stage_ledger.py)
  was implemented after collection. Its
  [aggregate output](../../experiments/results/m64-v2-worker-primary-1-stage-ledger.json)
  is descriptive and does not change the primary score analysis.

The 300 planned combinations contain **120 structurally infeasible** fixed or
parallel cells below their frozen 117k token minimum. No model was called for
those combinations. All **180 feasible cells** were run and journaled; **179
were score valid**. Cell 126 had an unscored `provider_failed` outcome and was
not retried; see the [incident log](m64-collection-incidents.md).

## Integrity and resource accounting

The completed journal has one start and one completion per feasible manifest
cell, in frozen order, with 180 distinct run IDs and no interrupted cell. An
independent pass verified all trace SHA-256 hashes, 96,490 contiguous event
sequence numbers, run and build identities, experiment hashes, reported token
totals, and five active oracle roots per vulnerable build with zero active
roots in patched siblings. All 2,122 completed model calls reported the
selected `moonshotai/kimi-k3-20260715` / `Moonshot AI` endpoint. The failed
call had no response or selected-endpoint metadata. A zero-cell collector
replay reproduced the predeclared analysis byte for byte, SHA-256
`8700995428e2f1df8bc5efc27e177242edc6b80593044ef606be2a08251fd68d`.

Completed calls reported **15,326,456 input** and **1,884,340 output** tokens.
Using the frozen $3/M input and $15/M output price snapshot yields **$74.24
estimated token cost**, below the collector's $306 estimated threshold. This
is not a provider invoice; the failed call has no reported usage, and fees or
provider accounting can differ. The 180 main-run traces and request/response
artifacts remain private in `.offsecgym/`. Only aggregate results are in Git.

Score-valid run statuses were 100 `budget_exhausted`, 67 `agent_failed`, and
12 `completed`. These scored statuses remain experimental outcomes; the one
`provider_failed` run is excluded from score-valid paired comparisons.

## Primary outcomes

Each vulnerable build has five distinct configured root causes. Recall is
the mean fraction validated across score-valid vulnerable runs. Patched
false findings are rejected candidate findings per score-valid patched run.

| Arm | Token budget | Valid vulnerable runs | Mean root recall | Valid patched runs | Mean patched false findings |
| --- | ---: | ---: | ---: | ---: | ---: |
| Fixed sequential | 120k | 10 | 0.24 | 10 | 0.5 |
| Fixed sequential | 160k | 10 | 0.38 | 10 | 0.9 |
| Matched parallel | 120k | 10 | 0.26 | 10 | 0.3 |
| Matched parallel | 160k | 10 | 0.36 | 10 | 1.2 |
| Opportunity-aware | 40k | 10 | 0.20 | 10 | 0.0 |
| Opportunity-aware | 60k | 10 | 0.28 | 10 | 0.0 |
| Opportunity-aware | 80k | 10 | 0.28 | 10 | 1.1 |
| Opportunity-aware | 120k | 10 | 0.28 | 10 | 1.0 |
| Opportunity-aware | 160k | 9 | 0.356 | 10 | 0.1 |

The common-feasible comparison uses only 120k–160k vulnerable cells. Its
normalized trapezoidal recall AUC is **0.310** for fixed sequential (10
score-valid seed curves), **0.310** for matched parallel (10), and **0.322**
for opportunity-aware (9). The unpaired arm means have different denominators
because of cell 126. The prespecified paired estimates are:

| Left minus right | Paired seed curves | Mean AUC difference | Seed-bootstrap 95% interval |
| --- | ---: | ---: | ---: |
| Fixed − parallel | 10 | 0.000 | −0.130 to 0.120 |
| Fixed − opportunity-aware | 9 | −0.033 | −0.111 to 0.044 |
| Parallel − opportunity-aware | 9 | 0.000 | −0.111 to 0.111 |

All three intervals span zero. With ten held-out seeds, this sample does not
establish a root-recall winner at common feasible budgets. Budget-specific
paired differences and patched false-finding intervals are preserved in the
aggregate JSON; patched false findings remain nonzero and variable.

At 40k, 60k, and 80k, only opportunity-aware admits runs under the frozen
policies. Its observed vulnerable recall is 0.20, 0.28, and 0.28. The fixed
and parallel entries in the separate policy-opportunity curve are structural
infeasibility, **not observed zero model recall**. The curve combines policy
feasibility with model performance and cannot support a like-for-like
low-budget model-behavior comparison.

## What the traces explain

The oracle-audited descriptive ledger covers all **445 active roots** in the
89 score-valid vulnerable runs. The six columns of missed-root stages are
applied in precedence order. `Trace proof` means trusted action artifacts met
the deterministic validator's static proof requirements. A refund transition
also requires before/after action evidence; clone replay occurs only when a
finding is submitted, so an unsubmitted trace proof is not a validated root.

| Root | Not ready | Ready, not admitted | Admitted, not executed | Executed, no trace proof | Trace proof, no submission | Submitted, not validated | Validated |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| Document read | 0 | 16 | 2 | 4 | 45 | 0 | 22 |
| Invoice read | 0 | 30 | 1 | 0 | 38 | 0 | 20 |
| Ticket read | 0 | 39 | 1 | 1 | 39 | 0 | 9 |
| Member refund | 30 | 0 | 28 | 30 | 1 | 0 | **0** |
| Public invoice metadata | 0 | 0 | 2 | 0 | 8 | 0 | 79 |

`READY` and admission predicates exist only for the opportunity-aware arm;
fixed and parallel workers receive six fixed packets, so their early-stage
readiness is inapplicable. The ledger's categories are useful for locating
failures, not a cross-arm causal estimate of prerequisite quality. In
particular, **no arm validated the refund root**. Among 89 score-valid
vulnerable runs, it was not ready in 30 opportunity-aware runs, had an
admitted task but no completed objective route in 28 runs, had a completed
route without sufficient trace proof in 30, and had trace proof without a
matching submission in one. There were no submitted refund findings. The
public metadata root was validated in 79/89 runs. Document, invoice, and
ticket roots were validated in 22, 20, and 9 runs respectively.
The unsubmitted refund trace proof is cell 124 (opportunity-aware, seed
1010, 120k): a member read a paid own-workspace invoice, received HTTP 200
and `refunded` from the refund POST, then read the refunded state. The model
submitted no refund finding. This is action-level evidence; no clone replay
was invoked without a submitted candidate.

`Trace proof, no submission` occurs 131 times across all roots. This means
the gateway record contains proof-capable actions, **not** that the model
necessarily retained or used those responses. The ledger is a post-collection
implementation of the predeclared stage definitions and should be treated
as descriptive evidence about conversion from actions to findings.

Across score-valid common 120k–160k cells, fixed, parallel, and
opportunity-aware workers recorded respectively 2,129, 2,035, and 1,841
HTTP dispatches; 163, 165, and 84 exact fingerprint repeats; and 51, 41,
and 8 cross-worker repeats. All three had **zero reservation conflicts**. No score-valid
opportunity-aware run had opportunity displacement or admissible unused
tokens. The aborted provider-failure run alone ended with 78,410 admissible
unused tokens; it is not evidence of a scheduler admission failure.

Mean per-run objective execution coverage in score-valid common-budget runs
was 0.845 for fixed, 0.880 for parallel, and 0.927 for opportunity-aware.
The latter's mean readiness and admission coverage were 1.000 and 0.846. Cross-worker
evidence references in submitted findings were rare: 1/107 fixed, 0/110
parallel, and 2/116 opportunity-aware submissions. This measures cited
provenance, not useful evidence reuse. At common budgets, matched parallel
ran a mean 124 seconds versus 419 seconds for fixed sequential, with similar
recall. That latency observation is descriptive and includes provider-time
variation; the randomized order reduced but did not remove temporal effects.

## Interpretation and limits

Opportunity-aware scheduling made the 40k–80k cells feasible and reduced
exact repeated dispatches in the common-budget sample. Parallel scheduling
shortened observed run time. Neither change produced a clear common-budget
recall advantage. The larger bottleneck is root-specific behavior: refund
work rarely formed a complete witness and never became a finding, while
proof-capable document, invoice, and ticket actions often did not become
submissions. Patched false findings also remain a practical safety issue.

The study compares three worker policies, not a monolithic control. A
common-bootstrap monolithic arm would require a separate manifest revision.
The selected endpoint was stable for completed calls, but one transport
failure reduced a paired 160k seed curve to nine. Exact fingerprint repeats
do not measure semantic duplication; evidence-reference reuse does not prove
utility. The stage ledger was coded after data collection and omits clone
replay for unsubmitted transitions. Do not tune or rerun this frozen protocol
to improve these results. A new protocol can target witness formation,
finding submission, and common-bootstrap controls explicitly.
