# M6.6 confirmatory v2: cell-188 provider-health pause

The approved collector stopped after **188 of 280** frozen assignments. The
append-only journal SHA-256 is
`bc651787966bbbfbdcc750a6949272f9cb8c1fc25f064ae7c75a51126480b87b`.
Cumulative estimated model-token cost is **$82.502808** under the original
$504 cap. Cell 189 never started. Pair 94 has no authoritative postcheck
receipt. No stopped cell may be retried or replaced.

Cells 186, 187, and 188 ended `provider_failed/provider_rate_limited`. The
frozen provider-health rule pauses after three provider failures in the last
ten cells. This is an intended policy gate, rather than a model or controller
race. Cells 89 and 90 remain retained `provider_unavailable`, cell 170 remains
retained `provider_rate_limited`, and cell 162 remains a score-valid
`agent_failed` source with the separately recorded offline stage exception.

The selected endpoint receipts remained Moonshot AI |
`moonshotai/kimi-k3-20260715` at $3/M input and $15/M output. A read-only
replay against authoritative PostgreSQL found **188 source runs, 182 valid
scores, six provider failures**, contiguous event identities, matching trace
hashes and oracle scores, and no extra run or active controller reservation.
The pair-94 source runs were audited individually: cell 187 has 623 events
and identity SHA-256
`a9ad17ab326351ae87115cbd65034a773b45e7415dbf5f1215319c8ed4a9427d`;
cell 188 has 833 events and identity SHA-256
`e0544f1f2cc56935c3bdc54d13c080e332514255a18e6f696411a5b0ee4a6eeb`.
A temporary-copy pair-94 receipt passed the complete frozen postcheck for
all 188 cells. **No original study artifact was written and no paid model
call was made during this reconciliation.**

## Proposed narrow operational amendment

The one-time wrapper is pinned to the exact 188-cell journal and the three
retained rate-limited run identities. It requires a **new, separate,
scope-bound approval receipt** whose hash is currently all zeros in code.
Until that hash is pinned, it fails closed. After approval, it would replay
all 188 PostgreSQL source runs and validator replay runs, require a fresh
public selected-endpoint and price check, create pair 94's receipt solely
from the two already completed arms, and record an explicit clearance.
The full frozen postcheck must pass before the unchanged collector can admit
cell 189. The new provider-health epoch would start at 189 exactly once.
Another health pause would stop collection again.

The proposed approval scope is **only assigned cells 189–280**, each at most
once, under the **same $504 cumulative** estimated-cost cap. The source
commit, protocol commit, model prompt and request, range, validator,
budgets, seeds, arm order, manifest, and original terminal outcomes remain
fixed. The amendment does not increase the sample, replace missing outcomes,
or treat an HTTP 429 as a scored model result.

## Interpretation limits

This is a post-start operational deviation. Provider failures remain in the
intention-to-treat schedule; any eligible-only comparison has a conditional
estimand and must report attrition by arm, range, and variant. Public model
metadata and a matching selected endpoint do not prove that rate limiting
has cleared. If a separately approved continuation encounters another
provider-health pause, the guard must stop again. This amendment provides
reproducible evidence handling, not an assurance that the remaining cells
will complete.
