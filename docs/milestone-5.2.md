# Milestone 5.2: response-derived state and exact entities

M5.2 changes the structured worldview's state abstraction. The M5-v1 six-run
diagnostic remains in [its original report](diagnostics/kimi-k3-openrouter-m5.md)
and event store. Its request/status facts had valid provenance but did not retain
roles, workspace ownership, response fields, or exact object IDs. The M5.2
implementation records those fields from verified gateway responses.

## State contract

`EventWorldState.record_response` checks the completed gateway event against the
request, evidence ID, HTTP status, and response SHA-256 before extracting facts.
The route-aware `ResponseFactExtractor` accepts only complete HTTP 200 JSON
objects with the matching body hash and response object ID. It supports `/api/me`,
workspace document/invoice/ticket listings, object details, public invoice
previews, and successful invoice refunds. The synthetic ticket response has no
requester field, so the extractor does not invent one. Workspace listings expose
IDs and workspace IDs, but no invoice status; status comes from invoice detail.

Controller-derived facts use schema v4 and `observed`. Model-authored claims
start `hypothesized`; citing an action may make them `evidence_linked`, which
proves the citation but not the claim's content. A model claim cannot downgrade
a controller-observed response field. Repeated observations of a changed field
supersede the earlier value. The event log retains every action; request and
status telemetry are no longer promoted to worldview facts.

`EntityLedger` projects controller-observed facts into typed objects with exact
UUIDs, source action IDs, and evidence IDs. Automatic context renders both IDs
needed for a finding citation and prioritizes the recent action's
entity and one-hop relations, then role, membership, ownership, and status.
Model hypotheses and coverage remain available. The structured agent's
`submit_observation` tool remains for details outside the route extractor.

## Finding and replay contract

The three finding tools now expose canonical benchmark categories with typed
enums and route/resource consistency checks. They still require real evidence
references. Deterministic validation remains independent of the model.

`offsecgym experiment revalidate RUN_ID --legacy-m5v1` applies a documented,
exact alias table to saved M5-v1 finding categories and validates the resulting
candidate against its original evidence and range context. It does not rewrite
the original finding, event stream, or evaluation. Some transition proofs use
isolated replay and can append events for a new replay instance.

The three saved transcript runs submitted 16 findings: three originally
validated and 13 rejected before proof checks as `property_unmatched`. Exact
alias replay validates nine of those 13. Across all 16, the replay results are
12 validated, two `proof_missing`, and two `property_unmatched`. This is a
contract-repair diagnostic, not a prospective model-quality estimate.

## Pinned diagnostic configs

The M5.2 configs are `experiments/configs/kimi-k3-transcript-m52.yaml` and
`experiments/configs/kimi-k3-structured-m52.yaml`. Both specify the same Kimi K3
model, high reasoning, upstream `moonshotai`, seed, vulnerable range, known
routes, and budgets. OpenRouter receives a provider order with fallback disabled.
An upstream rate limit or outage should be recorded as a provider failure, not
silently routed to another provider.

Run three repetitions per arm with `OPENROUTER_API_KEY` and
`OFFSECGYM_DATABASE_URL` set:

```sh
uv run offsecgym experiment run experiments/configs/kimi-k3-transcript-m52.yaml --repetitions 3
uv run offsecgym experiment run experiments/configs/kimi-k3-structured-m52.yaml --repetitions 3
```

The small run set is for manual trace inspection. The predeclared gates are a
structured exact request-repeat rate below 5%; at least one validated finding
in at least two of three structured runs; zero identifier reconstruction
failures; retrieved exact entity IDs; materially fewer `/api/me` rediscoveries;
controller-observed role, workspace, and object relations; and late structured
input context substantially below the transcript arm. A 10+10 expansion or M6
progression depends on those gates and the trace evidence.

The [pinned Kimi K3 diagnostic](diagnostics/kimi-k3-openrouter-m52.md) records
the run IDs, manual repeat annotation, and gate decision. It cleared the
finding and late-context gates and preserved exact IDs in context, but failed
the under-5% exact-repeat gate; expansion and M6 progression remain on hold.
