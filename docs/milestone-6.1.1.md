# Milestone 6.1.1: fair sequential worker handoffs

The [M6.1 six-run diagnostic](diagnostics/kimi-k3-openrouter-m61.md) is frozen
at `95eaf85`. This revision keeps the same six objectives, order, model,
gateway, shared worldview, and deterministic coordinator. It changes the
worker budget and context policy, packet selection, and coverage ownership.
It is a mechanistic follow-up, not a score-tuning exercise or M6.2 parallelism.

## Resource policy

Before worker `i`, the coordinator reads persisted global usage. For each
future worker it protects one model call, one action, one HTTP dispatch, and a
prospective model reservation of **15,500 input + 4,000 output tokens**. The
worker output cap is 4,000; the global 8,192 cap and 120,000 total-token limit
remain unchanged. In the earlier six-run diagnostic, first-call input
reservations peaked at 15,010 and actual output per call peaked at 1,810.
The new values are empirical headroom, not a model tokenizer guarantee.

The current worker may use the global remainder after these future floors.
`WorkerTaskPacket` records the protected amounts and the resulting spendable
budget. PostgreSQL checks model-call, token, and configured cost floors in the
same transaction as each model reservation. The sequential tool wrapper checks
action/HTTP floors against the persisted counters immediately before calling
the gateway; the gateway still makes the atomic global reservation. Action
floors are safe under the current single-worker execution, but M6.2 must move
their protection into the controller decision before parallel workers run.
Provider usage beyond its preflight reservation could still cross a floor;
the empirical calibration and signed reservation errors make that auditable.

## Handoff and coverage

The recorded packet is the worker's initial shared-state snapshot. Automatic
context on later model turns contains at most 2,600 characters of worker-local
checked actions, evidence grouped under entities/actions, and model-authored
claims created since the packet. Older shared facts remain available through
`get_entity` and `query_worldview`. This removes the full 7,500-character
automatic worldview that was previously stacked on top of the packet.

Packet selection is deterministic and objective aware. It reserves places for
secondary entity types (including workspace and identity), then ranks within
types by relationship to target objects, evidence, role/membership details,
and recency. Evidence is selected first across retained entities, then by
recency; checked actions favor the worker's route family. This is a bounded
projection, not an LLM retriever. Checked POST requests carry the source
event's exact body SHA-256 and a bounded canonical JSON preview from the
redacted request artifact; large bodies carry key/size summaries.

The coordinator acquires the objective's coverage lease before preparing the
packet. The worker cannot call coverage bookkeeping tools. Before its debrief,
the coordinator completes the lease if the worker dispatched an HTTP action
on that objective's route family, and releases it otherwise. `completed`
therefore means **objective explored**, not vulnerability proven or exhaustive
coverage. Leases and debriefs remain reconstructable from the event stream.

## Predeclared three-run smoke

Run only three vulnerable worker repetitions with the existing
[`kimi-k3-structured-m61-workers.yaml`](../experiments/configs/kimi-k3-structured-m61-workers.yaml)
spec. The historical M6.1 worker traces are the diagnostic reference; this
is not a new monolithic comparison or a statistical estimate. Inspect the
request/response artifacts and event traces manually for:

| Mechanistic gate | Threshold |
| --- | --- |
| Worker opportunity | All six workers get at least one model call in each run. |
| Action opportunity | Each worker with a nontrivial objective dispatches at least one relevant HTTP action in each run. Record misses exactly. |
| Future floor | No worker gets zero calls solely because earlier workers spent its protected reservation. |
| Input context | Peak worker input ≤10,900 tokens per run. Record any failure's exact overrun and source; compare with the prior 11,831 peak and monolithic 9,909 peak. |
| Packet relevance | Report omissions and manually check retained objective targets, identity/workspace relationships, and evidence associations. |
| Controller integrity | Zero active reservation/worker/coverage leaks and zero cross-worker attribution errors; replay matches persisted counters. |
| Late objectives | Public and refund workers each dispatch a relevant HTTP action in at least two of three runs. |

Root-cause recall is recorded but has no pass threshold. Even if these gates
pass, a three-run result cannot show that workers outperform the monolithic
policy. M6.2 still requires source-action ordering for concurrent facts,
cross-process range dispatch/reset coordination, and stale-reservation
recovery.

The [three-run smoke](diagnostics/kimi-k3-openrouter-m611.md) is complete.
Its budget, input-size, and attribution gates passed; objective-action,
packet-relationship, and refund-exploration gates failed. The failed gates
remain failed in the recorded version.
