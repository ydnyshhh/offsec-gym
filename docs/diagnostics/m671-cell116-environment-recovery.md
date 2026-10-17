# M6.7.1 cell 116 environment failure and bounded continuation

The one authorized cells 114–128 launch was consumed. Its journal records
cells 114–115 complete and cell 116 started without a `cell_completed` row.
The authoritative PostgreSQL trace for cell 116 contains only `RunStarted`
and `RunCompleted(environment_failed)`; zero provider model calls began. The
sanitized failure receipt identifies `DockerCommandError`. The exact Docker
command and error output were not retained, so this recovery does not claim a
more specific cause or retry cell 116.

Read-only reconciliation requires 113 completed cells, 230 source runs,
188,594 events, 57 pair receipts, zero active action reservations, and only
the previously retained cell-109 model reservation. The conservative cost,
charging a full-cell ceiling for cell 116, is $57.869619 against the original
$230.40 stop. The stopped journal, private log, complete 62-file evidence
set, sanitized failure receipt, and two-event source trace have pinned hashes
in `m671_cell116_environment_stop.py`. These records remain unchanged.

The separate operational wrapper admits only frozen orders 117–128 once in
their existing pair order. It copies pair receipts 1–57 and closes pair 58
from retained cell-115 and cell-116 evidence, without a provider or source
call. The final all-assigned analysis records cells 109, 113, and 116 as
explicit missingness, keeps cell 114 as unscored `provider_failed`, and keeps
cell 115's valid `budget_exhausted` score. Cells 85–86 remain provider
failures. It does not settle the cell-109 reservation.

The wrapper checks the pinned Docker context, daemon, Compose plugin, and
dedicated PostgreSQL container before evidence creation and immediately
before every `cell_started` row. This catches an unavailable Docker service
before a cell is consumed. It cannot prevent every later setup failure; any
unexpected terminal status is journaled only as a sanitized failure class
and stops the launch. A new paid launch requires a separate create-once
mode-0600 approval bound to the exact user-authorized cells 117–128, this
stopped evidence, the reviewed operational merge, selected endpoint and
price, and the original cumulative cap. Operational PR #65 passed exact-head
CI and merged as `19610f733dcd32dc83c2ad1dc938ea0659a22626` with both
reviewed commits preserved. The separate private create-once approval passed
the exact-scope parser; only its SHA-256 is pinned in the wrapper. The paid
launch still requires this approval-pin change to pass exact-head CI and merge,
then all read-only prelaunch gates to pass.

This is descriptive excluded feasibility work. Frozen source, protocol,
manifest, model, checkpoints, seeds, assignment order, prior journals, and
prior studies stay unchanged. No failed or interrupted cell is retried or
replaced.
