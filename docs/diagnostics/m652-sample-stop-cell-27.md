# M6.5.2 collection stop at cell 27: transport unavailable

The sole M6.5.2 collector stopped after it journaled cell 27. This is the
second source run to end `provider_failed`, so the
[single-failure continuation rule](../../experiments/manifests/m652-provider-failure-retention-v1.json)
required a hard stop. The collector is no longer running. Cell 27 has not
been retried or replaced, and cell 28 has not started.

At this stop, exactly 27 of 48 planned cells have one start and one
completion each. Seven of the 24 planned vulnerable/patched seed pairs
are complete, 13 have only one completed variant, and 21 cells are
unstarted. Cumulative estimated sample token cost is **$21.212547**
under the unchanged $108 stop. The stop journal SHA-256 is
`4013c4b450ba03189c5f4ef68524862ac18fdba27a5ff0aa8f75021cddafb2dc`.
The frozen sample manifest remains
`2afc5caaea1cfc330ea507783e4b8900fbd1255cc99e03f88bcf34b4a240197b`.

Cell 27 (`55d0b2c5f783f5f7`, vulnerable seed 749139) has source run
`c2ce2afd-ccc3-4df2-bfb0-68f6d6210aba`. Its authoritative event
stream contains 194 contiguous events. Read-only reconciliation matched
every event and event identity to the saved trace, verified the trace
hash, replayed the unscored evaluation, and found no reporting branch or
later gateway action. A single `ModelCallFailed` event at sequence 192
has reason `provider_unavailable` and no HTTP status; the run closed
`provider_failed` at sequence 194 with `score_valid=false`. The provider
adapter uses this reason for transport, OS, and timeout errors, so this
record does **not** establish whether the upstream model service itself
was unavailable. This cell has no scored recovery outcome.

The earlier cell 16 was a distinct, observed HTTP 429 rate limit. Cells 2
and 10 remain score-valid but reporting-ineligible budget-preflight
prefixes. None of these cells has been retried or rescored. The frozen
paired analysis accepts invalid-prefix records, but its primary result
cannot be reported for the planned 24-pair sample while 21 cells remain
unstarted. No interim recovery aggregate was inspected to make a
continuation decision.

Any further collection needs a separately documented decision about
provider-failure attrition. It must preserve these recorded cells, the
original model requests and budgets, sample order, source-only proof
rule, primary analysis, and $108 cost stop. It must not silently treat
`provider_failed` as a valid score or retry these runs.
