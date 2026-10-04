# M6.5.1: completed witness-recovery sample

## Scope and audit

The frozen [v2 protocol](../../experiments/manifests/m651-witness-recovery-v2.json)
ran a structured-memory monolithic probe and then a fresh, read-only reporter
on ten new paired `tenant_boundary_v2` seeds: one vulnerable and one fully
patched build per seed. The probe retained its 120k-token allowance. The
reporter had a separate 80k-token, four-call allowance and could retrieve
trusted evidence and submit findings, but could not make range requests.
The [failed v1 pilot](m651-witness-pilot-v1-results.md) and the
[successful one-cell v2 feasibility recheck](m651-witness-pilot-v2-results.md)
are outside this 20-cell sample.

All **20 cells** have one journaled start and completion, a distinct run ID,
and a score-valid outcome. No cell was retried or replaced. Probe run status
was `budget_exhausted` in 16 cells and `agent_failed` in four; those partial
trajectories remain in the prespecified score. There was no provider failure.
The selected endpoint remained `moonshotai/kimi-k3-20260715` on `Moonshot AI`.
The 3,302,544 reported model tokens comprise 2,879,933 input and 422,611
output tokens. Estimated token cost at the frozen price snapshot was
**$14.978964**, below the approved $45 stop; this is not a provider invoice.

The [read-only postcheck](../../research_ops/m651_witness_postcheck.py)
matched all 20 trace files to their hashes and authoritative PostgreSQL
event streams: 10,487 contiguous events with unique identities. It rebuilt
each reporter packet from its source event prefix and restricted artifacts,
checked the packet/source hashes, the 17-request bootstrap boundary,
run/build identities, model usage, endpoint, and drained controller state.
No gateway action followed `ReporterStarted`. Hidden-oracle scoring and the
prospective root ledger replayed for every run, and the
[predeclared analysis](../../experiments/results/m651-witness-recovery-v2-analysis.json)
replayed byte for byte. The [audit summary](../../experiments/results/m651-witness-recovery-v2-audit.json)
records hashes and counts. Private traces, model artifacts, packets, and
range state remain under `.offsecgym/`.

## Predeclared primary result

The ten vulnerable runs had 50 configured root opportunities. The probe
constructed complete static trace proof for 41, then submitted validated
findings for 32 of those 41. That left **nine recoverable missed roots**:
complete proof in the frozen probe evidence but no distinct validated
integrated finding. The reporter newly validated **four of nine**, a
**44.4% recovery fraction**. Combined distinct-root coverage was 36 of 50.

The prespecified 10,000-resample **seed-pair bootstrap interval is 0%–100%**.
All four recoveries came from one vulnerable seed, `193727`; another seed
had four recoverable missed roots and no recovery, and a third had one and
no recovery. The point estimate therefore does not establish a stable
population recovery rate.

| Root family | Complete probe proof / 10 | Integrated validated | Recoverable missed | Reporter recovered |
| --- | ---: | ---: | ---: | ---: |
| Document cross-tenant read | 10 | 8 | 2 | 1 |
| Invoice cross-tenant read | 10 | 8 | 2 | 1 |
| Ticket cross-tenant read | 9 | 7 | 2 | 1 |
| Member refund transition | 2 | 1 | 1 | 0 |
| Public invoice metadata | 10 | 8 | 2 | 1 |

The reporter submitted **no false finding on the ten patched runs**. This
small patched sample cannot establish a zero false-positive rate elsewhere.

### Refund has a different bottleneck

In all ten vulnerable runs, a member attempted the relevant refund route;
the trace contains 19 successful unauthorized transition actions across
those runs. Only **two of ten** runs assembled a complete ordered,
same-identity paid-before / refund / refunded-after witness. One of those two
had an integrated validated finding. The reporter did not recover the other
proof-capable missed refund root, despite five matching reporter submissions
across the vulnerable runs; none of those five supplied validation-sufficient
citations. A successful state change alone is not a complete reportable
transition witness.

## Exploratory reporter behavior

The following diagnostics were computed **after** the primary analysis from
the frozen traces with a separate
[post hoc script](../../research_ops/m651_witness_exploratory.py). They did not
change the manifest, denominator, or primary estimator. The
[machine-readable diagnostic](../../experiments/results/m651-witness-recovery-v2-exploratory.json)
preserves the exact counts.

| Reporter terminal reason | Cells |
| --- | ---: |
| Four-call limit (`reporter_call_budget`) | 6 |
| Later request could not pass token preflight | 7 |
| Total model-token budget exhausted | 1 |
| Finished naturally | 5 |
| Invalid reporter tools | 1 |

Thus 14 of 20 reporters ended at a budget boundary, including **seven**
`reporter_preflight` endings after two or three successful model calls. The
v2 amendment made multi-call reporting feasible in the non-sample pilot and
many sample cells, but did not eliminate late context-growth failures. The
observed recovery is under this exact bounded four-call protocol; it is not
the maximum recoverability of the traces.

The reporter made **38 candidate submissions**: 26 validated and 12 rejected.
Of the 26 validated submissions, **22 repeated a root** already validated by
the integrated probe or an earlier reporter submission, while four added a
distinct root. Some same-root submissions cite different valid witnesses, so
root duplication is not necessarily an exact repeated claim. The 12 rejected
reporter submissions had seven `property_unmatched` and five `proof_missing`
validation codes. These mechanical codes alone do not establish the model's
reasoning error.

| Reporter call | Cells reaching call | Retrievals | Candidate submissions | Validated | Root duplicates | New distinct roots | Cumulative new roots |
| ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| 1 | 20 | 97 | 0 | 0 | 0 | 0 | 0 |
| 2 | 19 | 93 | 1 | 0 | 0 | 0 | 0 |
| 3 | 17 | 26 | 15 | 12 | 12 | 0 | 0 |
| 4 | 11 | 2 | 22 | 14 | 10 | 4 | 4 |

All four incremental roots appeared on call 4 of the **same** reporter
trajectory, after 20 retrievals in that run. This shows a case where later
reporting inference mattered; the sample does not identify a general
marginal-compute curve because only 11 cells reached call 4 and the recovery
is concentrated in one seed.

The reporter used 1,025,185 tokens overall: **0.39 incremental roots per
100k reporter tokens**, or 256,296 reporter tokens per incremental root
across the sample. Submission novelty was **4/38 = 10.5%**. There were 218
retrieval calls. Among 212 distinct evidence IDs fetched by exact
`get_evidence`, 157 were never cited by a reporter finding; 13 cited IDs had
no exact `get_evidence` lookup. Search and entity lookup, plus the automatic
packet index, are other information paths. These provenance counts do not
measure whether a retrieval influenced the model.

## Interpretation and limits

This study demonstrates that **fresh inference can sometimes turn an
already complete frozen witness into a distinct validated finding** without
further exploration. It also shows substantial losses before that stage:
nine configured roots lacked complete proof in the probe traces, including
eight of ten refund opportunities. Later reporter inference did not solve
the missing ordered refund witnesses.

The assay adds reporter compute. It does **not** compare against an equally
funded continuation of the integrated agent or isolate a causal advantage
for a separate reporter. The interval is broad, all recoveries occurred in
one seed, and budget/preflight endings censor some reporter trajectories.
The range is one synthetic SaaS family and the model is one selected Kimi K3
endpoint. Complete trace proof is a static, offline property of trusted
artifacts; it does not show what the model noticed. The v1 pilot failure,
v2 feasibility amendment, and this sample remain separate versioned records.

The frozen sample journal SHA-256 is
`e7c7d463fc9e4287caed2b501c4edd12d1c4860f066d9468617b734275c1c7ad`;
the published primary analysis SHA-256 is
`2e9521baf2150180da03f84cd403b54ef4b17c2769682a1d2441b80df2d38605`.
