# Milestone 6.0: atomic shared state and worker runtime

M6.0 establishes the controller boundary needed before coordinator agents.
The M5.4 historical executable remains frozen at code commit `0f27533`.
The [corrected M5.4 diagnostic](diagnostics/kimi-k3-openrouter-m54.md) retains
the original gate table: three unchanged rereads exceeded the one-repeat gate,
and one explicitly labeled second-invoice corroboration failed the literal
same-asset evidence gate. The bounded action index can evict old work, and
the evidence contract has no `primary` or `corroborating` role. These are
accepted baseline limitations. No Kimi tuning or new live-model experiment is
part of M6.0.

## Shared controller state

`PostgresEventStore.run_transaction` locks the run row for each decision and
can read and append events in that same transaction. WorldState fact writes,
adjudication, supersession, and coverage claims use this transaction, so two
controller processes cannot both claim the same active component/objective.
The local lock is used only by non-PostgreSQL test event stores.

`PostgresControllerState` persists run usage and reservations in the new
`0002_controller_state` migration:

- The first reservation declares the run's global budget in an event and
  stores its canonical hash. Later reservations must present that same budget;
  a worker task slice cannot silently replace the run limit.
- Each attempted action emits `ActionAttemptReserved` and reserves one
  action-budget slot and its action ID in the same transaction as
  `ActionRequested`; rejected scope checks still
  consume that attempt slot. Its exact fingerprint covers destination,
  method, path, identity, and canonical JSON body. A partial unique index
  permits only one active dispatch for a fingerprint in a run. Releasing
  it allows a later deliberate recheck with a new action ID.
- A second atomic decision checks and increments the HTTP dispatch slot,
  enforces the run's rate interval, and appends `ActionReservationAcquired`.
  Gateway completion, failure, or cancellation releases the reservation and
  appends `ActionReservationReleased`.
- Model-call count, provisional tokens, and provisional cost are reserved
  before a provider call. The model start event is appended in that same
  transaction. Provider-reported usage settles the reservation afterward.
  The preflight input estimate is `ceil(serialized UTF-8 request bytes / c)
  + margin`; the serialized model spec records `c` and `margin` (defaults 2
  bytes/token and 1,024 tokens). An M6.0 follow-up migration stores the split
  input/output reservation and request byte count. Reservation and settlement
  events record the split, actual provider usage, signed total reservation
  error, and signed input reservation error. Positive error means the provider
  reported more tokens than reserved.
  Across 278 completed local Kimi K3 model calls, outbound JSON bytes per reported
  input token ranged from 2.70 to 4.29. The default 2 bytes/token plus margin
  underestimated none of those calls; this is empirical calibration, not a
  tokenizer guarantee. Model-specific values for matched experiments must be
  set in the frozen experiment spec.
  The output bound remains the requested output cap.
  Cost uses configured token
  prices in integer microdollars. This conservative estimate prevents
  competing calls from spending the same *reserved* budget, but opaque
  provider tokenization or output-limit violations can still exceed it.
- Worker spawn counts and active slots are reserved transactionally against
  `max_workers` and `max_concurrency`. Spawn, start, and finish have distinct
  events. `WorkerDebriefed` is defined for M6.1.
- Coverage claims have paired `CoverageLeaseAcquired` and
  `CoverageLeaseReleased` events alongside the existing claim/update events.

Actions, action artifacts, response evidence, and model-call events carry
`worker_id` and `task_id` when run by a worker. Legacy monolithic events remain
readable. The global budget is available in `AgentContext` separately from a
future worker's task budget slice. The gateway no longer scans all prior run
events to decide action or HTTP budgets.

`project_controller_events` rebuilds counters, active action and coverage
owners, model reservations, worker statuses, and the last dispatch time from
the authoritative trace. The SQL tables are the fast shared decision state;
the event stream is the reconstruction and audit source.

## M6.0 acceptance evidence

An isolated migrated PostgreSQL database ran fake concurrent workers through
two independent engine/event-store instances:

- Twenty identical action requests produced exactly one active dispatch owner
  and nineteen `action_already_reserved` decisions. All twenty counted toward
  the action-attempt budget; one counted toward the HTTP budget. Releasing the
  owner permitted a later exact request with a new action ID.
- Twenty-four distinct actions competing for five action/HTTP slots produced
  exactly five reservations, with the SQL counters matching event replay.
- Competing WorldState instances produced one active coverage claim and one
  rejection for the same objective. Concurrent submission of one fact ID
  produced one fact and one duplicate rejection.
- Three worker spawns competing for two lifetime slots produced two spawns;
  only one started under a one-worker concurrency cap. Finishing it freed the
  active slot. Eight model calls competing for two call slots produced exactly
  two reservations, with token and cost reservations released on settlement.
- The controller event projection matched persisted counters after the tests.

These tests prove controller reservation behavior, not model competence or
parallel live-range throughput. The existing range runtime still uses a
process-local instance guard around dispatch and reset. M6.2 will need a
cross-process reset/dispatch barrier before enabling parallel live workers.
Crash reconciliation and stale-reservation recovery also remain to be built;
an interrupted owner can leave an active action or coverage lease. Provider
usage over a provisional token/cost reservation must be surfaced as an
accounting overrun, not silently treated as an exact hard cap.

## Next sequence

1. **M6.1 sequential orchestration.** One coordinator issues narrow tasks
   such as authorization mapping, invoice testing, ticket testing, public
   exposure, and transition verification. Each ephemeral worker receives an
   objective, budget slice, relevant entities/evidence, checked actions, and
   active coverage. It uses the existing gateway, worldview, and finding sink.
   It must return a typed debrief with new facts, hypotheses, candidate
   findings, completed coverage, open questions, and followups. The
   coordinator adjudicates the debrief into global state without carrying
   full worker transcripts between tasks.
2. **Matched diagnostic.** Keep the `0f27533` M5.4 runs as historical diagnostic
   evidence. The causal control is the **M5.4 monolithic structured policy run
   again on the current controller code**, compared with M6.1 sequential
   workers on that same code. Both arms use the same model, range, surface visibility,
   provider, validator, and global budget. Track root-cause recall, false
   findings, HTTP actions, exact and semantic duplication, worker overlap,
   evidence reuse, tokens/cost, time to first valid finding, coverage,
   abandoned branches, recovery after falsified hypotheses, and coordinator
   overhead. The first gate is infrastructure and attribution, not a worker
   score win.

M6.2 must order conflicting response facts by the source
`ActionCompleted.sequence_number`. Today `record_response` verifies the response
event and persists extracted facts in separate transactions; parallel workers
could write those facts in the opposite order. Sequential M6.1 workers do not
exercise that race.
3. **M6.2 concurrency.** Enable parallel workers only after sequential traces
   are sensible. Use the action and coverage reservations to distinguish
   independent confirmation from controller-race duplicates; add the
   cross-process range lifecycle barrier first.
4. **Budget-aware scheduler.** After competing worker proposals exist, log a
   heuristic utility estimate based on finding probability/value,
   information gain, cost, and redundancy. Learned scheduling is deferred.
