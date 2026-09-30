# Milestone 5: structured worldview

The Milestone 5 condition uses the same SaaS range, provider, action gateway, finding
sink, validator, and evaluator as the monolithic transcript baseline. It changes the
agent's memory representation. The controller stores typed observations and hypotheses
as `WorldFact` claims in the authoritative event stream, adjudicates their provenance,
and builds a bounded task-relevant context for each model call. Model request artifacts
show the exact selected context. The immediately preceding selected context, model
output, and tool results are carried into the next call for stateless tool continuation;
earlier turns are not copied into the next request.

## Fact and coverage contracts

`WorldFact` v3 distinguishes observations, hypotheses, relationships, findings, and
open questions. It retains typed subject/value, confidence, source worker/event/action/
evidence IDs, timestamps, supersession, and contradiction links. A worker submits a
claim as `hypothesized`; it cannot assign a trusted status. The WorldState checks that
referenced events and action/evidence pairs exist in the same run before appending
`WorldFactSubmitted`. `WorldFactAdjudicated` records the controller's state decision.
Evidence-backed claims may become `observed`; independent matching evidence can
corroborate them. Conflicting values under one subject/predicate are retained with
contradiction links. Neither confidence nor agreement alone grants `validated` status.

Coverage claims are separate typed records with an owner task, component, objective,
and lifecycle. Their events prevent an active duplicate from masquerading as new work.
This is an initial single-controller projection; atomic multi-controller claims and
leases belong with Milestone 6 worker coordination.

## Retrieval and safety

`WorldContextBuilder` ranks facts by task/query terms, includes related facts for a
selected subject, and caps fact count and rendered bytes. It records the selected fact
IDs in `ContextRetrieved`. The model can also use `query_worldview`,
`submit_observation`, and `submit_hypothesis`. The controller binds submitted evidence
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

The structured condition adds worldview tools to the transcript condition's tool set.
A result from the two supplied experiment configs therefore measures the combined
effect of structured retrieval, fact submission, and the changed tool affordances. It
is not an isolated estimate of memory representation alone. A later controlled
comparison should hold tool affordances constant across both arms.
