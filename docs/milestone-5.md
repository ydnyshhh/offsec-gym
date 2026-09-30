# Milestone 5: structured worldview

The Milestone 5 structured-worldview system uses the same SaaS range, provider, action
gateway, finding sink, validator, and evaluator as the transcript-baseline system. The
controller stores typed observations and hypotheses as `WorldFact` claims in the
authoritative event stream, adjudicates their provenance,
and builds a bounded task-relevant context for each model call. Model request artifacts
show the exact selected context. The immediately preceding selected context, model
output, and tool results are carried into the next call for stateless tool continuation;
earlier turns are not copied into the next request.

## Fact and coverage contracts

`WorldFact` v4 distinguishes observations, hypotheses, relationships, findings, and
open questions. It retains typed subject/value, confidence, source worker/event/action/
evidence IDs, timestamps, supersession, and contradiction links. A worker submits a
claim as `hypothesized`; it cannot assign a trusted status. The WorldState checks that
referenced events and action/evidence pairs exist in the same run before appending
`WorldFactSubmitted`. `WorldFactAdjudicated` records the controller's state decision.
Claims citing a real gateway action/evidence pair become `evidence_linked`; distinct
matching evidence links become `multi_evidence_linked`. These names deliberately do not
claim that response content entails model-authored text. Legacy v3 `observed` and
`corroborated` events remain readable, but the retriever treats them as evidence-link
classes. Conflicting linked claims keep contradiction links; neither value is selected
as true. A model cannot supersede a fact. A controller disposition requires a separately
validated replacement. Neither confidence nor agreement grants `validated` status.

Coverage claims are separate typed records with an owner task, component, objective,
and lifecycle. Their events prevent an active duplicate from masquerading as new work.
This is an initial single-controller projection; atomic multi-controller claims and
leases belong with Milestone 6 worker coordination.

## Retrieval and safety

`WorldContextBuilder` ranks facts by task/query terms and provenance class before model
confidence, groups repeated `(subject, predicate, value)` observations in the retrieval
projection, includes related facts for a selected subject, and caps group count and
rendered bytes. Each group reports observation count, first/last seen, and bounded
evidence IDs. It records representative fact IDs in `ContextRetrieved`. Automatic
retrieval combines the goal, active coverage, recent action path, and recent hypothesis.
The model can also use `query_worldview`, `submit_observation`, and
`submit_hypothesis`. The controller binds submitted evidence
and model-call provenance; model text cannot promote itself to a validated fact.
Gateway responses remain the authority for action and evidence IDs. Hidden oracle data
never enters WorldState. A bounded HTTP result reaches the model as the immediate tool
result; the event-backed worldview retains selected derived facts rather than copying
raw HTTP response bodies into shared state.

The event stream is authoritative and WorldState is rebuilt from it on each query. This
keeps crash recovery and PostgreSQL persistence simple for one controller. An indexed
projection and transactional claim reservation are needed before concurrent controllers
or large worker cohorts.

## Comparison limit

The supplied configs are explicitly named `transcript_baseline` and
`structured_worldview` agent systems. Structured adds five worldview tools and different
instructions. Their score difference measures the combined system change; it is not an
isolated estimate of memory representation. A future controlled comparison should add a
third arm that retains the transcript while exposing the same worldview tools as the
structured arm. Comparing that arm with structured would better isolate transcript
replacement by bounded retrieval.
