# M6.6 pilot operational amendment 1: post-run coverage closure

The approved M6.6 excluded pilot stopped after the first matched pair ran.
Its private journal SHA-256 at the stop is
`6e8e0c909482301b20368801593080af4b7e5d0a16f72f9e5ad4dd495ee77261`.
Cell 1 (`8c0c378b543a8b70`) is journaled and score valid. Cell 2
(`55e8cfa8bb10ab32`) completed as run
`e11a07a5-4557-4b69-ab79-f807901d7a35` but was left as a started
journal entry because the collector rejected one active monolithic coverage
lease. The two arms used the same build. Cell 2's original PostgreSQL stream
has 400 contiguous events and one `RunCompleted(budget_exhausted)`. No
collector remains active.

This is terminal controller bookkeeping, previously encountered in M6.5:
a monolithic coverage claim can remain active when its run ends. The amended
collector now releases active monolithic coverage **after**
`RunCompleted` and before its outstanding-reservation audit. The release
appends `CoverageUpdated` and `CoverageLeaseReleased` events through the
existing `EventWorldState` API. It refuses to mask an active worker, action,
model reservation, or admission hold. It makes no model request, gateway
request, range transition, validator call, score change, or policy change.

`research_ops/m66_reconcile_cell2.py` is a one-cell, create-only recovery
script. It requires the exact stopped journal bytes and pinned run identities,
copies the preclosure trace, verifies the existing build, terminal event,
event sequence and single coverage lease, appends the two bookkeeping events,
replays the oracle score, records the completed second cell from the
authoritative stream, and writes a reconciliation receipt. It cannot retry
or replace either arm. The resumed collector will then start at cell 3,
in the original frozen order.

The original manifest SHA-256
`0bda0f40d75c8a683752f20da0cbf09ea8a5e7d1cd39eaaf6c77725bac8aabd8`,
source commit `950bdb746e0d8ae9d68a324f58e5b763c7ddbc1d`,
protocol commit `840992f760eae35e89ae4993f3283894e86b6fbe`,
eight cell identities, model requests, budgets, endpoint, price, approval,
and $15 cumulative estimated-cost stop remain fixed. The amendment and
reconciliation are part of the operational audit record, not an alternate
pilot arm or a new sample.
