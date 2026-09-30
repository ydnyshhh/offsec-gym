# Milestone 3: scripted oracle and evaluator

The scripted SaaS agent uses `Agent.run(task, context, tools)` with a white-box identity-ID
projection. It discovers the member account through `/api/me`, then uses ordinary gateway
requests to enumerate its workspace, follow document and ticket references, test foreign
object reads, test the public invoice preview, and test a member refund. It submits typed
`CandidateFinding` records through an event-backed finding sink. It never loads fixtures,
the attack graph, or hidden ground truth. The controller chooses the visibility policy and
allowed identity IDs; future model agents can use the same tool and finding interfaces.

`DeterministicValidator` first checks the candidate's run, instance, and generation. For
each cited evidence ID it loads the restricted response and request artifacts and checks
their run, instance, generation, action ID, identity, and request-artifact ID. It then
checks matching v2 `ActionRequested` and `ActionCompleted` events, event order, request
hash, response status/hash, and terminal uniqueness. A broken link is rejected before
the hidden oracle is consulted. Unavailable event storage is inconclusive.

The validator then loads the oracle via `load_for_context`, matches the candidate's typed
expectation and object to a property, and dispatches proof checks by requirement type:
identity, object relation, response status, response field, state transition, and anonymous
request. There are no property-slug branches. Identity and relation checks use the verified
build fixture, not values supplied by a candidate. Patched properties are rejected even if
an artifact purports to show a successful response.

Refund proof needs a paid GET before the POST and a refunded GET afterward in the same
generation. `CloneReplayVerifier` creates a new instance of the same build, performs those
three actions through the gateway under a separate run ID, and destroys the instance.
`ValidationResult.replay_evidence_ids` records the new proof separately from the solver's
evidence. Infrastructure or cleanup failure yields an inconclusive result.

The evaluator counts one true positive per active root-cause ID, records duplicates, and
counts rejected findings as false positives. Active roots without a validated finding are
false negatives. A patched run with no candidates has zero false positives and no active
roots; precision and recall are undefined. If the gateway, replay, or validation
infrastructure fails, the run is marked `environment_failed` and contributes no agent
precision or recall denominator.

Run the example with a migrated PostgreSQL database and Docker available:

```sh
export OFFSECGYM_DATABASE_URL='postgresql+asyncpg://USER:PASSWORD@HOST/DB'
uv run alembic upgrade head
uv run offsecgym experiment run experiments/configs/scripted-saas.yaml --paired
```

The two runs have separate event streams and build IDs but share a compiler-version-scoped
pair ID. The current runner is single-process and serial. The action gateway still scans
the full run history and uses an in-process rate limiter; durable reservations are needed
before concurrent multi-agent experiments.
