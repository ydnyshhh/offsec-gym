# M6.6 confirmatory v2: provider-health pause after cell 90

The approved v2 sample is stopped at **90 of 280 assigned cells**. The frozen
manifest remains SHA-256
`a8aa98654360945ad69f350c38153dccaf827434a4e157819f558ae8da05916c`.
Neither failed cell was retried or replaced, and cell 91 did not start.

Cell 89 (`d22b46a80a5008c9`) ended `provider_failed` with reason
`provider_unavailable`. The first collector stopped when the immediate public
endpoint recheck failed DNS resolution. The guarded recovery in PR #21 passed
exact-head CI, preserved that run, and performed a fresh matching endpoint
recheck before starting only its assigned partner, cell 90.

Cell 90 (`278c79865b5f3895`, run
`4ea5886d-ebcb-41b5-8595-d00a7eb13a3c`) also ended `provider_failed` with
reason `provider_unavailable`, after ten completed model calls and one failed
call. The pinned provider-health policy returns `pause` for two consecutive
transport failures. The recovery process therefore stopped before writing a
pair-45 postcheck receipt or starting cell 91. Its terminal error reports that
policy decision; it does not authorize another attempt.

The final append-only journal has SHA-256
`1f47e18f9f8b993ea6ddcae3a240133235da4d9b904653b8c5dc932d681acf1d`.
It records 90 completed cells, 88 score-valid cells, two retained score-invalid
provider failures, and **$39.286650** cumulative estimated model-token cost
against the approved $504 stop. A separate read-only replay checked all 90
authoritative PostgreSQL runs and 93,451 events, trace hashes, stage outputs,
score replay, model-call accounting, build and fixture bindings, and the
dedicated run inventory. It found no extra run or active controller reservation.

No treatment estimate is reported from this incomplete sample. Any later
analysis must retain both provider-failed cells in their original assignment
positions and state its missing-outcome rule. Further paid collection would
need a separately reviewed operational amendment that explicitly handles the
frozen `pause` decision and missing pair receipt. The v1 stopped sample and all
v2 evidence remain unchanged.
