# Milestone 6.2.0: concurrency correctness

This patch prepares the frozen M6.1.3 worker policy for a separate scheduling
comparison. It does not change worker objectives, prompts, tool permissions,
packet selection, or the six-worker order.

## Shared-state rules

- Controller-extracted facts for one `(subject, predicate)` are superseded by
  the sequence of their source `ActionCompleted` event. A response processed
  late cannot overwrite a newer gateway result. List membership predicates
  retain distinct child objects. The event stream records every adjudication.
- Dispatch and reset for a range instance use one OS file lock under its state
  directory. The generation is checked while the lock is held, and a crashed
  process releases the lock. All controllers sharing an instance must share
  the same local state directory and host; this is not a distributed lock.
- Spawned and started worker slots have durable expiry timestamps. A live
  worker holds a PostgreSQL session advisory lock and heartbeats its lease.
  Another controller can reconcile an expired slot only when that lock is
  free. Reconciliation releases active action and coverage reservations,
  settles model reservations, closes unfinished calls and actions, and emits
  `WorkerLeaseRecovered` plus a failed `WorkerFinished`. Replay reconstructs
  the same accounting. Completed model calls retain their recorded token and
  estimated cost usage. Repeated recovery is idempotent.
- A worker that fails during packet preparation is finished explicitly. New
  action and model reservations from a finished worker are rejected.
- `ModelToolRejected` records stable reason codes and worker/task ownership.
  For HTTP proposals it keeps the method and SHA-256 of the proposed path,
  without storing raw model arguments in the event.

## Verification

PostgreSQL integration tests exercise competing reservation attempts,
expired-slot recovery, advisory-lock exclusion, a crash between spawn and
start, normal setup failure, and recorded model usage after a crash. Unit tests
exercise reverse response processing and a second process contending for an
instance lock. The complete local suite passed with PostgreSQL and Docker.

## Boundaries before M6.2.1

Recovery covers worker-owned reservations. A model call that has reached the
provider but has no persisted completion has unknown actual usage; it is
settled at zero and the failed call remains explicit in the trace. Generic
non-worker reservations and orphaned artifact files still require their own
retention/reconciliation policy. This patch makes no claim about cross-host
range locking or provider-side exactly-once execution.

The next experiment must retain M6.1.3's worker contract and vary only
sequential versus parallel scheduling. Do not interpret a six-worker parallel
run as a score improvement until packet timing and budget-slice equivalence
are controlled and reported.
