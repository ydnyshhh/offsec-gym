# Milestone 6.1: deterministic sequential workers

M6.1 changes trajectory structure without adding a coordinator model or parallel
range calls. The coordinator runs six fixed objectives in order: identity and
workspace mapping, document authorization, invoice authorization, ticket
authorization, public invoice exposure, and refund transition authorization.
Each worker starts a fresh structured model context. All workers use the shared
event-backed worldview, gateway, finding sink, provider, and validator.

## Contracts and accounting

`WorkerTaskPacket` contains the objective, task and worker IDs, a per-worker
model-call/action/request slice, up to eight relevant entities, twelve exact
entity/action/evidence associations, 24 prior checked actions (including up to
twelve pinned `/api/me` requests), bounded coverage state, and hypotheses. The
packet is capped at 10,000 serialized characters. `WorkerPacketPrepared`
records it before `WorkerStarted`, allowing the handoff to be reconstructed
from the event stream. No previous worker's raw model transcript is carried.
Request paths come from restricted range artifacts and are checked against
their request event and run instance.

The six workers divide the global model-call, action, and HTTP request counts
deterministically; earlier workers receive the remainder. The per-call output
limit is unchanged. Token and cost limits stay global and are reserved by the
M6.0 PostgreSQL controller before every model call. This avoids artificial
20,000-token local partitions whose output reservation could stop a worker
despite remaining global compute. The packet gives each worker bounded local
counts, and the prompt states the global token limit.

Every worker uses the M5 structured model tool protocol. Model calls, actions,
controller-observed facts, model-authored facts, and v3 finding submission
events retain worker and task attribution. On exit, the coordinator releases
unfinished coverage leases and constructs `WorkerDebriefed` from actual fact,
finding, and coverage events. The debrief contains bounded IDs, open questions,
and followups. `WorkerFinished` closes the slot. Event replay checks packet
and debrief lifecycle ordering.

## Matched comparison

The historical M5.4 executable at `0f27533` and its traces remain frozen
diagnostic evidence. The causal control reruns the M5.4 monolithic structured
policy **on the same current controller code** as M6.1. The two configs are
[`kimi-k3-structured-m61-control.yaml`](../experiments/configs/kimi-k3-structured-m61-control.yaml)
and [`kimi-k3-structured-m61-workers.yaml`](../experiments/configs/kimi-k3-structured-m61-workers.yaml).
They match the model, high reasoning, upstream provider, range seed, visibility,
validator, action/HTTP/model/token/output/wall budgets, and token-reservation
calibration. The worker arm changes `orchestrator`.

The CLI report derives exact repeat dispatches, cross-worker and within-worker
repeat dispatches, cross-worker evidence reuse in findings, packet size,
debrief count, coordinator model calls, and time from run start to the first
submitted finding that later validates. Duplication uses successful HTTP
dispatches as its denominator. A repeat may be deliberate confirmation;
semantic duplication, abandoned branches, and recovery after false hypotheses
still need manual trace annotation. Cost is reported only when the model spec
contains prices; otherwise token counts are the compute measure.

## Verification and remaining work

PostgreSQL fake-provider tests exercised six sequential workers, bounded
packets, attributable model calls, debriefs, and event replay. A fake-provider
trace through the real Docker range showed the public-exposure worker using
an invoice entity and evidence discovered by the identity worker, submitting
an attributed finding, and receiving one validated result. The coordinator
made zero model calls. These tests validate plumbing, not a score advantage.

The next diagnostic is three current-code monolithic controls and three
sequential-worker runs with the paired configs, then manual trace inspection.
M6.2 must resolve response-fact ordering by source action completion sequence,
cross-process range guards, and stale reservation recovery before parallel
workers are enabled.
