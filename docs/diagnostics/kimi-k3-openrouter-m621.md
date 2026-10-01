# Kimi K3 OpenRouter M6.2.1 matched scheduling diagnostic

## Protocol and scope

This one-pair diagnostic used the predeclared [M6.2.1 protocol](../milestone-6.2.1.md)
at `9af0253` (scheduler `2465e16`). Both runs used vulnerable build
`c435a866-da5a-5f65-a2b5-ba2e4c2cc4db`, seed 1, range seed 42, known
routes, deterministic validation, OpenRouter `moonshotai/kimi-k3` with high
reasoning, Moonshot AI upstream and fallback off. The global limits were
60 actions, 60 HTTP requests, 20 model calls, 120,000 tokens, 8,192 output
tokens per call, and 600 wall seconds. Six fixed task slices and normalized
packet contents matched exactly by objective across arms. The execution
semaphore was one worker or six workers. This is a trace diagnostic, not a
statistical comparison or a comparison to the earlier rolling-packet M6.1.3
runs.

| Measure | Matched sequential | Matched parallel |
| --- | ---: | ---: |
| Run ID | `b8799de4-8b40-4585-b94d-f9975b3541d3` | `bfd922ad-dbd6-4c52-97f5-c89b329a44a4` |
| Status / score validity | `agent_failed` / true | `agent_failed` / true |
| Validated roots / false findings | 0/5 / 0 | 0/5 / 0 |
| Elapsed run time | 318.17 s | 100.75 s |
| Model calls / total tokens | 13 / 51,184 | 14 / 50,289 |
| Peak model input | 5,209 | 4,929 |
| HTTP dispatches | 29 | 27 |
| Exact repeats after a prior completed action | 20 | 19 |
| Cross-worker exact repeats | 19 | 19 |
| Active reservation conflicts | 0 | 0 |
| Worker overlap pairs | 0/15 | 15/15 |
| Mean schedule-to-start wait | 149.75 s | 0.18 s |
| Coverage completed / released | 1 / 5 | 1 / 5 |
| Objective HTTP actions | 10, all identity | 8, all identity |
| Strict-contract violations | 5 | 5 |
| Findings reusing another worker's evidence | 0/0, undefined | 0/0, undefined |

Both arms submitted no findings, so time to first or last valid finding,
useful evidence reuse, and false-positive precision are undefined. Dollar
cost is unavailable because the frozen model spec has no token prices.
Coordinator model calls were zero in both arms. All six packets and debriefs
were present; the maximum packet was 1,135 characters.

## Trace annotation

Every dispatched HTTP request in both runs was `GET /api/me`. There were nine
unique exact request fingerprints in the sequential arm and eight in the
parallel arm. The remaining 20 and 19 dispatches repeated a fingerprint after
its previous action had already completed. Nineteen repeats in each arm came
from a different worker. There were no additional distinct-route semantic
duplicates to classify. The parallel arm had no simultaneous active-owner
collision; its duplication occurred after prior reservations were released.
Thus the zero reservation-conflict rate is compatible with a very high
redundant-action rate (20/29 and 19/27).

The identity worker fulfilled its route objective. Document, invoice,
ticket, public-exposure, and refund workers in both arms made no matching
objective action and did not emit `WorkerBlocked`. All ten finished with
`WorkerContractViolated(reason_code=task_block_required)` and failed. There
were nine typed `worker_route_mismatch` tool rejections in sequential and
eight in parallel; the parallel trace also has one `invalid_tool_call` and
one `invalid_tool_arguments`. No refund POST was dispatched. These are the
known strict-contract limitations retained from M6.1.3, amplified by the
matched packet protocol.

Preparing all six packets before any worker acted made the packet comparison
exact, but each packet had zero relevant entities, evidence references, and
prior checked actions. Later specialists could query the shared worldview,
yet their strict one-turn action phase did not result in object detail tests.
The previous rolling-packet M6.1.3 diagnostic had at least one validated
public-exposure root per run; this new matched-packet pair therefore must not
be interpreted as a direct score comparison with M6.1.3. It isolates the
effect of scheduling on this fixed, weak packet state.

There are 19 **candidate redundant actions** in the parallel arm: exact
requests already completed earlier. The trace cannot prove that each was
useless independent confirmation. By worker-local event order, five model
turns that generated at least one such repeat used 17,839 tokens. That is an
upper bound on model compute associated with those actions, not an exact
`WastedParallelCompute` value, because a turn may also have processed other
information. The analogous sequential figures are 20 actions, six turns,
and 21,804 tagged tokens. The event schema does not
link individual HTTP tool calls directly to their model turn, so this
turn attribution is inferred from each worker's event order. Recovery after
a falsified hypothesis cannot be assessed from these traces; five non-identity
objectives were abandoned by contract failure in each arm.

## Infrastructure gate audit

All six workers started and finished in each run. The parallel arm had 15/15
overlapping worker pairs; the sequential arm had none. Each run acquired and
released six coverage leases. Event replay found zero active workers, action
reservations, coverage leases, and model reservations at completion. Replay
matched the PostgreSQL action, HTTP, model-call, token, and reserved-token
counters. No `WorkerLeaseRecovered` event or controller/provenance error
occurred. Packet policy and fixed slices matched by objective, and no active
duplicate action reservation was observed. Hosted CI passed for `2465e16`
and `9af0253`.

The result supports a narrow observation: with these fixed empty packets,
parallel scheduling overlapped model work and reduced elapsed time in one
pair, while redundancy remained high and validated recall stayed zero.
It does not establish a general speedup or a useful multi-worker benchmark.
Coverage contention was not exercised because the six coordinator claims
have distinct objectives. The next research protocol should provide the
same useful prerequisite state to both arms while keeping scheduling as the
only changed variable; dependency-aware orchestration remains separate.

Full event streams and restricted model/request artifacts remain local under
`.offsecgym/`. The isolated PostgreSQL snapshot is
`.offsecgym/diagnostics/m621-postgres.dump` (SHA-256
`5bdaa7979f475e3f1e712d88511d690c247b531892764b3c73d113f9431fa4d2`).
