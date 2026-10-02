# M6.4 v2 worker-policy preflight

## Frozen inputs and scope

The v2 range compiler and matrix planner are at `c477d23`; the worker
pilot tests are at `cc34d83`; the analysis rules are at `53004af`; and
model-endpoint event capture is at `4de2249`. The prospective
[`m64-v2-worker-primary-1` manifest](../../experiments/manifests/m64-v2-worker-primary-1.json)
pins source commit `4de2249`, range compiler
`tenant-boundary-v2/compiler-1`, seeds 1001–1010, paired build and public
fixture hashes, the three worker config hashes, model request settings,
budget ceilings, experiment hashes, and a deterministic randomized run
order. M6.3.1 worker prompts, task contracts, minima, and scheduler utility
weights are unchanged. Seed 42 was not rerun.

The primary arms are fixed sequential workers, matched parallel workers,
and opportunity-aware sequential workers. All use the same audited
bootstrap, known routes, structured worldview, provider request, and
validator. Monolithic does not yet share this bootstrap boundary and is
outside this primary matrix. Hard partition and greedy elastic remain
frozen diagnostic arms.

## Checks completed

| Check | Result |
| --- | --- |
| v2 paired public fixtures | Exact match for vulnerable/fully patched siblings at all ten seeds |
| Real Docker scripted oracle/validator | Ten seed pairs passed; each vulnerable build had five distinct validated roots; each patched build had zero findings |
| v1 regression | Seed-42 build ID remains `c435a866-da5a-5f65-a2b5-ba2e4c2cc4db`; existing scripted pair passed |
| Matrix determinism | 300 distinct cells; 180 feasible with orders 1–180; 120 fixed-worker cells rejected below the 117k one-turn floor |
| Fake-provider worker pilot | 12 passed: three arms × vulnerable/patched × each arm's smallest/largest feasible budget, all on v2 seed 1001 |
| Worker pilot integrity | Every run scored validly, bootstrapped visible identities and assets, prepared bounded packets, and released active worker/action/coverage/model reservations |
| Local lint and fast tests | Ruff check/format clean; 153 non-Docker/non-PostgreSQL tests passed |
| Analysis rules | Complete-sample and matching checks, invalid-score handling, paired seed differences, bootstrap intervals, and both curve definitions passed synthetic tests |
| Selected endpoint visibility | All 12 archived M6.3.1 OpenRouter responses and all six new non-held-out pilot calls selected `moonshotai/kimi-k3-20260715` via `Moonshot AI`; the new completed-model event retained both fields |

The fake-provider pilot deliberately returns `task_blocked`. Its scores say
nothing about model finding quality, and its traces are excluded from the
confirmatory sample. Fixed-worker 40k, 60k, and 80k cells are structural
infeasibilities; no provider call will be made for them. Opportunity-aware
workers remain feasible at those budgets.

## Bounded live provider pilot outside the sample

Two opportunity-aware runs used **v2 seed 1101**, outside the ten held-out
seeds, at a 40k model-token budget and an additional $1 configured cost cap
per run. Their builds share pair ID
`7c46302a-1df7-5eed-9644-564cb1690f3b` and bootstrap snapshot SHA-256
`0e50d8ea223b31b19729e859054c8d4c3398e31aeec059ee4ecff3acbe8ba4a1`.

| Measure | Vulnerable | Patched |
| --- | ---: | ---: |
| Run ID | `c5177c9c-2cb0-4958-82e1-281d9c189b65` | `a286bd4d-a22a-44b4-a958-30ef1ceea2f4` |
| Status / score valid | `completed` / true | `budget_exhausted` / true |
| Distinct true positives / false positives | 1 / 0 | 0 / 0 |
| Candidate duplicates | 2 | 0 |
| Model calls | 3 | 3 |
| Input / output tokens | 22,348 / 1,796 | 21,490 / 1,179 |
| Estimated token cost | $0.093984 | $0.082155 |
| Wall time | 82.09 s | 68.88 s |
| Worker HTTP dispatches | 3 | 6 |
| READY / admitted / executed objectives | 4 / 1 / 1 | 4 / 1 / 1 |

Every completed model-call event carried the same selected endpoint revision
and upstream provider. Replaying all 207 vulnerable and 221 patched events
left zero active workers, action or coverage reservations, model calls, and
admission holds. The private trace export is
`.offsecgym/diagnostics/m64-v2-nonheldout-pilot-traces.json`, SHA-256
`2dfa7d0bc5bfc1a6220046e6420bbf24c12a369397855452f83073277d4851d5`.
The report and raw artifacts remain in ignored `.offsecgym/`. These two
pilot scores are diagnostic and must not enter the confirmatory matrix.

## Prospective live expense

The 180 feasible cells have **20.4 million configured worker model tokens**
and at most **3,600 model calls** before any provider or controller failure.
Provider-reported usage can exceed a preflight token reservation; the sum
is not a strict billing cap. At the 2026-10-02
[OpenRouter Kimi K3 listing](https://openrouter.ai/moonshotai/kimi-k3),
the pinned Moonshot AI upstream lists **$3/M input** and **$15/M output**.
Charging all configured tokens at the output rate gives a **$306 token-only
planning estimate at that price snapshot**, not an enforced USD limit. Pricing
can change; input/output mix, fees, and failed requests affect the bill.
The existing 120k M6.3.1 run took 333.59 seconds. At a proposed 900-second
limit per live cell, 180 serial cells imply 45 hours at that nominal limit,
plus range lifecycle overhead. The synchronous provider call may outlive
coroutine cancellation, so wall time is not a strict cap yet.

## Remaining preflight gates

1. Review the full manifest and spending envelope before live collection.
   `usd_ceiling` remains unset because no enforced cost budget is configured.
2. During collection, stop or partition the analysis if a selected endpoint
   changes or metadata becomes unavailable; the predeclared analyzer rejects
   a mixed revision/upstream sample.

No confirmatory Kimi/OpenRouter run has started for M6.4.
