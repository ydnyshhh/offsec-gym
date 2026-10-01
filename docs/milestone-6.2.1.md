# Milestone 6.2.1: matched sequential versus parallel workers

## Frozen comparison protocol

The worker agent, six M6.1.3 objectives and typed contracts, model prompt,
tools, gateway, worldview, finding sink, validator, range specification,
seed, surface visibility, provider, and reasoning setting are identical in
both arms. The known M6.1.3 strict-contract failures remain part of the
policy. This comparison does not tune those failures.

Both arms prepare all six bounded task packets from the same pre-worker
worldview snapshot. Each objective receives a fixed budget slice. Integer
compute limits are partitioned deterministically across the six workers;
unused slices are not transferred. The global controller budget remains the
hard shared ceiling. The old M6.1.3 rolling-slice diagnostic is a historical
baseline and must not be mixed into this comparison. The comparison changes
only the execution semaphore: one active worker or up to six active workers.
`WorkerScheduled` records queue entry, and existing lifecycle and action/model
events record attribution.

Configs:

- [`kimi-k3-m621-matched-sequential.yaml`](../experiments/configs/kimi-k3-m621-matched-sequential.yaml)
- [`kimi-k3-m621-matched-parallel.yaml`](../experiments/configs/kimi-k3-m621-matched-parallel.yaml)

Run one vulnerable repetition per arm first as a trace diagnostic, with no
statistical claim and no score gate. Before interpreting performance, require
all six workers to start and finish, matching packet policy and fixed slices,
zero active action/coverage/model reservations at finish, replay agreement
with SQL counters, zero duplicate active dispatch ownership, and sequential
worker overlap of zero versus positive overlap in the parallel arm. Keep the
parallel arm's strict-contract failures in the result.

Report validated root-cause recall and false findings; HTTP actions; exact
and manually annotated semantic duplication; reservation conflicts; coverage
contention; evidence reuse; token/cost use; coordinator model-call overhead;
worker queue wait and overlap; time to first and last useful finding; and
abandoned branches or recovery from falsified hypotheses. Estimate wasted
parallel compute by annotating tokens/actions used after equivalent work was
already resolved by another worker. Do not infer a causal speedup from one
pair: this first run checks whether the comparison is executable and whether
the trace supports those measurements.

The [one-pair diagnostic](diagnostics/kimi-k3-openrouter-m621.md) is complete.
Infrastructure and packet-equivalence gates passed. Both arms scored 0/5;
the matched initial packets lacked object IDs, so this is a scheduling and
trace-quality result rather than an orchestration score result.
