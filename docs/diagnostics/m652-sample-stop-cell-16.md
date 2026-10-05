# M6.5.2 collection stop at cell 16: provider rate limit

The sole resumed collector stopped after it journaled cell 16. Its source
run, `232a0788-aa12-4f7c-8e5c-016fe6acbada`, ended `provider_failed`
after one HTTP 429 with reason `provider_rate_limited`. The frozen scoring
contract marks that run `score_valid=false`. It opened no fresh or
continuation reporting branch. The cell was neither retried nor replaced.

At this stop, all orders 1–16 had exactly one start and one completion in
the journal. No order 17 had started. Cumulative estimated sample token
cost was **$12.898005**, below the unchanged $108 stop. The stop journal
SHA-256 is
`775073bdc84ab7ff9de0c6f35b6dc0b23e09eeb58900e440eb0092948b1bac93`.
The sample manifest remains
`2afc5caaea1cfc330ea507783e4b8900fbd1255cc99e03f88bcf34b4a240197b`.

Read-only reconciliation matched the cell's 448 contiguous events and
event identities to authoritative PostgreSQL, its saved trace bytes and
SHA-256, the single terminal 429, the terminal `provider_failed` event,
the unscored evaluation including its candidate and validated counts,
and the absence of reporting branches. The completed model calls before
the 429 used the pinned `moonshotai/kimi-k3-20260715` endpoint on Moonshot
AI. No model call or gateway action followed the 429. Cell 16 had 22
candidate submissions and 16 validated verdicts before the failure;
these **do not become a valid score**.

Cells 2 and 10 remain separately retained as score-valid but reporting-
ineligible budget-preflight prefixes under the earlier
[eligibility amendment](m652-budget-preflight-amendment.md). Cell 16 is a
different condition: the provider path failed, so the source score is
invalid. The frozen [paired analysis](../../src/offsecgym/research/m652_analysis.py)
already lists invalid source prefixes and excludes them from its
eligible-root numerator and denominator. It still records the planned 24
seed pairs and does not impute a reporting outcome for this cell.

The [single-failure retention manifest](../../experiments/manifests/m652-provider-failure-retention-v1.json)
pins this stop, and the [continuation guard](../../research_ops/m652_resume_after_provider_failure.py)
requires an exact read-only event-store reconciliation before handing the
remaining unstarted cells to the previously pinned controller. The guard
accepts only this one stopped 429; another provider failure stops
collection. It changes no model request, checkpoint, seed, order, budget,
validator, source-only eligibility rule, primary analysis, or cost stop.
No recovery result was inspected to choose this operational rule.

Paid continuation requires exact-head GitHub CI on this amendment. The
final postcheck must preserve cell 16 as unscored, verify every source and
branch trace against PostgreSQL, and report all invalid-prefix flow and
any effect of provider attrition on interpretation.
