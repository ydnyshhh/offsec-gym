# M6.8 interface study: source audit and prospective design

**Status:** offline syntax qualification and historical replay only. The detached helper `m681_reference_syntax.py` parses prespecified public reference syntax in memory; it is not connected to the agent/controller runtime. This work does not implement a live treatment, qualify a range, freeze a manifest, approve spending, or run a model. The frozen M6.7 and M6.7.1 artifacts remain untouched.

## Repository basis

GitHub `main` was verified at `373453a7f2fbf79d44195cfe2338c16014f6b0f6` before this note was prepared. PRs [#67](https://github.com/ydnyshhh/offsec-gym/pull/67), [#68](https://github.com/ydnyshhh/offsec-gym/pull/68), and [#69](https://github.com/ydnyshhh/offsec-gym/pull/69) are merged. The source inspection below is from that exact commit. This note does not update the stopped M6.7.1 recovery or its separate approval.

The M6.7.3 report records 18 model-authored `start_experiment` proposals in 12 cells, 18 valid JSON argument objects, 18 strict `target_objects` validation failures, and no persisted experiment state. Of 43 proposed references, 42 contained a UUID inside another representation; 39 had a typed prefix. It found 35 identifiers in preceding model-visible content. That occurrence is not independent public-source provenance, typed object resolution, or correct-target correspondence. These are **historical observations**, not a replay against a new M6.8 treatment.

## Observed `start_experiment` call chain

1. `model_tools` in `src/offsecgym/solver/monolithic.py` adds `EXPERIMENT_TOOL_MODELS` when `experiment_commitment` is enabled. `_strict_schema` derives the provider-facing strict JSON parameters from the Pydantic model. `StartExperimentArgs` in `src/offsecgym/solver/experiment_state.py` requires UUID-valued `target_objects`.
2. `MonolithicSecurityAgent.run` builds tool definitions and request input. Before a later model turn, an active experiment can contribute a bounded, model-visible reminder through `ExperimentStateStore.reminder`.
3. The controller calls `provider.prepare_request`, writes the exact request artifact, records `ModelCallStarted`, calls `provider.complete`, writes the response artifact, and records `ModelCallCompleted`. PostgreSQL model-call reservation and settlement belong to this model-call path; this note does not invoke or change them.
4. For each provider `function_call`, the controller parses its argument string with `json.loads`, constructs the enabled schema map, and calls `schema.model_validate(raw_args)`. This is the strict argument boundary. A validation failure occurs **before** experiment dispatch.
5. A validated experiment call reaches `_dispatch`, which calls `ExperimentStateStore.change`. With a PostgreSQL store, `change` uses a run transaction. `_change` rejects a terminal run or conflicting active experiment, appends `ExperimentStateChanged`, and returns a view of the persisted state. The event is the authoritative activation observation.
6. The controller serializes the returned view as `function_call_output`; it is carried into the next model request. Later state retrieval or updates require separate model-authored calls and events. Stored model-marked progress is not a verified gateway action or security transition.
7. The rejection path records `ModelToolRejected` with a bounded reason code and returns `invalid_tool_call` feedback. The loop can stop after three invalid calls. The M6.7.3 audit observed this path; it did not observe a successful historical activation.

The prospective representation comparison therefore belongs at the controller's pre-validation boundary if a later reviewed source change proceeds. This PR does not wire the detached parser into that boundary. Changing `StartExperimentArgs`, the provider-facing schema, the tool description, or an HTTP tool would introduce additional interventions. An implementation review must verify schema identity between arms from serialized provider requests, not only from class names.

## Detached syntax qualification supplied here

`src/offsecgym/research/m681_reference_syntax.py` defines a pure anchored parser and an in-memory `StartExperimentArgs` replay. Its allowlist is pinned to the enterprise public entity types exposed by `range_surface`; the shorter `change` alias is deliberately unsupported. It accepts canonical bare UUIDs and `kind:canonical_uuid` strings for those public kinds. Extra text, multiple identifiers in one reference, unknown kinds, uppercase/noncanonical identifiers, descriptions, and encoded wrappers fail. It does not query a range, infer an object, create state, or modify model-facing tools. Acceptance remains an offline schema observation.

The focused synthetic tests assert strict control acceptance, typed-reference identity preservation, rejection of ambiguous syntax, failure on unrelated schema errors, and allowlist agreement with the audited public surface. They do not establish any treatment effect or persistence in a real range.

## Proposed E1 identification plan

**Question:** Does removing only a lossless representation mismatch increase model-authored experiment-state creation? Parsing success, public grounding, state maintenance, downstream action, and verified outcome are separate measurements.

**Arms:** The control retains the existing strict validation. A future opt-in treatment could recognize only a predeclared, anchored typed-reference grammar and pass its unchanged UUID value into the existing typed validation. It must reject unknown types, extra text, multiple identifiers, ambiguous wrappers, and descriptions. It must not resolve an entity, supply a target, consult hidden fixtures, or change provider-facing exposure. The precise allowlist remains **unresolved** until the public entity schemas are reviewed and recorded in a separate source change. This document does not select one from historical acceptance counts.

**Proposed cohort:** 16 fresh matched seed/block identities, each assigned once to control and once to treatment, for 32 excluded feasibility cells. Allocate four blocks to each B2-S3/B3-S2 by vulnerable/patched combination. Use independent range instances and model histories. Freeze seeds, assignments, and order prospectively; exclude historical evaluation seeds. This is a design proposal, not an execution manifest or paid authorization.

**Primary endpoint:** `experiment_state_created` for every assigned cell, established by an authoritative persisted event. Report no proposal as non-uptake, not parser failure. Report missing or unauditable cells explicitly. Estimate paired activation differences at the matched-block level and by checkpoint and variant; intervals are descriptive.

**Separate stages:** reference proposed; assigned syntax accepted; state persisted; independently public-source grounded; declared public object type resolved; state retrieved or updated later; planned action attempted; relevant gateway action completed; required observation and transition verified. Keep unknown values `null`. A model-visible echo does not prove public provenance. An experiment marked completed by the model does not imply verified task success. Report token, call, and HTTP overhead separately.

**Historical qualification completed offline:** The pinned, private M6.7.3 audit detail file (SHA-256 `34bc9b4a9348fcf3a324ea7265a6e8d53d076e5d8e96778354bee1b008093647`) was replayed against the original Pydantic schema, the prespecified grammar, and Pydantic validation after normalization. The checksum-guarded replay code is `research_ops/m681_replay.py`. It stores 18 per-call rows in a new mode-0700 private directory with a mode-0600 file; [the public aggregate](m681-historical-replay.json) contains no proposal IDs or UUIDs. Strict validation accepted 0/18 proposals. The grammar accepted 27/43 individual references, but only 4/18 complete proposals had every target eligible. All four passed Pydantic validation after lossless normalization. This is syntax qualification only; independent public-source provenance, object resolution, and correct-target correspondence remain unknown. Historical replay is not a treatment-effect estimate.

**No-model qualification still needed:** A later source PR should demonstrate strict control compatibility, one unambiguous treatment acceptance, malformed-reference rejection, persistence and later retrieval, exact UUID preservation, and identical provider-facing schemas in the actual controller. The focused tests here cover only detached syntax; they do not claim controller or Compose qualification.

## Independent follow-up studies

M6.8.2 must establish whether public information and actor authorization support an unambiguous B3 decision opportunity. Ambiguous candidates, incomplete information, absent permission, and deadline expiration need distinct outcomes. A structural candidate count alone does not prove authorized or informed action. No public-path automation is included here.

M6.8.3 should study generic post-action evidence selection in its own arm, with action completion and later correct-object observation verified separately. More reads alone are not evidence closure. A B2-focused 32-cell cohort is a proposed amendment to the earlier 64-cell B2/B3 design, not a frozen revision.

M6.8.4 should keep deadline slack and public information as separate interventions. The latter can change effective actionable opportunity even if the physical clock stays fixed. Neither effect should be attributed solely to model reasoning.

M6.8.5 should use detached synthetic requests to specify one context factor per condition. Exact output repetition is observable; hidden provider caching is not. No provider request is part of this note.

## Remaining gates

Before any M6.8 live study: review and merge the relevant source independently; pin exact execution-critical hashes; review a separate prospective protocol; complete no-model qualification; verify model and provider metadata, budgets, and stopped-evidence integrity; then obtain a distinct exact-scope private approval. No M6.7.1 approval or planning cost ceiling authorizes M6.8 execution. This note supplies none of those gates and makes no claim that M6.8.1 is ready for a paid protocol.
