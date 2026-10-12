# M6.6.1 cell-4 cached-build permission repair result

PR #40 merged as `464b946cfd11c416165a8c9ae17a6e228efdcf75`; the
repair-only approval pin PR #41 passed both exact-head CI runs and merged as
`e5266faa2ce94c560515eddce0d687f5a3c5342f`. The private approval receipt
has SHA-256 `e307bb70e175ef8adec708a28d4db76d84b6dc01412d13308729c128ad51bebd`
and authorized **zero paid cells**. Its contents remain outside Git.

Before mutation, the clean merged execution checkout, frozen source and
protocol commits, manifest and approval hashes, exact stopped journals and log,
no-running-collector gate, and read-only PostgreSQL stop audit all passed. The
dedicated store contained six referenced runs with no extra run or active
reservation. Cells 1 and 4 were infrastructure-missing, cells 2 and 3 were
completed and score-valid, and cell 5 had never started. Cumulative estimated
model-token cost was $0.719841 under the original $172.80 cap.

The approved helper ran **once**. It wrote create-only intent receipt SHA-256
`fbc1c2721e030f90ac3954f3ff1ed573fefc8404b96f3eb79628d138caf563f5`
and completion receipt SHA-256
`2e353af79f9e3ccdfd9e04dbfea169a9584635fb62afc230146150afa8e94347`.
It changed only the 16 files in the exact cached patched build from mode `0600`
to `0644`. Independent readback verified every file against its pre-repair
SHA-256, the expected inventory and modes, and build integrity. No file bytes
changed.

Post-repair readback verified the original and continuation journal bytes, the
stopped log, and cell-4's two-event `environment_failed` trace identity. The
dedicated PostgreSQL store still had exactly six referenced runs and 3,124
events; completed cells 2 and 3 replayed, and there was no active action or
model reservation. No collector was started, no cell was retried or replaced,
and no model call was made for this repair. The study remains stopped with cell
5 unstarted.

The permission mismatch is a strong inferred explanation for cell 4's startup
failure, supported by the earlier disposable no-model range test. The direct
Docker error was not retained, so the repair does not retroactively change the
assigned outcomes. Any continuation of frozen cells 5–96 requires a separate
paid-call decision, approval, versioned operational code, and postcheck.
