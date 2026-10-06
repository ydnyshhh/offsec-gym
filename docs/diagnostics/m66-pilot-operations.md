# M6.6 excluded pilot: approved collection boundary

The eight-cell pilot specification is the frozen
`experiments/manifests/m66-pilot-v1.json`, SHA-256
`0bda0f40d75c8a683752f20da0cbf09ea8a5e7d1cd39eaaf6c77725bac8aabd8`.
The user approved the $15 cumulative estimated model-token ceiling after that
hash was presented. The separate
`experiments/approvals/m66-pilot-v1.json` records the approval; the frozen
manifest retains `paid_model_calls_authorized: false` and has not been edited.

The collector in `research_ops/m66_collect_pilot.py` is operational code. It
loads all model, range, gateway, validator, and pair-runner modules from the
detached `source_commit` checkout
`950bdb746e0d8ae9d68a324f58e5b763c7ddbc1d`. It reads the frozen
configs from the detached `protocol_commit` checkout
`840992f760eae35e89ae4993f3283894e86b6fbe`. It verifies both checkout
heads, pinned source/config hashes, the imported package path, eight cell
identities and order, and the approval/manifest binding before collection.
The offline stage extractor runs later from the exact protocol checkout.

Collection uses an isolated PostgreSQL database and range-state directory. A
private append-only journal holds an exclusive lock. Both cells of each pair
are marked started before the pair runner executes; an interrupted pair cannot
be resumed without event-store reconciliation. The collector reserves the
worst-case $3.60 for each pair before launch, rechecks the selected OpenRouter
endpoint and $3/$15 per-million-token prices before every pair, and stops
subsequent paid requests after any provider failure or selected-endpoint drift.
It records each run's trace hash, build/pair binding, source arm, bootstrap
boundary, provider usage, estimated cost, and score validity. A score-invalid
pair is retained and stops collection without retry or replacement.

This approval covers the frozen eight cells only. Pilot results are excluded
from confirmatory M6.6 analysis, and outcome magnitude is not a feasibility
gate. The private journal, traces, model artifacts, range state, and database
remain local; a post-collection audit and aggregate report can be committed
without publishing private model or fixture artifacts.
