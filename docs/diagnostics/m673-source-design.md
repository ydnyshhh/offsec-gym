# M6.7.3 source design

Status: research implementation design. This milestone makes no provider request, runs no collector, and leaves the frozen M6.7.1 source, protocol, manifest, traces, journal, receipts, validators, and reservations untouched.

## Evidence boundary

The audit accepts the frozen manifest only at SHA-256 `d51ba9d9dffe12c6e809b32196f2c48937f72e33768ffb2ad050218d4be41e2f`, source commit `f5273e6d59d2810ac7de2646c238a2d0672dc042`, and protocol commit `86c5406024e961eaaeacb851d9bf51d4a550df04`. It admits each historical continuation through the existing stopped-evidence verifiers. It reads PostgreSQL with a read-only transaction and checks every trace digest against the corresponding receipt. Model requests and responses are read with `ModelCallArtifacts.read_verified`; HTTP evidence is accepted only through the existing checkpoint artifact verifier. The public report contains aggregate counts. Private per-call material stays in an ignored, mode-0700 output directory.

Every assigned row is retained. `assigned`, `started`, `terminal`, `trace_auditable`, `score_valid`, `resource_auditable`, and `stage_observable` are distinct states. Unstarted or unauditable assignments remain unknown, never negative outcomes. Unexpected analyzer errors abort.

## Separation of public information from oracle material

The first model request and its verified later request artifacts define model-visible information. Controller setup traces and bootstrap evidence are included only when their content was actually carried into the request. The checkpoint receipt, `private_anchors`, range fixture, ground-truth manifest, and frozen validator may identify the target **offline** after a public candidate path has been reconstructed; none is an input to a public-information resolver or model-visible treatment. In particular, `verified_public_state` in a private receipt is not automatically model-visible. Source request fingerprints are reused from `m67_repeat_analysis.py`.

## Analysis units and limits

The experiment-interface unit is a model-originated `start_experiment` function call, keyed by run, model call, and tool-call ID. Replay proposed JSON against the unchanged strict Pydantic schema and offline alternate schemas. Parse acceptance, public grounding, uniqueness, target correctness, controller feedback, and later repair are separate fields. Alternate schemas are never dispatched to the event store.

B3 opportunity reconstruction begins with the exact first model request. Physical reachability may use a private target anchor for a separate counterfactual, while epistemic identifiability uses only public data and route relationships. Ambiguous candidate sets and inaccessible prerequisites remain unknown or underdetermined. The historical worker clock is read from verified event and checkpoint evidence; no environment is restarted.

B2 closure begins at each verified model-originated approval HTTP response. It then checks later model-originated reads, actor/object binding, timing, and visible model output. An approval HTTP 200 is a descriptive successful operation, while the frozen transition remains false unless its own later-observation rule is met.

Reporting proposals are keyed by run/model/tool-call ID and given exactly one final disposition. Validation error flags may overlap. Repeated outputs use the existing `request_fingerprint` and `classify_adjacent` functions; decision relevance is assigned only from an explicit current object/route/hypothesis relation visible before the response, otherwise unknown.

## Prospective qualification boundary

E1 schema representation, E2A deadline slack, E2B public information, E3 generic post-action closure, and E4 context sensitivity remain separate proposed protocols. This PR may parse synthetic arguments, simulate a clock, or generate static request perturbations. It cannot create an active experiment, mutate a range, call a provider, select live seeds, or freeze a paid study.
