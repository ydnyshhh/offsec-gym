# M6.5.2 bounded provider-attrition continuation

The [cell-27 stop](m652-sample-stop-cell-27.md) remains authoritative.
The collector retained 27 completed cells, including two score-invalid
source provider failures at orders 16 and 27, then stopped under the
earlier single-failure rule. The user directed continuation of the run.
This is a **post-start operational amendment**, not part of the original
confirmatory protocol or a reinterpretation of the failed cells.

The [amendment manifest](../../experiments/manifests/m652-bounded-provider-attrition-v1.json)
pins the unchanged sample manifest, the exact 27-cell stop journal,
both failed run IDs, the versioned
[controller](../../research_ops/m652_resume_with_provider_attrition.py),
and a ceiling of **four** provider-failed source cells across the 48-cell
sample. Each failure remains unscored and is never retried or replaced.
The controller resumes only the 21 unstarted cells, one at a time.

Only these transient source failure shapes can be retained:

- HTTP 429 `provider_rate_limited`;
- HTTP 408 `provider_timeout`;
- HTTP 5xx `provider_server_error`;
- no HTTP status with `provider_unavailable` (transport, OS, or timeout
  error; it does not prove an upstream outage).

For each retained failure, the controller requires one matching failed
model call, a terminal `provider_failed` event, no later model or gateway
work, no reporting branch, exact trace bytes and event identities from
PostgreSQL, matching selected endpoint for completed model calls, and
unscored evaluation replay. The original score-valid
budget-preflight-prefix rule is independently rechecked. A fifth
provider-failed source, invalid reporting branch, auth or quota failure,
request rejection, trace mismatch, endpoint or price drift, interrupted
cell, or any other gate failure stops collection for separate review.

The 48 planned cells and 24 seed pairs, model, Moonshot AI endpoint,
reasoning, prompts, 120k-token probe budget, 80k-token reporter
allowance per arm, range, validator, source-only proof rule, paired
analysis code, and $108 sample stop remain unchanged. The frozen
analysis already lists invalid prefixes and excludes them from its
eligible-root numerator and denominator. The final report must describe
provider attrition and cannot claim an unconditional 24-pair effect:
missingness could be related to a cell's outcome. The four-cell ceiling
was chosen after observing two provider failures, so that change and
its interpretation must be labeled explicitly.

Before any paid continuation, the controller and manifest are committed
separately, the 27-cell journal and PostgreSQL events are reconciled
read-only, unit checks pass, the live endpoint price matches the frozen
snapshot, and exact-head GitHub CI passes. No interim recovery result
was inspected to select this rule. Final postcheck must replay all 48
cells from authoritative PostgreSQL and preserve every invalid cell.
