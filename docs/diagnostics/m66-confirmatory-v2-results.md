# M6.6 v2 confirmatory result

The frozen study completed all **280 assigned cells and 140 pair receipts** with no cell retried or replaced. The read-only final postcheck against the dedicated PostgreSQL event store passed for all 280 distinct source runs, 291,456 source events, 24 referenced validator replay runs, trace hashes, event identities, score replay, stage extraction, fixture and pair bindings, selected endpoint receipts, and cumulative cost. Every completed cell has an audited stage artifact. The frozen offline analysis was run from the exact protocol checkout twice; its output bytes matched.

## Predeclared primary outcome

The primary contrast is complete pre-terminal witness roots per **all assigned vulnerable root pairs**, witness minus control, analyzed separately by range family. Both arms have trace-auditable outcomes for every assigned vulnerable root, including retained provider-failed cells; there is no missing primary root outcome.

| Range family | Assigned vulnerable seed pairs | Witness complete | Control complete | Difference per assigned root | Seed-pair bootstrap 95% interval |
| --- | ---: | ---: | ---: | ---: | ---: |
| Range A (`saas`, MEMBER-REFUND) | 60 | 38/60 | 7/60 | +0.5167 | +0.3833 to +0.6500 |
| Range B (`enterprise_change_control_v1`, three roots) | 60 | 1/180 | 0/180 | +0.0056 | 0 to +0.0167 |

The single Range B complete witness was B1-SOD; B2-REVOKED-ROLE and B3-CANCELLED-JOB had none in either arm. The predeclared smallest effect of interest was 0.20 per assigned root. The observed Range A contrast exceeds it, while the Range B contrast is far smaller. The pooled +0.1333 opportunity-weighted contrast is secondary; it weights Range B's three roots three times per seed and does not replace the two family results. Bootstrap intervals use the frozen 10,000 seed-pair resamples.

The secondary matched-score-valid contrasts exclude three Range A vulnerable pairs and four Range B vulnerable pairs: Range A +0.5439 over 57 matched pairs (95% bootstrap +0.4035 to +0.6842); Range B +0.0060 over 56 matched pairs and 168 roots (0 to +0.0179). These are conditional on both arms having valid scores and are not the primary estimand.

## Patched findings, failures, and resources

Patched-build false findings are **all submitted security findings**, including inconclusive ones. Range A had four control submissions and three witness submissions; all seven were rejected and none inconclusive. Range B had zero submissions in either arm. All 40 patched cells had auditable traces.

Across all assignments, 242 cells ended `budget_exhausted`, 27 `agent_failed`, ten `provider_failed`, and one `completed`. Exactly 270 scores were valid. The ten retained score-invalid provider failures were cells 89, 90, 170, 186, 187, 188, 192, 226, 227, and 249. Agent failures remained score-valid where recorded. No completed or interrupted cell was retried or replaced.

The journal records 3,154 model-call starts and 3,144 completed model calls, 23,914,319 input tokens, 3,422,201 output tokens, and 8,667 gateway actions. Cumulative **estimated model-token cost was $123.075972**, below the approved $504 stop. The selected endpoint remained Moonshot AI | `moonshotai/kimi-k3-20260715` at $3/M input and $15/M output in the audited receipts, without fallback.

## Provenance and limitations

The frozen manifest SHA-256 is `a8aa98654360945ad69f350c38153dccaf827434a4e157819f558ae8da05916c`; source commit `6bfb14dc240ae6ab7e65bd04025b74688312a806`; protocol commit `9fe5b24e8fa1550ed681e9345673cbc1457e7e23`. The final journal SHA-256 is `0c72f16ceba1f86b0da2eaa6cc58da38cf1748b63d176c808eed108e10014119`. The saved authoritative postcheck SHA-256 is `970c57a7d8465905cbd7f2fc0075d93141374df29f0b84a1a3b489046a32af3e`; the saved frozen analysis SHA-256 is `4398e6e9968d476e070b7644b2e17dad479a19da1c12bc2536b24a74748f7058`.

Collection crossed several separately approved operational pauses for provider failures, public metadata transport, and exact-cell offline stage reconstruction. These amendments retained the original outcomes and frozen assignments, model requests, budgets, validator, and analysis, but they changed collection timing and required retrospective audit receipts. The ten provider failures remain part of the all-assigned result; the score-valid matched analysis has a narrower, post-failure population. Estimated token cost is based on recorded usage and pinned prices, not a billing statement. The finding is specific to these synthetic ranges, this endpoint, frozen evidence and budgets, and selected fresh seeds; the large difference between the two ranges cautions against treating the pooled result as a general effect.
