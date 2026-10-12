# M6.6.1 cell-4 infrastructure stop and proposed mode repair

The excluded feasibility study is stopped. This document records an operational
failure; it does not change the frozen source, checkpoints, assignments,
analysis plan, model, or budget.

## Authoritative stopped prefix

- The original cell-1 journal and log remain byte-for-byte unchanged. Cell 1
  remains assigned outcome-missing after `environment_failed`, with no model
  request or token cost.
- The separate continuation journal has completed cells 2 and 3, followed by
  `cell_started` for cell 4 and no cell-4 completion. Its SHA-256 is
  `abcf535e29ffa26b24feb6c5eed02534f60253eada9fa4676992853d3a191077`.
  The stopped continuation log SHA-256 is
  `cf2a2575dcf06b4776b3082e0c0396e16b990f74031030bb1c93b89c01d92b87`.
- Cell 2 is score-valid `budget_exhausted`, estimated token cost $0.448449.
  Cell 3 is score-valid `agent_failed`, estimated cost $0.271392. Pair 1 has
  its audited receipt. Total estimated cost is **$0.719841** against the
  unchanged $172.80 cumulative stop.
- Cell 4 is the assigned patched B3-S0 cell. PostgreSQL run
  `52f176a6-d8d3-449f-b14f-2204c4b104f6` has exactly two contiguous events:
  `RunStarted` and `RunCompleted(environment_failed)`. Event identity SHA-256
  is `c69e40e8aca18c5fe1dd7f56825d7f70bd61aa114f62aaba40a21d81e00a6b80`.
  It has zero model starts, completions, and token cost. No checkpoint setup
  receipt, stage, or pair-2 receipt exists. Cell 5 never started.
- The dedicated PostgreSQL store contains exactly six runs: cell 1, cells 2
  and 3 with their two zero-model setup runs, and cell 4. No extra run or
  active action/model reservation exists. `m661_cell4_stop.py` replays the
  completed sources and checks these identities read-only.

## Failure mechanism and bounded repair

Cell 4 reused the cached build created for cell 1. All 16 files in that build,
including `init_db.py`, remain mode `0600` from the first collector's `077`
umask. The fresh build used by cells 2 and 3 has mode `0644` files. The cell-4
source run ended before `RangeStarted` or checkpoint setup. The missing
checkpoint receipt then caused the operational collector's `_record` call to
raise `FileNotFoundError`; that exception is not a new experimental outcome.

In a **disposable copy** of the cached build and oracle, a no-model test changed
the 16 file modes from `0600` to `0644`, verified every file's bytes and build
integrity before and after, started the range to `healthy`, and destroyed it.
No original build file was modified. This supports a mode-only repair as an
operational prerequisite. The direct Docker error for cell 4 was not retained,
so the causal attribution remains an inference from the exact cached build,
its modes, the two-event source trace, and the disposable test.

`m661_cached_build_permissions.py` is deliberately closed with an all-zero new
approval hash. If separately approved and pinned, it would write create-only
intent and completion receipts, change only the 16 cached build file modes,
and verify byte-for-byte integrity. A partial repair must stop for separate
reconciliation. It must never retry cells 1 or 4.

The proposed future paid scope is only frozen cells 5–96 once, with all earlier
outcomes retained and the original $172.80 **cumulative** cap. A separate
approved continuation and postcheck must account for the missing cell 4 and
the partial pair 2 before any paid call. This proposal alone does not approve
that continuation or permit a write to the original study state.
