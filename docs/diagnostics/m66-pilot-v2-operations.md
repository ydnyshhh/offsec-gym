# M6.6 v2 execution guard: closed after cell 7

The [frozen v2 pilot](m66-pilot-v2-freeze.md) was executed once and
[closed without retry](m66-pilot-v2-results.md) after a score-invalid
provider transport failure in cell 7. Its manifest
SHA-256 is
`b8dbdc28689e7299c4f5f099ce8e3f788483de7b558c0319043fae7ca43fe528`.
Model execution is pinned to source commit
`950bdb746e0d8ae9d68a324f58e5b763c7ddbc1d`; configs and stage
extraction are pinned to protocol commit
`ea56685ff55d2bb20207edd8429562a793ed904f`.

The user explicitly approved this exact eight-cell excluded feasibility pilot
with a $15 cumulative estimated model-token ceiling. A separate create-only,
private approval artifact binds the manifest, source and protocol commits,
`approval_scope: excluded_feasibility_pilot_only`, and
`v1_approval_reused: false`. Its SHA-256 is
`5a894865c4a8e3a1ffc556ec5efa84d71dbcac94e57e48d6b1b8cb957685779f`
and is pinned in the [operational collector](../../research_ops/m66_collect_pilot_v2.py).
The frozen manifest itself keeps `paid_model_calls_authorized: false`. V1's
approval did not start v2. Exact-head CI passed before execution, and the
live endpoint, price, and empty event-store preflight passed before pair 1.

Before dispatch, the collector checks both clean detached commit heads,
manifest and config hashes, imported runtime path, approval hash and scope,
eight cell identities and order, empty or reconciled dedicated event-store
inventory, and remaining worst-case cost. Immediately before the first paid
arm of **each** pair, it re-reads the selected OpenRouter endpoint and its
$3/$15 per-million-token prices. Every request must select only the pinned
Moonshot AI route with fallback disabled and high reasoning. Every returned
turn must identify the exact selected model revision and upstream. Drift or
provider failure stops further requests.

The operational sequence is fixed:

1. Append one `cell_started` journal row before that arm runs.
2. Run the frozen control or witness runner for that arm.
3. Require a terminal event, close post-terminal coverage bookkeeping, save
   the authoritative trace hash, and append one `cell_completed` row.
4. Replay the score against the build oracle, verify event identities and
   costs, closed reservations, selected endpoint, and private stage
   extraction from the exact protocol checkout. Check the dedicated database
   for unjournaled runs and model work before admitting the next arm.
5. After both arms, require the same build/pair/fixture and write a private,
   create-only pair postcheck receipt. Only then admit the next pair.

An interrupted start, score-invalid cell, missing or failed audit, missing
pair receipt, endpoint drift, or cost mismatch stops the collector. No cell
is retried or replaced automatically. Resume of fully completed pairs
replays their receipts against PostgreSQL; partial pairs require separate
authoritative reconciliation. All pilot traces, model artifacts, stage
outputs, journal rows, and receipts stay in the private `.offsecgym` state.
The per-cell checks are operational gates; they do not tune the model prompt,
witness intervention, validator, range semantics, frozen seeds, arm order,
budgets, or outcome-based policy analysis.

The [predeclared final postcheck](../../research_ops/m66_pilot_v2_postcheck.py)
requires exactly eight score-valid journaled cells and four pair receipts.
It replays every cell from PostgreSQL and emits a create-only aggregate of
trace identities, stage hashes, resource use, and infrastructure gates. A
failed pilot is retained and reported as such; this successful-pilot gate
must not be weakened after observing outcomes.
