# M6.5.2 confirmatory collection stopped at cell 2

**Status:** stopped by a predeclared prefix-integrity gate. The collector is
not running. No cell was retried or replaced, and no third cell started.
The frozen sample manifest remains
`2afc5caaea1cfc330ea507783e4b8900fbd1255cc99e03f88bcf34b4a240197b`.

The journal contains two completed vulnerable cells, orders 1 and 2, for
seeds 793024 and 526804. Both source scores are valid. The completed trace
files match their hashes; the pinned selected endpoint was observed. The
cumulative estimated sample token cost is **$1.591260**, below the $108
stop. The journal SHA-256 at this stop is
`ab4f3d3b08446d9925fe7e69444914ac4c7d69aeff818481ff791b8cf60cde23`.

Cell 2 (`36be9ab0d4a7709d`) has source run ID
`f8adfe3d-6bfb-4e3b-8f9b-ea6865b5bc46`. Its source trace SHA-256 is
`e93f6d770beaa9604765270dd9a0594f03538893c655f72a26f5e2742031c37c`.
Independent read-only reconciliation matched all 527 source events to the
authoritative PostgreSQL stream, loaded the verified checkpoint artifact,
and replayed the source score from the oracle. No reporting branch exists
for this cell.

The source completed nine model calls. It saved a valid post-tool
checkpoint at event sequence 514. A subsequent context retrieval at 515
was followed by `model_reservation_rejected` at 516 with reason
`model_token_budget_exhausted`: the tenth request could not fit the frozen
120k-token probe cap. The paired runner requires the checkpoint to be the
last source event before branching, so it recorded
`reporting_prefix_rejected(invalid_source_prefix_ValueError)` at 517.
The source ultimately closed `budget_exhausted`; no branch request or
post-split gateway action occurred. The collector retained the completed
cell and stopped on its frozen prefix-integrity gate.

This is a **request-reachability/eligibility edge case**, not evidence of
provider drift or a corrupt trace. It cannot be resolved by retrying the
cell or changing its budget without changing the experiment. A possible
prospective amendment is to classify exactly this verified pattern as an
ineligible source prefix, retain cell 2 in the flow table with no reporting
outcome, and resume only unstarted cells under the same model, seeds,
budgets, and manifest. That would be an explicit eligibility and collector
amendment, not a reinterpretation of the frozen result. No amendment or
resumption has been made.
