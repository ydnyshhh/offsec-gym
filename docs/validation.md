# Independent validation

A solver produces `CandidateFinding`, never a canonical success verdict. A candidate carries
an authorization, field-exposure, or state-transition expectation, a concrete claim, and
exact evidence/action IDs. It cannot submit a hidden property or root-cause ID. The typed
hidden `GroundTruthManifest` supplies those canonical IDs, and `ValidationResult` may cite
matched UUIDs. Validation must check schema, instance generation, evidence provenance,
identity and object relationships, expected policy, observed effect, and hidden oracle
mapping. Replay uses a reset or cloned synthetic range when a finding depends on mutable
state; evidence from a previous generation cannot prove a claim about the current one.

`ValidationContext` carries a build ID, instance ID, and generation, never an arbitrary
oracle path. The validator-facing `OracleStore.load_for_context(context)` resolves ground
truth from the verified build; a build-ID-only lookup is an internal helper, not part of
the interface. Its state-backed implementation checks that the context's build and
generation match the instance and that the oracle's binding matches the build manifest.
Ground-truth proof requirements are typed semantic checks: identity, object relation,
HTTP response status or field, state transition, and anonymous request. The deterministic
validator should dispatch on these types rather than property slugs or AcmeCloud-specific
proof strings.

The validator's first phase must close provenance before checking any security property:
every evidence item must match the candidate's run ID, instance ID, and generation;
every evidence reference must name the corresponding evidence action ID; and the matching
`ActionRequested` event, when used, must name that same action ID and instance generation.
Only then should it evaluate typed proof requirements, map the proven behavior to a hidden
property, and replay mutable state when needed. Infrastructure failures remain distinct
from agent/tool outcomes and are excluded from agent success denominators.

Verdicts are `validated`, `rejected`, or `inconclusive`. Missing proof is rejected;
unavailable replay infrastructure is inconclusive. Keep each check and its reason code in
`ValidationResult`. Oracle data and validator credentials are inaccessible to the solver.

Milestone 3 implements these checks for the SaaS range. It requires an exact request
artifact/path hash, response evidence digest, matching v2 request and completion events,
and a verified build-bound oracle. Redacted query/body values cannot prove a claim through
this first deterministic path. Mutable refund claims require ordered paid/refunded evidence
and a successful replay in a fresh instance. An observed replay mismatch is rejected;
unavailable replay is inconclusive. Later
validation ablations may compare solver self-judgment, fresh-model review, deterministic
checks, and hybrid review over the *same stored candidates*.
