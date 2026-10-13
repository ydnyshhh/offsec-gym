# M6.6.1 cell-14 provider-pause proposal

This operational amendment is **closed to paid calls**. Its required approval
SHA and operational merge commit are zero sentinels. It does not change the
frozen source commit, protocol commit, manifest, model, checkpoints, seed-paired
assignment, execution order, budgets, analysis, or $172.80 cumulative cap.

The read-only admission binds the exact stop after cell 14 and replays the
authoritative PostgreSQL data before the deliberately absent pair-7 receipt.
The stopped cell-4 paid journal is immutable. Cells 13 and 14 remain assigned
`provider_failed/provider_unavailable` outcomes; cells 1 and 4 remain
infrastructure missing. Cell 15 has no run. The prior $4.202310 estimated cost
and $7.646460 conservative cost carry forward.

After a **separate exact-scope approval**, the proposed wrapper would:

1. Reaudit cells 13 and 14 and create pair 7 only from their existing events.
2. Run the frozen read-only postcheck, including the retrospective pair receipt.
3. Recheck the selected public endpoint and frozen $3/M input, $15/M output
   prices without a model call.
4. Create a new health epoch at cell 15 and a separate continuation journal.
5. Replay the full PostgreSQL prefix again before the first paid call.
6. Execute only frozen cells 15–96 once, in assignment order, with the original
   cap, no replacement, and the unchanged per-cell collector and audit gates.

The extended postcheck reads both continuation journals, verifies the exact
cell-14 boundary, allows the frozen health pause only at that boundary, and
applies the same frozen provider-health rule within the new epoch. It still
requires 48 pair receipts and all assigned rows on successful completion.

A temporary-copy rehearsal closed pair 7 and passed both pre-call PostgreSQL
postchecks. It was forced to stop at cell-15 spec construction, before model
work. The original journals, receipts, and database remained unchanged at 26
runs, 18,626 events, and zero active reservations.

This is an excluded feasibility diagnostic. The two provider failures remain
missing primary outcomes and their resource use is conservatively reserved.
Any later response curve must describe that missingness and the operational
pause. No result-based resizing or filling of missing assignments is allowed.
