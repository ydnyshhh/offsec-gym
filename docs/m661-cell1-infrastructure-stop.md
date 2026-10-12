# M6.6.1 cell-1 infrastructure stop and proposed continuation

The excluded feasibility collector stopped after writing `batch_started` and
`cell_started` for assigned cell 1. The journal has SHA-256
`3c763b636487e39b25fd921570e0ec9d3ab45d18eae10fdd7e14d2d53fb026ae`.
Cell 2 has not started. Dedicated PostgreSQL contains exactly one source run,
`b23fffa6-62bc-4aec-a6a3-f1492d4d4445`, with contiguous `RunStarted` and
`RunCompleted(environment_failed)` events. Its event-identity SHA-256 is
`aabfb7617981f672f2d7e21e1b663660cae457c685d691f8ecfdd6c91180a206`.
There is no model call, token charge, or active controller reservation. No
checkpoint receipt, checkpoint stage, or pair-1 receipt was produced.

The launcher inherited file mask `077`. Generated range scripts therefore had
mode `0600`; the image's non-root init process could not open `/app/init_db.py`.
A separate, temporary, **no-model** range compiled under file mask `022`
produced mode `0644` scripts, reached healthy state, and was destroyed. It used
the frozen range specification in a separate temporary state, without any
model call or study database event. The original
cell-1 build, destroyed instance, journal, and log remain unchanged.

The proposed operational recovery keeps cell 1 assigned and explicitly missing
from the checkpoint outcome because the environment failed before checkpoint
binding. Its zero model/HTTP usage is recovered independently from the two
authoritative events. It does not count as a model failure or as a successful
transition. Missingness bounds in the frozen analysis retain the assignment.

The recovery code admits only the exact stopped journal, run, trace, original
endpoint receipt, original build, destroyed instance, and zero model work. It
keeps the original journal byte-for-byte and writes a separate continuation
journal. It can execute only frozen cells 2–96, each once, with no replacement
or retry of cell 1. The original `$172.80` cumulative estimated model-token cap,
provider-health policy, endpoint pin, source, protocol, manifest, model, prompts,
checkpoint interventions, budgets, seeds, and order are unchanged. It sets file
mask `022` before compiling new range artifacts while private evidence files
remain explicitly `0600`.

The paid continuation is **closed**: its expected approval SHA-256 is all zeros.
This proposal cannot launch a model call until a separate human decision
authorizes precisely cells 2–96 once, with cell 1 retained as an infrastructure
failure and the original cumulative cap. A create-only private receipt must then
be bound by a separate provenance commit and pass exact-head CI. A subsequent
interruption leaves remaining assignments missing; no balance is filled in.

This incident limits the excluded study: the first checkpoint assignment will
be unauditable for outcome, and all-assigned rates must show that missingness.
The no-model permission qualification validates the environment setup fix; it
does not establish that any later model cell will complete.
