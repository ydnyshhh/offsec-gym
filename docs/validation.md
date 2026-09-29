# Independent validation

A solver produces `CandidateFinding`, never a canonical success verdict. A candidate must
state the violated security property and reference exact evidence/action IDs. Validation
checks schema, provenance, identity and object relationships, expected policy, observed
effect, and hidden oracle mapping. Replay uses a reset or cloned synthetic range when a
finding depends on mutable state.

Verdicts are `validated`, `rejected`, or `inconclusive`. Missing proof is rejected;
unavailable replay infrastructure is inconclusive. Keep each check and its reason code in
`ValidationResult`. Oracle data and validator credentials are inaccessible to the solver.

Build the deterministic validator and scripted solver before LLM experiments. Later
validation ablations may compare solver self-judgment, fresh-model review, deterministic
checks, and hybrid review over the *same stored candidates*.
