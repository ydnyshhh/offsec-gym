# Kimi K3 OpenRouter M5.4 corrected smoke

## Design

This is the fresh three-run verification after the targeted M5.4 checked-action
rendering correction in commit `3990755`. The [initial M5.4 smoke](kimi-k3-openrouter-m54-initial.md)
and its failed gates remain preserved. The [predeclared gates](../milestone-5.4.md)
are unchanged. The config still pins Kimi K3, high reasoning, Moonshot AI with
fallback disabled, the same seeds, synthetic range, surface visibility, and
budgets. No transcript rerun or large-N comparison was made. The three traces
are diagnostic observations, not a causal effect estimate.

| Run | Run ID | Status | Model calls | HTTP actions | Exact / unchanged repeats | Findings submitted / validated | First → last input tokens (peak) |
| --- | --- | --- | ---: | ---: | ---: | ---: | ---: |
| 1 | `04f8beda-6863-40f6-92f5-911c7ac68eae` | budget_exhausted | 12 | 39 | 3 / 0 | 9 / 6 | 2,253 → 7,431 (9,075) |
| 2 | `1b97599a-0ca0-4737-9c62-50ddab9a39e3` | completed | 12 | 45 | 4 / 3 | 10 / 7 | 2,253 → 9,412 (10,113) |
| 3 | `edc2c4b7-a5d9-4e93-875f-58dd2b0b1177` | budget_exhausted | 15 | 30 | 0 / 0 | 14 / 12 | 2,253 → 7,559 (9,774) |

All 39 completed model calls requested and selected the pinned Moonshot AI
upstream. The corrected action index displayed up to 25 compact checked-action
lines late in runs 1 and 2 (22 in run 3), versus about ten before correction.
The maximum carried worldview-tool output was 2,311 serialized characters;
the maximum fresh automatic context plus carried worldview outputs was 9,694
characters. Each model request had exactly one fresh automatic context.

## Gate audit

| Predeclared gate | Result | Evidence |
| --- | --- | --- |
| Zero identity rediscoveries caused by eviction | Pass | No `/api/me` request was repeated. The pinned identity section remained separate from object recency. |
| Zero fabricated/truncated typed IDs | Pass | No tool-call rejection. Every typed `*_id` tool argument was a canonical UUID found in the run's gateway actions/evidence, and every submitted asset ID appeared in gateway artifacts. |
| Zero cross-object evidence associations | **Fail under the literal gate** | One validated exposure finding claimed invoice `863af65f…` and cited its own preview plus a preview of invoice `764f6dde…`. The second citation explicitly says it is a *second invoice* corroborating a systemic serializer issue. Both action/evidence pairs are real; the second object still violates the predeclared same-asset rule. |
| At most one eviction-driven unchanged repeat | **Fail** | Three in run 2; none in runs 1 or 3. Immediately before each repeat, the model input lacked the exact checked request. Replay of the 32-entry index shows one had aged out of storage and two were stored but not in the 25 rendered entries. |
| World-tool carryover ≤2,400 characters; combined memory ≤10,000 | Pass | Observed maxima: 2,311 and 9,694 characters. |
| Late full input <12,000 tokens in every clean long run | Pass | Last inputs: 7,431, 9,412, 7,559; peaks: 9,075, 10,113, 9,774. |
| ≥1 validated finding in at least two clean runs | Pass | All three were clean enough for scoring and had 6, 7, and 12 validated findings. |

Across 114 HTTP actions, there were seven exact repeats: three unchanged
workspace-document listings in run 2 and four invoice reads with changed
responses in runs 1 and 2. The latter followed state changes and are reported
as verification, not memory eviction. No unchanged repeat occurred with a
matching checked-action line visible in its immediately preceding model
request in this batch. That is a descriptive result for three trajectories;
the [initial batch](kimi-k3-openrouter-m54-initial.md) did contain eight
unchanged repeats with relevant prior action state visible, so policy-side
redundancy remains a real observed category.

All 77 submitted finding action/evidence pairs resolved to their gateway
completions. No citation used an invented or truncated UUID, and no finding
tool was rejected in this corrected batch. The cross-object case above is
an explicitly labeled corroborating example, not a silent mix-up of which
invoice the first citation proves. It still misses the stated gate. The
validator accepted that finding because the claimed invoice also had its
own valid preview citation; acceptance does not waive the research gate.

## Decision

**M5 is frozen as the accepted M6 comparison baseline at commit `0f27533`.**
The corrected M5.4 system fixed
identity eviction and explicit retrieval carryover and exposed substantially
more checked actions within the same context ceiling. The remaining
memory-side failures are three early workspace-list checks that were no longer
visible at a later identical request. One had left the 32-entry store; two
were outside the 25-entry rendered subset. The separate same-asset citation
gate also failed on clearly labeled second-object corroboration.

The failed gates above remain failed: three eviction-driven unchanged rereads
exceeded the one-repeat threshold, and a second invoice was used as explicitly
labeled corroboration outside the literal same-asset evidence rule. Bounded
checked-action history can evict older work. The evidence contract has no
explicit `primary` versus `corroborating` role, so a valid systemic example
cannot be represented without failing that literal rule. These are accepted
limitations for the M5 baseline, not retroactive gate passes. We will not
tune Kimi further just to remove these three rereads.

No further memory tuning or large-N monolithic comparison is part of this
baseline. The original and corrected event streams, raw model turns, and gateway
artifacts remain in the local diagnostic PostgreSQL database and
`.offsecgym/`; they are not committed to Git.
