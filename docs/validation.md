# Independent validation

A solver produces `CandidateFinding`, never a canonical success verdict. A candidate carries
an authorization, field-exposure, or state-transition expectation, a concrete claim, and
exact evidence/action IDs. It cannot submit a hidden property or root-cause ID. The typed
hidden `GroundTruthManifest` supplies those canonical IDs, and `ValidationResult` may cite
matched UUIDs. Validation must check schema, instance generation, evidence provenance,
identity and object relationships, expected policy, observed effect, and hidden oracle
mapping. Replay uses a reset or cloned synthetic range when a finding depends on mutable
state; evidence from a previous generation cannot prove a claim about the current one.

Verdicts are `validated`, `rejected`, or `inconclusive`. Missing proof is rejected;
unavailable replay infrastructure is inconclusive. Keep each check and its reason code in
`ValidationResult`. Oracle data and validator credentials are inaccessible to the solver.

Milestone 2.5 supplies these contracts and tests, not production verdict logic. Build the
deterministic validator and scripted solver before LLM experiments. Later
validation ablations may compare solver self-judgment, fresh-model review, deterministic
checks, and hybrid review over the *same stored candidates*.
