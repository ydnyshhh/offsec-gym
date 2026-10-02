# M6.4 confirmatory collection incidents

This is an append-only account of collection interruptions. The frozen
manifest, worker policy, run order, and predeclared analysis are unchanged.
The private `.offsecgym/diagnostics/m64-matrix/journal.jsonl` and trace exports
are the authoritative run records.

## 2026-10-02: cell 126 provider transport failure

- Manifest order 126, cell `b07525ee1352d6f8`: opportunity-aware, seed 1009,
  vulnerable, 160,000-token budget. Run `d813603f-e592-4d7e-94ac-225debeaf7b4`.
- Eleven model calls completed. The twelfth started at 18:17:36 UTC and the
  adapter recorded `provider_unavailable` at 18:49:29 UTC. It retained no HTTP
  status or response artifact, so the trace does not establish whether the
  cause was upstream service health or the local network path.
- The run completed as `provider_failed` with `score_valid=false`. The collector
  recorded the complete trace, clean controller replay, and estimated token
  cost of $0.337770, then stopped before cell 127. Its reported 73,840 input
  and 7,750 output tokens cover completed model calls; the failed call has no
  provider-reported usage. Actual provider billing may differ.
- At inspection, the OpenRouter model-metadata endpoint returned HTTP 200.
  This is a reachability check, not proof that the Responses endpoint was
  healthy. The journal contained 126 completed cells, no interrupted cell,
  and no active collector. A zero-cell replay passed.
- Cell 126 was **not retried**. Collection resumed at cell 127. The
  predeclared analyzer retains the invalid status count and excludes this
  cell from score-valid paired means and seed curves.

The failed request remained open for about 32 minutes despite the nominal
900-second run wall limit. This confirms the documented limitation that a
synchronous provider request can outlive coroutine cancellation. Report
elapsed time and censoring separately from model-performance outcomes.
