# M6.6 excluded pilot v2: infrastructure correction

The eight-cell v1 pilot remains frozen and excluded. Its Range B vulnerable
pair stopped before model work because the 18-user, three-organization
fixture requires 33 bootstrap GETs and the v1 cap was 32. Its pinned offline
stage extractor also passed a completed trace into a source-prefix verifier.
The [v1 failure record](m66-pilot-v1-bootstrap-failure.md) retains the
authoritative runs and unstarted final pair.

This v2 protocol is a **new excluded feasibility pilot**. It keeps the
`source_commit` model, witness, range, pair runner, validator, endpoint policy,
model request, 120,000-token/20-call global budget per run, model output cap,
gateway budget, visibility, and four-pair vulnerable/patched design. It makes
three predeclared infrastructure changes:

1. Range B bootstrap receives 40 actions and HTTP requests, above the 33
   required by the pinned fixture topology. Range A remains at 32. Before
   any paid model call, a no-model qualification must verify both selected
   Range B builds require at most 40 bootstrap GETs and finish bootstrap.
2. Both configs explicitly serialize the selected endpoint's $3/$15
   per-million-token input/output rates into `ModelSpec`, so event-level cost
   accounting is no longer silently zero. The collector still checks the
   live selected endpoint and price before each pair and enforces a $15
   cumulative estimated-cost ceiling.
3. The versioned offline extractor supplies only source-eligible events to
   the reporter evidence-bundle verifier. It must complete on a finished,
   already collected v1 trace before v2 collection.

A read-only local check of this extractor change completed on all four
score-valid v1 Range A traces. It used a temporary in-memory protocol-commit
binding for the amended code; it did not change the frozen v1 manifest,
event store, traces, or outcomes. This check verifies the failure path is
closed, while the final v2 manifest still requires its own exact-commit audit.
The selected Range B seed 551180 also completed two local fake-provider pairs,
one vulnerable and one patched. Both control and witness arms in both pairs
completed 33 bootstrap actions and produced score-valid terminal runs. These
were no-cost qualification runs, outside the future eight-cell live pilot.

New pilot seeds are derived from `m66-pilot-range-a-v2|0` and
`m66-pilot-range-b-v2|0` with the same SHA-256 map as v1: **712868** and
**551180**. The immutable v2 exclusion registry contains all v1 exclusions
plus v1 pilot seeds 124501 and 704929. Selection occurred before building or
inspecting either new fixture. Both v2 seeds remain excluded from any later
confirmatory M6.6 sample.

`m66_pilot_protocol_v2.plan_pilot` must run from the exact merged
`protocol_commit` checkout. It pins the unchanged full `source_commit`, the
new protocol commit and transitive analysis imports, every config and source
file hash, the seed registry, compiler and Range B qualification hashes,
tool schema, eight ordered cell identities, selected endpoint, price-check
time, and $15 stop. Its output is created once, hashed, and never overwritten.
Approval for paid calls must be a **separate artifact bound to that final
manifest SHA-256**. Until then, `paid_model_calls_authorized` remains false
and no v2 model call may run. No v1 cell is retried or counted as a v2 cell.

V2 gates remain infrastructure and feasibility only: eight journaled cells,
four matched pairs, valid bootstrap and scores, selected endpoint and price,
trace/event replay, stage extraction, complete no-retry accounting, and
cost under the approved ceiling. Policy effect magnitude is not a pilot
gate. Any failed cell is retained without outcome-based replacement.
