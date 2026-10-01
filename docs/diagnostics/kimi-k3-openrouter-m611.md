# Kimi K3 OpenRouter M6.1.1 smoke

## Frozen protocol

These three vulnerable sequential-worker runs used code and gates committed at
`aad7e61`, before any results were inspected. The
[predeclared gates](../milestone-6.1.1.md) and earlier
[M6.1 comparison](kimi-k3-openrouter-m61.md) remain unchanged. Each run used
the same synthetic build `c435a866-da5a-5f65-a2b5-ba2e4c2cc4db`, seed 42,
known routes, deterministic validator, OpenRouter `moonshotai/kimi-k3`, high
reasoning, pinned Moonshot AI upstream without fallback, and global limits of
60 actions, 60 HTTP requests, 20 model calls, 120,000 tokens, 8,192 maximum
output tokens per call, and 600 seconds. The worker policy capped its own
output at 4,000 and protected one 15,500-input/4,000-output prospective
reservation per future worker. No monolithic control was rerun. This is a
mechanistic smoke, not a new effect estimate.

| Run | Run ID | Score | Calls | HTTP | Exact repeats | Input + output tokens | Peak input | First valid |
| --- | --- | --- | ---: | ---: | ---: | ---: | ---: | ---: |
| 1 | `8d57812d-c11c-495c-9305-7aeb64eb5c5f` | 1/5 roots; TP 1, FP 0, dup 2 | 15 | 38 | 0 | 101,289 + 10,255 = 111,544 | 9,368 | 268 s |
| 2 | `1b472963-4799-408d-87a2-1d70748dc0ec` | 1/5 roots; TP 1, FP 0, dup 2 | 15 | 20 | 0 | 97,720 + 7,941 = 105,661 | 7,962 | 258 s |
| 3 | `8f6cde9e-f0e3-40e3-943f-44caa8f8368b` | 1/5 roots; TP 1, FP 0, dup 2 | 15 | 25 | 1 cross-worker | 97,394 + 7,486 = 104,880 | 8,231 | 271 s |

All runs completed with `score_valid=true`, submitted three findings, and
validated all three submissions; two submissions per run duplicated the one
public-exposure root cause. No finding cited evidence originating with a
different worker in this batch. Configured model prices were absent, so cost
is unavailable. All 45 stored requests pinned the same model, high reasoning,
Moonshot AI upstream, and disabled fallback. Actual input
was below its reservation on every call (signed input error ranged from
−6,036 to −3,821 tokens).

## Predeclared gate audit

| Gate | Result | Observation |
| --- | --- | --- |
| All six workers get ≥1 model call | **Pass** | Calls by objective (identity, document, invoice, ticket, public, refund): run 1 `[2,3,2,3,2,3]`; runs 2 and 3 `[2,3,3,2,3,2]`. |
| Each nontrivial worker takes ≥1 relevant HTTP action per run | **Fail** | Relevant actions by the same order: run 1 `[17,7,0,0,3,8]`; run 2 `[14,0,0,0,3,0]`; run 3 `[14,0,7,0,3,0]`. The ticket worker in run 2 made three invoice reads, not ticket actions. |
| No worker has zero calls because an earlier worker spent its protected floor | **Pass for these traces** | Every worker received two or three model calls. The gate does not establish that every later worker had sufficient calls for a finding. |
| Peak worker input ≤10,900 tokens per run | **Pass** | Peaks 9,368, 7,962, 8,231, below both the predeclared 10,900 limit and the prior M6.1 worker peak of 11,831. First-call automatic context used the worker-local delta mode in every worker. |
| Packet omissions reported and objective state manually checked | **Fail on relevance** | Omitted entity/evidence/checked-action/detail counts across six packets were `21/22/36/0`, `21/23/5/12`, and `21/30/21/10`. Document and invoice packets retained three target objects, but their two selected identities had role labels without membership links. Ticket packets contained no ticket entity; none had been discovered before that task. Evidence in every packet remained bound to a retained entity. |
| Zero leaks, attribution errors, and replay mismatch | **Pass** | Six packets, debriefs, and finished workers per run; zero remaining active action or coverage leases, worker slots, or token reservations. Event replay matched persisted action, HTTP, model, and token counters. No worker/task ownership mismatch was found. |
| Public and refund each take a relevant HTTP action in ≥2/3 runs | **Public pass; refund fail** | Public preview actions in all three runs; refund POST actions only in run 1. |

Coverage is now coordinator owned. It completed for 4/6, 2/6, and 3/6
objectives respectively; every other lease was released. `Completed` means at
least one objective-route HTTP dispatch, not exhaustive security coverage.
The one exact repeat was cross-worker in run 3. This trace-only repeat count
does not classify broader semantic duplication.

## Failure localization

The resource-floor fix removed the **zero-model-call** starvation seen in
M6.1 W2/W3, and the packet-aware context reduced peak input size. It did not
produce a relevant HTTP action from every task. Workers with no relevant
action often used their entire two or three calls on `query_worldview` and
`get_entity`: for example, run 2's refund worker issued seven retrieval calls
across two model turns and no HTTP request. Run 3's document worker made six
worldview queries across three turns and no HTTP request. This is a model/tool
trajectory observation, not a controller reservation failure.

The handoff relevance check found two concrete limits. The deterministic
entity ranking reserved identity slots but favored recently checked
identities without `member_of` links in document/invoice packets. The identity
worker queried `/api/me`, document lists, and invoice lists, but never the
workspace ticket list. No ticket entity was available at the ticket handoff;
the known-routes instruction also omitted `GET /api/workspaces/{id}/tickets`.
These observations identify packet and route-discovery work for a separately
versioned follow-up. They do not retroactively pass the failed gates.

## Decision

**Do not freeze sequential orchestration or begin M6.2 parallel runs yet.**
M6.1.1 passed budget fairness, smaller context, replay, attribution, and lease
integrity checks. It failed objective-action opportunity, packet relationship
relevance, and refund exploration. Root-cause recall stayed at one public
root in all three traces; no score-improvement threshold was predeclared and
three runs cannot establish a score effect. A next iteration should repair
identity/workspace selection and the ticket route hint, then measure whether
retrieval-only turns persist without treating the current failures as passes.

Full event streams and restricted model artifacts remain local in `.offsecgym/`.
The PostgreSQL database after these runs is preserved at
`.offsecgym/diagnostics/m611-postgres.dump` (SHA-256
`d005de262a7bf9681c5d3ec0afd67a63af9ae6433b8f790820dc8e671908fbb0`).
It and raw model turns are ignored by Git and not published.
