# Kimi K3 OpenRouter M5.3 diagnostic

## Design and status

This is the predeclared three-run structured smoke test for
[M5.3](../milestone-5.3.md). It uses `kimi-k3-structured-m53.yaml`: Kimi K3,
high reasoning, seed 1, range seed 42, known routes, the same action/model
budgets, and the pinned Moonshot AI OpenRouter upstream without fallback. The
[M5.2 transcript traces](kimi-k3-openrouter-m52.md) remain the existing
control. There was no transcript rerun or 10+10 expansion. This is trace
inspection, not a statistical effect estimate.

The runner produced three runs. Run 1 ended `provider_failed` after an HTTP 429
from OpenRouter's Moonshot AI shared upstream pool. The saved error artifact
reports `provider_rate_limited` and a one-second `Retry-After`; the run is not
a clean completed smoke sample. Runs 2 and 3 reached their model token budgets.
All 31 completed model calls requested the pinned upstream and selected
Moonshot AI. The failed provider call is recorded separately.

| Run | Run ID | Status | Model calls | HTTP actions | Exact / unchanged repeats | Submitted / validated findings | First → last input tokens (peak) |
| --- | --- | --- | ---: | ---: | ---: | ---: | ---: |
| 1 | `73ad4391-562d-465a-bbfb-395c06cbd0ad` | provider_failed | 7 | 38 | 1 / 0 | 9 / 7 | 2,253 → 10,408 (13,016) |
| 2 | `971d986e-15cd-4e01-903f-e1d22ae15a1b` | budget_exhausted | 11 | 44 | 4 / 3 | 14 / 11 | 2,253 → 13,551 (13,551) |
| 3 | `658eb785-45aa-4d49-bdfd-937295568787` | budget_exhausted | 13 | 47 | 5 / 4 | 6 / 4 | 2,253 → 10,408 (11,341) |

An exact repeat uses the same method, path, identity, and JSON body as an
earlier action in that run. An unchanged repeat also has the same response
SHA-256. The three repeated invoice reads observed changed post-refund state,
so they are reported separately from the seven unchanged rereads.

## Gate audit

| Predeclared gate | Result | Trace evidence |
| --- | --- | --- |
| Unchanged exact repeats below 5% | **Fail** | 7/129 = 5.4% across all three; 7/91 = 7.7% in the two clean runs. Exact repeats including changed responses were 10/129. |
| Zero `/api/me` repeats | **Fail** | Three in run 2 and three in run 3, each for an identity already queried in that run. |
| Zero fabricated/truncated IDs | Pass in typed actions/findings | Every finding asset ID appears in its run's gateway action artifacts; every path segment that targets an object uses a canonical UUID; no tool-call rejection or malformed `*_id` argument was recorded. Prose descriptions sometimes abbreviate IDs, but typed targets and citations do not. |
| Zero cross-object evidence associations | Pass in submitted findings | All 34 action/evidence citation pairs resolve to matching gateway completions. Object-targeting cited actions match the finding asset across 29 submitted findings. The two workflow findings cite before/action/after requests against the same invoice. Identity and workspace-list context actions were treated as context, per the declared rule. |
| At least one validated finding in 2/3 runs | Pass | Both clean runs had validated findings (11 and 4); the provider-failed run had already validated 7. |
| Late input below roughly 12k tokens | **Fail** | Run 2's last input was 13,551. Runs 1 and 3 ended at 10,408; run 1 briefly peaked at 13,016 before its provider failure. |

## What the traces show

- The controller stored 26 document/ticket `body_excerpt` facts and 26 typed
  `mentions_invoice` facts across the three runs. The fixture bodies were
  shorter than the 512-character extraction limit, so no live
  `body_truncated` fact was needed; the bound is covered by a unit test.
- The active working set rendered recent action/evidence pairs under target
  entities. The run 3 model input before its repeated document read still
  contained that document's UUID and `body_excerpt`. Detail retention alone
  did not eliminate the reread.
- The model invoked `query_worldview` 1, 2, and 2 times, respectively, but
  never invoked `get_entity`. In run 2, the two `/api/me` repeats at action
  sequence 187 and 192 followed the initial identity checks at sequences 21
  and 40. The model request preceding those repeats contained the identities
  in the static roster but no prior role/action entry for them in its carried
  context. The five-entity recent set had rotated through other objects.
  This is an observed loss of *checked-identity state* from automatic context;
  it does not prove why the model declined the exact lookup.
- Run 2's final request carried two explicit worldview-query results of
  roughly 6.9k and 5.6k serialized characters alongside automatic context,
  tool schemas, and the latest exchange. The 7,500-character automatic
  context bound therefore did not bound the full model input below 12k.
- No new cross-object evidence association appeared. Both saved workflow
  findings keep the invoice asset and all before/action/after citations on
  the same exact UUID.

## Decision

**M5 is not frozen.** M5.3 improved the M5.2 repetition direction (M5.2 had
10 unchanged repeats in 116 structured actions), but it missed the
predeclared unchanged-repeat, `/api/me`, and late-input gates. The provider
failure also leaves only two clean runs. These traces do not authorize the
proposed M6 progression or a large-N monolithic comparison yet. The next
targeted change should preserve compact checked-identity/action state beyond
the five-entity recency window and bound explicitly retrieved result carryover;
its own gate should be declared before another small smoke test.

The event streams, model turns, provider error body, and gateway request
artifacts remain in the local diagnostic PostgreSQL database and `.offsecgym/`.
They are not committed to Git.
