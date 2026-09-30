# Structured worldview

Milestone 5 implements a typed, event-backed WorldState. `WorldFact` v4 distinguishes
observations, hypotheses, relationships, candidate findings, and open questions. Every
fact carries a stable ID, typed subject and value, source event/action/evidence IDs,
confidence, timestamp, state, and contradiction or supersession references. The states
are hypothesized, evidence_linked, multi_evidence_linked, validated, contradicted, and
superseded. Legacy v3 `observed` and `corroborated` events remain readable, but those
labels only proved evidence linkage and agreement; they never established truth.

Submissions enter as claims. The controller checks that cited events and completed
gateway actions belong to the same run and that evidence IDs match those actions.
Model-authored hypotheses remain hypothesized even when they cite evidence. An observation
with a genuine action/evidence pair becomes `evidence_linked`. A matching claim with a
different evidence ID can become `multi_evidence_linked`. Neither transition checks
whether the response entails the assertion. Conflicting evidence-linked claims remain
visible, marked `contradicted`, with links in both directions; this means unresolved
incompatibility, not proof of which claim is false. Only an independent validation process
may establish `validated`. A model-submitted claim cannot supersede another fact; a
controller-only disposition requires an independently validated replacement.

Coverage has a separate typed claim lifecycle: active, completed, or released. A single
controller refuses an active duplicate for the same component and objective. The event
stream remains authoritative; the current implementation rebuilds its projection from
run events. Cross-controller atomic reservations and indexed projections belong with
the worker coordination milestone.

`WorldContextBuilder` selects facts using a task query, includes related facts for a
selected subject, and enforces fact-count and character limits. Its retrieval projection
groups repeated `(subject, predicate, value)` claims, retaining supporting evidence IDs,
observation count, and first/last seen times. Provenance state outranks model confidence
when relevance is equal. It records selected representative fact IDs in
`ContextRetrieved`. The automatic query combines the global goal with active coverage,
the recent action path, and the recent hypothesis. In the `memory=structured`
monolithic condition, each model request includes this selected context and the
immediately preceding context, model output, and tool results. The model can also
query the worldview and submit observations or hypotheses through strict tools.
See [Milestone 5](milestone-5.md).
