# M6.6.1 cell-86 interrupted-call proposal

This operational amendment is **closed to paid calls**. Its required approval
SHA and operational merge commit are zero sentinels. It does not change the
frozen source, protocol, manifest, model, checkpoints, seeds, assignment order,
analysis, or $172.80 cumulative estimated model-token cap.

The sole approved cell-14 continuation stopped during assigned cell 86. Cells
15–85 have completed records; cell 86 has a `cell_started` record and no
`cell_completed` record. Its source run has 674 retained events, including two
model-call starts and one completion. The second call's provider outcome and
actual bill are unknown. Its task-owned reservation remains active: 20,261
tokens and $0.159087. No source run for cells 87–96 exists. Pair receipts 1–42
exist; pair 43 has only its frozen endpoint preflight.

The read-only stop audit pins the stopped journal (`8d884cbef7d9f111b897461cca5d7233c81ae2eedec457b708af43b018e20335`),
log (`05071c2f82661990be370e474cf0defc2759f9705ccec2615bfc15fa36864556`),
cell-86 source trace, event identities, active reservation, previous evidence,
and all 170 PostgreSQL runs and 136,601 events. It replays all 83 completed
source cells and their score and stage artifacts. Completed-cell estimated cost
through 85 is $35.417421; conservative completed cost is $38.861571. The
database records another $0.026463 used in cell 86. For admission and future
cap accounting, the proposal charges the full $1.80 cell ceiling to cell 86,
bringing conservative cost to $40.661571 before any new cell. The reservation
is **not** settled or removed by this proposal.

After a **separate exact-scope human approval**, the proposed wrapper would:

1. Reconcile the exact stopped evidence and require the same single active
   reservation, without retrying or terminating cell 86.
2. Close pair 43 solely from retained cell-85 events and cell 86's pinned
   interrupted prefix. The receipt labels cell 86 as outcome unknown and score
   invalid; it does not fabricate a terminal event or score.
3. Run the read-only PostgreSQL prefix postcheck, recheck the selected public
   endpoint and frozen $3/M input and $15/M output prices without a model call,
   then create a separate clearance and journal.
4. Replay the full prefix before the first paid call. Execute only frozen
   assigned cells 87–96 once, in order, under the original cap and provider
   health gates. Preserve cells 1 and 4 as infrastructure missing, cells 13
   and 14 as provider failures, and cell 86 as interrupted/outcome missing.
5. Require 48 pair receipts and a full read-only PostgreSQL postcheck on
   completion. The postcheck keeps the cell-86 reservation visible and charges
   its full per-cell ceiling when checking the cap.

The reporting row for cell 86 uses the frozen analysis function's
`validation_failed` missing-row category with the explicit reason
`collector_interrupted_with_provider_outcome_unknown`. This is a reporting
placeholder, **not** a claim that the source run reached a validation-failure
terminal state. The actual provider outcome and bill remain unresolved. Any
descriptive result must keep this limitation and all previously missing or
invalid outcomes visible. This remains an excluded feasibility diagnostic; no
result-based resizing or replacement of assignments is proposed.
