# M6.7.1 cell 113 environment failure and bounded continuation

The one authorized cells 110–128 launch was consumed. Its journal records
cells 110–112 complete and cell 113 started without a `cell_completed` row.
The authoritative PostgreSQL source trace for cell 113 contains only
`RunStarted` and `RunCompleted(environment_failed)`. No model call began for
that cell. The frozen runner catches setup exceptions and emits only the
terminal status in its trace; the exception class was not journaled by the
operational wrapper. The exact Docker setup failure cannot be recovered from
the preserved records, so this recovery does not attribute a more specific
cause or retry cell 113.

Read-only reconciliation before any new work requires 111 completed cells,
225 source runs, 186,537 events, 56 pair receipts, and only the previously
retained cell-109 model reservation. The conservative estimated cost,
including full-cell ceilings for cells 109 and 113, is $53.883117 against
the original $230.40 stop. The stopped journal, private log, 61-file evidence
set, cell-113 two-event trace, and destroyed instance manifest have pinned
hashes in `m671_cell113_environment_stop.py`.

The separate operational wrapper admits only frozen orders 114–128 once in
their existing pair order. It copies pair receipts 1–56, records cell 113 as
unscored `environment_failed` in the final all-assigned analysis, and leaves
cell 109 interrupted with its reservation untouched. Cells 85–86 remain
provider failures. PR #63 passed exact-head CI and merged as
`33f8ad8d1e7bdaac2a2178ee130ab68c7c46645f`. The separate create-once,
mode-0600 private approval for the exact human-approved cells 114–128 passed
its scope parser; its SHA-256 is
`7544d7bb1a553158f467f7c065e11f0816d8860bd91a6b5b200f3262cd03ea3e`.
It binds the stopped evidence, retained reservation, selected model and price,
and original $230.40 cap. Paid work requires this pin to pass exact-head CI,
merge, and a clean execution checkout to pass full read-only admission. A
future unexpected terminal status writes a small sanitized failure-class
receipt and stops the collector.

This is descriptive excluded feasibility work. The frozen source, protocol,
manifest, model, checkpoints, seeds, assignment order, prior journals, and
prior studies remain unchanged. No failed or interrupted cell is retried or
replaced.
