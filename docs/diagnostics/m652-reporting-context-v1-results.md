# M6.5.2: fresh versus continued reporting context

## Primary result

On the audited, source-proof-eligible missed roots, the fresh-context arm
recovered **11 of 19** and the continuation-context arm recovered **15 of
19**. The prespecified fresh-minus-continuation difference is **−4/19 =
−21.1 percentage points**. The 10,000-resample seed-pair bootstrap 95%
interval is **−62.5 to +8.3 percentage points**. There were five roots
recovered only by continuation and one only by fresh context.

This is an observed continuation advantage under the specified reporting
context policy, with substantial uncertainty: the interval includes no
difference. Only **seven vulnerable seeds** contributed any of the 19
eligible roots. Three of the five continuation-only roots came from one
seed; the sole fresh-only root came from another. The result does not
establish a population advantage for either context policy.

Each arm received the same frozen source evidence, read-only reporter
instruction and tools, model endpoint, nominal 80k-token/four-call
allowance, validator, and source-only proof rule. The treatment was the
exact post-tool model carry state: continuation received it; fresh began
without it. This compares reporting contexts after one shared probing
prefix, not extra reporting against no extra reporting.

## Enrollment, attrition, and patched control

All **48 planned cells across 24 vulnerable/patched seed pairs** have
exactly one journaled start and completion, with no retry or replacement.
The final read-only analysis lists **11 reporting-ineligible prefixes**:

| Reason | Cells | Count |
| --- | --- | ---: |
| Score-valid source, later model reservation could not fit the probe budget | 2, 10, 33, 42 | 4 |
| Probe ended `agent_failed` before an eligible reporting split | 18, 36, 39, 46, 47 | 5 |
| Source ended `provider_failed`, score invalid | 16, 27 | 2 |

Thus **37 cells** have both score-valid reporting arms: 16 vulnerable
and 21 patched. The primary 19-root denominator comes from seven of the
16 valid vulnerable prefixes, not all 24 enrolled vulnerable seeds.
Among the 21 valid patched prefixes, the fresh arm made **two** new
finding submissions and continuation made **four**. These are the
protocol's patched false-finding counts; they are submission counts, not
a broader estimate of false-positive severity or prevalence.

The collector stopped and recorded the [cell-2 budget edge](m652-sample-stop-cell-2.md),
the [cell-16 HTTP 429](m652-sample-stop-cell-16.md), and the
[cell-27 transport failure](m652-sample-stop-cell-27.md) before
continuing under separately versioned, user-approved operational rules.
The final [bounded provider-attrition amendment](m652-bounded-provider-attrition-amendment.md)
allowed no retries and at most four provider-failed sources; only the two
already documented failures occurred. The original sample manifest,
model requests, budgets, validator, and analysis code were never changed.
These **post-start amendments must remain attached to any use of this
result**. In particular, source or reporting-arm validity may be related
to an unobserved outcome, so the primary contrast is conditional on
eligible, valid prefixes rather than an unconditional effect over all
24 seed pairs.

## Realized resources

The two arms had equal *allowed* compute, but different realized usage:

| Reporting arm | Model calls | Input tokens | Output tokens | Reporter token-preflight endings |
| --- | ---: | ---: | ---: | ---: |
| Fresh | 129 | 1,809,045 | 173,893 | 18 |
| Continuation | 96 | 1,755,788 | 155,824 | 33 |

Continuation encountered more reporter token-preflight endings, yet
recovered more eligible roots in this sample. Fresh used 33 more model
calls. These are outcomes of the context policies under a common nominal
allowance; conditioning on equal realized calls would change the question.
Across source probes and both arms, the 48 cells used **680 completed
model calls**, **7,239,543 input** and **1,006,833 output** tokens. The
frozen endpoint-price estimate is **$36.821124**, below the $108 sample
stop. It is an estimate from recorded tokens, not a provider invoice.

## Authoritative audit

The [read-only postcheck](../../research_ops/m652_postcheck_v2.py)
passed against the dedicated PostgreSQL store. It matched all **48
distinct source runs** and **23,838 distinct event identities** to the
saved source and branch traces (21,702 source events and 2,136 branch
events), replayed oracle and branch scores, checked fixture/pair bindings,
source-only proof eligibility, the immutable checkpoint and first
reporter-request bytes, and found no post-split gateway action. It
recomputed usage/cost and the [predeclared analysis](../../experiments/results/m652-reporting-context-v1-analysis.json)
**byte for byte**. The [audit artifact](../../experiments/results/m652-reporting-context-v1-audit.json)
records these counts and hashes. Every completed model turn selected
`moonshotai/kimi-k3-20260715` on Moonshot AI.

| Frozen item | SHA-256 |
| --- | --- |
| Sample manifest | `2afc5caaea1cfc330ea507783e4b8900fbd1255cc99e03f88bcf34b4a240197b` |
| Final private journal | `7e04e95576254841baa6b88a7709bae717d1ef307d40067fb21d2fb79a0067d6` |
| Published analysis | `ab8d9751a02fcbe252287566547f62017e3e6f4d2a202385c0626dfd1cbd7ef9` |

The source traces, response/request artifacts, and range state remain
private under `.offsecgym/`; the published artifacts contain aggregate
analysis and audit receipts. The separate non-sample pilot and the older
M6.5.1 sample were not included in this 48-cell analysis.

## Interpretation limits

The observed direction runs against the hypothesis that a fresh
reporting context would improve recovery from the same evidence. The
wide interval, seven proof-eligible seed clusters, and 11 prefixes
without both reporting arms prevent a strong conclusion. The study uses
one Kimi K3 endpoint and one synthetic SaaS range family. Complete
source proof is an offline property of trusted artifacts; it does not
show what the model noticed. The treatment is this exact context-carry
policy under a common reporting role, not a generic test of long
transcripts or independent agents. The post-start attrition rules and
possible nonrandom missingness further limit confirmatory interpretation.
