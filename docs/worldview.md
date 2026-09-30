# Structured worldview

Milestone 5 implements a typed, event-backed WorldState. `WorldFact` v3 distinguishes
observations, hypotheses, relationships, candidate findings, and open questions. Every
fact carries a stable ID, typed subject and value, source event/action/evidence IDs,
confidence, timestamp, state, and contradiction or supersession references. The states
are hypothesized, observed, corroborated, validated, contradicted, and superseded.

Submissions enter as claims. The controller checks that cited events and completed
gateway actions belong to the same run and that evidence IDs match those actions.
Model-authored hypotheses remain hypothesized even when they cite evidence. A grounded
observation may become observed, and independent matching gateway evidence may make
observations corroborated. Conflicting observations remain visible and are marked
contradicted. These states describe provenance and agreement; only an independent
validation process may establish a validated security claim.

Coverage has a separate typed claim lifecycle: active, completed, or released. A single
controller refuses an active duplicate for the same component and objective. The event
stream remains authoritative; the current implementation rebuilds its projection from
run events. Cross-controller atomic reservations and indexed projections belong with
the worker coordination milestone.

`WorldContextBuilder` selects facts using a task query, includes related facts for a
selected subject, and enforces fact-count and character limits. It records selected
fact IDs in `ContextRetrieved`. In the `memory=structured` monolithic condition, each
model request includes this selected context and the immediately preceding context,
model output, and tool results. The model can also query the worldview and submit observations
or hypotheses through strict tools. See [Milestone 5](milestone-5.md).
