# Milestone 6.2.3: hard worker compute escrow

M6.2.2 is frozen [with its failed behavioral gates](diagnostics/kimi-k3-openrouter-m622.md).
This final M6.2 scheduling protocol holds its GET-only prerequisite bootstrap,
six objectives, worker prompts, tools, packet selection, strict contracts,
model, range, validator, visibility, global compute caps, and fixed slices
constant. It changes only the accounting authority for those slices. It does
not transfer unused worker budget or introduce a dependency scheduler.

## Atomic escrow

Before scheduling, the coordinator creates six immutable PostgreSQL worker
accounts in one run transaction. Their declaration events allow reconstruction
from the event stream. `_fixed_slice` assigns 20,000 tokens to each worker;
model-call limits are 4, 4, 3, 3, 3, and 3 in objective order; action and
HTTP limits are 10 each. Together they equal the worker phase's 120,000
tokens, 20 calls, 60 actions, and 60 HTTP requests. The bootstrap keeps its
separate 32-action/HTTP cap and zero model use; its actual use is reported
separately.

Action attempts, HTTP dispatches, and model reservations update both the
run-wide counters and the owner account atomically. Model preflight checks
`used + reserved + estimated` against both limits before provider dispatch.
Settlements and crashed-worker reconciliation update both counters in the
same transaction. Unused account capacity stays with its worker. A provider
report larger than the preflight estimate remains possible; the diagnostic
must audit actual settled usage against each account limit rather than assume
the estimate is infallible.

Configs:

- [`kimi-k3-m623-escrowed-sequential.yaml`](../experiments/configs/kimi-k3-m623-escrowed-sequential.yaml)
- [`kimi-k3-m623-escrowed-parallel.yaml`](../experiments/configs/kimi-k3-m623-escrowed-parallel.yaml)

## Predeclared one-pair diagnostic

Run one vulnerable repetition per arm with OpenRouter `moonshotai/kimi-k3`,
high reasoning, Moonshot AI upstream, fallback off, seed 1, range seed 42,
known routes, and deterministic validation. Run sequential first, then
parallel. Do not tune or rerun a failed gate to seek a better score.

Check before interpreting performance:

1. The GET-only bootstrap makes zero model calls and produces the same
   normalized snapshot in both arms. All six initial packets match after
   normalizing run-specific IDs and carry the same fixed slices and targets.
2. All six declared account limits match the partition above in both arms.
   Every worker receives at least one model call. No reservation or actual
   settled use exceeds its account, and no worker consumes another account's
   capacity. SQL account counters agree with event replay.
3. All workers start, finish, and debrief. There are no provenance, replay,
   reservation, or range-generation leaks. Sequential overlap is 0/15 pairs;
   parallel overlap is 15/15.
4. At least four of five non-identity workers execute an objective route in
   each arm. Record strict-contract violations exactly, including refund
   failures; do not alter the contract or prompt to pass this gate.
5. Record root-cause recall, false findings, HTTP actions, exact and manually
   annotated semantic duplication, cross-worker evidence reuse, total and
   per-objective calls/tokens, wall time, queue wait, time to first and last
   useful finding, account headroom, and token-bearing turns that generated
   redundant actions. There is no winner or score gate for one pair.

If accounting and behavioral gates clear, freeze M6.2 as the scheduling
baseline. If any fail, preserve the failed result and keep M6.3
dependency-aware and budget-aware scheduling as a separate protocol.

The [one-pair diagnostic](diagnostics/kimi-k3-openrouter-m623.md) is complete.
Escrow, state matching, packet matching, replay, and concurrency gates passed.
All six workers got one call, but every second-turn reservation exceeded its
20,000-token account. Both arms missed the four-of-five objective-action
gate and submitted no finding. Preserve this failed behavioral result.
