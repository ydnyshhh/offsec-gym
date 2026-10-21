# M6.8.1.1 detached persistence review

## Inspected interface and control flow

`EventStore` exposes `append(event)` and `read_run(run_id)`. `PostgresEventStore.run_transaction(run_id)` locks the run row and yields a `RunTransaction` with those methods. `ExperimentStateStore.change(run_id, name, args, *, causation_id)` uses that transaction for PostgreSQL, then `_change` reads the run, rejects a `RunCompleted` run, applies the existing active-experiment lifecycle rules, appends one `ExperimentStateChanged` event for a mutation, and returns its view. `active(run_id)` reconstructs active state from that run's events. The event carries the experiment fields, status, and sequence. `StartExperimentArgs` is the unchanged strict schema. The existing detached parser accepts only its anchored grammar and has no runtime caller.

The qualification harness will validate scripted input before calling `change`. In strict mode it uses `StartExperimentArgs` directly. In normalization mode it parses every target reference with the frozen parser, creates a copy containing the same UUIDs, and then validates the unchanged schema. Scripted later turns call the same state store. A fresh `PostgresEventStore` connection will test durability. Historical taxonomy is read-only and publishes aggregate counts; private rows stay outside Git.

This design does not instantiate the controller, model provider, HTTP gateway, range, or hidden-root resolver. It does not qualify a live autonomous-agent treatment.

## Implementation and measured boundary

`m681_detached_persistence.py` accepts a fabricated run ID, `SyntheticRunContext`, scripted arguments, a normalization switch, and fixed scripted operations. It validates before any event write. Strict mode sends an untouched copy to `StartExperimentArgs`; normalization mode parses the entire target array and replaces it with a lossless UUID list only if every entry is eligible. Both modes use `ExperimentStateStore.change` for state mutation. The caller supplies only an `EventStore`, never an agent, provider, gateway, or range. A generated experiment ID is taken from the real start result for later scripted turns.

The unit suite tests strict error equivalence, immutability, supported and rejected syntax, event-free failures, step bounds and regression, completion, abandonment, cross-run isolation, duplicate start, and terminal behavior. PostgreSQL integration tests use two independent engines against a disposable migrated `offsecgym_test` database. One test starts state, disposes the first engine, retrieves and updates through the second, checks completion and isolation, and verifies terminal rejection. Another races two starts on the same fabricated run; the run lock permits one event and one `active_experiment_exists` result. The qualification generator repeats complete and abandoned scripted lifecycles twice, compares a canonical projection byte-for-byte, and retains hashes of the real events privately.

`get_active_experiment` intentionally returns `active: false` after completion or abandonment. Terminal status is still visible through the authoritative event history. This follows the original state-store contract; no new completed-experiment lookup was introduced. `current_step` remains model-declared completed-step count, not a validator's observation. The test's synthetic completion produced zero task-verdict events.

## Historical rejection taxonomy

The checksum-pinned retained input reconstructed exactly 18 proposals and 43 references. The frozen parser accepted 27 references. Four complete proposals were eligible and passed the unchanged schema after normalization. The other fourteen comprise thirteen proposals that mixed eligible and ineligible references and one proposal with only UUID wrappers outside the grammar. Proposal dispositions are mutually exclusive and sum to eighteen.

Reference-level categories are 27 supported typed UUIDs, 12 unsupported typed UUIDs, three other UUID wrappers, and one natural-language reference. Non-exclusive proposal flags show unsupported kinds in twelve proposals, invalid wrappers in two, a natural-language reference in one, and multiple references with at least one invalid entry in fourteen. The flags overlap; they must not be added as proposal totals. No additional schema-field failure was observed. The private ordinal table and original identifiers remain in ignored mode-0600 files. Public reports contain only counts and source hashes.

The retained audit marked 35 of 43 identifiers as seen in model-visible context, but did not independently verify public-source provenance for any of the 43. Context visibility is not provenance. Object correspondence and correct target selection remain unknown. The taxonomy identifies representation syntax as a measurable obstacle for this frozen grammar; it does not apportion a causal contribution among public grounding, tool feedback handling, multi-reference construction, or state persistence in the historical agent runs. Historical state activation remained zero because the replay never dispatched state changes.

## Qualification limits and next review

`live_autonomous_agent_treatment_qualified = false` and `prospective_model_effect_estimated = false`. The harness demonstrates the existing state transaction under fabricated, scripted input. It does not show that a model proposes, retrieves, updates, or uses an experiment in a live run. It does not validate security findings or range transitions.

Any prospective experiment would require an independent review of a separate runtime proposal and its model-facing behavior, an independent no-model qualification of that exact runtime diff, and a separately reviewed protocol, source pins, cost cap, and exact run approval. None is created or authorized here.
