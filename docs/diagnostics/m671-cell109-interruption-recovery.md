# M6.7.1 cell 109 interruption and bounded recovery

The separately approved sole collector for frozen cells 87–128 stopped after
starting cell 109. Its journal records completions for cells 87–108 and a
start for 109, with no completion for 109. No collector remains running. The
authoritative source trace for 109 has 1,296 events, six completed model calls,
and a seventh `model_call_started` with no matching completion or terminal
`run_completed`. The provider outcome of that seventh call is unknown. One
model reservation remains active; it must not be settled or erased to make the
study appear complete.

The read-only PostgreSQL stop audit found 108 completed cells, 218 exact
source/setup runs, 181,603 events, 54 complete pair receipts, zero active
action reservations, and the one known active model reservation. Completed
continuation cells recorded $9.527106 estimated model-token cost. Cell 109
has $0.234096 in already completed model-call estimates and a $0.159873
unsettled reservation; neither resolves the unknown provider outcome. The
conservative study total charges cell 109 its full $1.80 ceiling and is
$50.729976, below the original $230.40 cumulative stop.

The continuation journal SHA-256 is
`eb70456653062f0661325d54df3276b961c6ead87724ec0d734a9e2eb52c8789`;
its private log SHA-256 is
`f5d791f32f8d4f7a16e852283b4bb572397e34b9cffc899e8b9a01e8f626c084`.
The 68-file continuation path/content digest is
`93de9939bb80b6997540a9ce18febce86ca13b0a804e1c45d6e7822cf54bea5f`.
Original stopped and failed first-launch evidence remain pinned and unchanged.

The human has authorized **exactly one new launch for frozen cells 110–128,
once each**, with no retry or replacement of cell 109. Cells 85–86 retain
their provider failures. The operational wrapper initially had zero approval
and merge pins. PR #61 passed exact-head CI and merged as
`84a173dd5d9abcec66ba4092cea8b648cb432ede`. The separate create-once,
mode-0600 private approval for the exact human scope passed its parser; its
SHA-256 is
`9753fad9f52c7768e9aa9fd8cf9e3a78fef97fbf68c93ae0273084e24a6533c2`.
It binds the interrupted trace and active reservation, original model/price,
and $230.40 cap. No new paid call may occur before this pin passes exact-head
CI, merges, and a clean execution checkout passes full read-only admission.

The frozen generic pre-call guard rejects every active reservation. This
recovery admits only the pinned cell-109 reservation, charges its full cell
ceiling, checks the exact run/call and database inventory before and after
every new cell, and passes only the count of *other* active reservations to
that guard. Any additional or changed reservation stops collection. No
reservation is settled by the recovery code.

The new create-once journal starts at order 110. Pair 55 records cell 109 as
explicit missingness and cell 110's single new source run; the remaining
pairs run in frozen order. Final replay reads every completed source trace
and all 64 pair receipts, checks the sole retained reservation, and invokes
the unchanged frozen factorial analysis twice for byte-identical results.
Cell 109 remains an assigned, interrupted, score-invalid row. These results
are descriptive excluded feasibility evidence, not confirmatory evidence.
