# M6.6.1 cell-92 interrupted-action recovery

This operational amendment is **closed to paid calls** until its exact approval
receipt and merge commit are pinned in a separate reviewed change. It preserves
the frozen source, protocol, manifest, model, checkpoints, seeds, assignment
order, analysis, and $172.80 cumulative estimated model-token cap.

The sole cells-87–96 collector was stopped after cell 91 returned
`provider_failed` / `provider_unavailable`, with an invalid score and
unauditable resources. Cell 92 had already started. Its authoritative run ends
after 61 events with one active task-owned action reservation, no model call,
and no terminal event. Its action outcome is unknown. Cells 93–96 never
started. Cells 1 and 4 remain infrastructure missing; cells 13 and 14 remain
provider failures; cell 86 remains outcome missing with its original active
model reservation. Pair receipts 1–45 and endpoint preflight 46 are retained.

The read-only exact-stop audit pins the stopped journal
(`c205ec34ffdc442005cbfea0968fb2c13acbbcfdc57e2fd95f7673d1734e612f`),
log (`b37a654129e035aa4ceccf53ab4055ae11a7b933e088dd2678885539e3c12282`),
cell-92 trace and event identities, all prior journals and receipts, 181
PostgreSQL runs, 144,702 events, and exactly the two retained reservations.
It replays all 88 completed source cells against their frozen stage and score
artifacts. Recorded estimated cost, including cell 86's recorded use, is
$37.281219. Conservative admission cost is $45.996150 after charging the
full $1.80 per-cell ceiling to both missing cells 86 and 92 and every
resource-unauditable completed cell. Neither reservation is settled.

The user approved **only frozen cells 93–96 once** under the original cap,
with no retries, replacements, or reservation settlements. After the separate
receipt and merge pins are reviewed, the proposed wrapper will:

1. Reconcile the exact stopped journal, PostgreSQL inventory, both active
   reservations, cached build, and frozen checkouts before any new work.
2. Close pair 46 solely from cell 91's retained provider-failure events and
   cell 92's exact interrupted prefix. It will not synthesize a terminal event
   or score for cell 92.
3. Run a full read-only pre-call postcheck, recheck the selected public
   endpoint and frozen $3/M input and $15/M output prices, and open a separate
   journal for cells 93–96.
4. Execute those four frozen assignments once in order, with a new provider
   health epoch at cell 93. Any unauditable result or provider failure stops
   further paid work before the next cell.
5. Require 48 pair receipts and a final authoritative PostgreSQL postcheck
   and byte-for-byte frozen analysis if all assignments finish.

A temporary-copy rehearsal closed pair 46, passed the pre-call postcheck, and
was forced to stop before constructing cell 93's spec. PostgreSQL remained at
181 runs and 144,702 events. The original private evidence was unchanged.

The reporting row for cell 92 will use the frozen analysis function's
`validation_failed` missing-row category with reason
`collector_interrupted_during_action`. That is a reporting placeholder, not a
claim that its source run reached a validation-failure terminal state. The
unknown action outcome and both active reservations remain limitations of
this excluded feasibility diagnostic.
