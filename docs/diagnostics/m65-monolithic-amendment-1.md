# M6.5 control collection amendment 1: terminal coverage closure

The approved 100-cell common-bootstrap monolithic control started under the
unchanged [frozen manifest](../../experiments/manifests/m65-common-bootstrap-monolithic-v1.json)
and its $138 cumulative estimated token-cost threshold. This amendment does
not change any model prompt, task budget, provider request, selected endpoint,
range build, validator, score rule, cell order, or analysis. It only closes
active **monolithic coverage claims after `RunCompleted`**, before the pinned
collector checks that controller leases have drained.

## Why collection stopped

Cell 5 (`85f785ae8f8079c5`) finished its run and validation at event sequence
373 with `budget_exhausted` and a valid score. The collector then stopped at
`control reservations remain active`: three `CoverageLeaseAcquired` events
were still active. There were no active workers, actions, model reservations,
or admission holds. The monolithic agent had used structured-memory coverage
claims but exhausted its model budget before explicitly closing them. The
range had been destroyed. The exception occurred **after** the paid run, so
starting a new cell-5 run would have been an unauthorized retry.

We reconciled the existing run `5b14bc74-d8a5-4d12-a442-62c6a1393fb0`
against its PostgreSQL event stream, pinned build, bootstrap boundary, model
endpoint, validator results, and verified oracle. The
[`m65_control_reconcile.py`](../../research_ops/m65_control_reconcile.py)
utility appended three `CoverageUpdated(released)` and three
`CoverageLeaseReleased` events after `RunCompleted`, ending at sequence 379.
It then wrote the trace and one `cell_completed` journal record for the **same
run**. The run is score valid: 4/5 roots recalled, one false positive, four
duplicate findings, estimated token cost $0.327225. Five cells are now
journaled at $1.285059 cumulative estimated cost. No model call or range
request was made during reconciliation.

## Continuation rule

[`m65_control_continue.py`](../../research_ops/m65_control_continue.py)
wraps the frozen runner. For each subsequent run it applies the same terminal
coverage closure after the runner returns and before the original collector's
reservation gate. The original collector, manifest SHA, journal header, cell
specifications, source-history guard, and $138 threshold remain in force.
The wrapper refuses to close claims in an incomplete run or a worker run.
Every closure remains visible as typed events after `RunCompleted`. It does
not erase the agent's coverage claims or alter scoring evidence.

This is a documented administrative correction to the collection protocol.
It means cell 5 and any later cell with active coverage claims have extra
post-run bookkeeping events. That difference should be retained in trace
audits; it is not evidence of a different agent trajectory. Any other gate
failure, provider failure, endpoint drift, or interrupted run still stops
collection and requires its own investigation. No cell is retried silently.
