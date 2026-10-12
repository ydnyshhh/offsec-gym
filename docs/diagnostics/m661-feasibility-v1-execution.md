# M6.6.1 excluded feasibility execution guard

The human approved the frozen M6.6.1 feasibility manifest with SHA-256
`88cda1e9d8977644cc7d7447c44dda15c562bb9e6899feacf935537344271373`
for its 96 assigned cells once, without retries or replacements, under a
**$172.80 cumulative estimated model-token cost cap**. The separate private,
create-only approval receipt has SHA-256
`7ff3db6492353baf794000b26c7151608ec6fcd005bf2b825043874fbf451ed8`.
The receipt and credentials are outside the repository. The manifest itself
continues to say `paid_model_calls_authorized=false`, preserving the separation
between frozen study specification and execution authorization.

This operational change adds only the guarded collector, authoritative
read-only postcheck, and focused fail-closed tests. It does not modify the
frozen source commit `e302b182a6987c84ac542f50c36932de249c99c7`,
protocol commit `d0cd222be911c72fd26eef777bd77bd6234ed383`, manifest,
model request, checkpoint setup, validator, budgets, seed list, or order. The
collector imports runtime code from a clean exact source checkout and
reconstructs the manifest and offline analyzer from a clean exact protocol
checkout. It validates the source/protocol config byte identity, file hashes,
assignment hash, private approval, and a dedicated empty PostgreSQL store
before the first cell.

For every two-cell assigned pair, the collector checks public OpenRouter
endpoint metadata without a key or model request. The selected endpoint must
remain Moonshot AI | `moonshotai/kimi-k3-20260715` at $3/M input and $15/M
output, with no fallback. Each response is checked against the same actual
selected revision and upstream provider. The collector reserves $1.80 before
each cell, the maximum configured cost if all 120,000 tokens were output
tokens. Completed call usage is replayed from PostgreSQL; an incomplete model
call uses the full $1.80 conservative reservation in the cap ledger.

After each assigned cell, the collector checks the private checkpoint receipt,
separate trusted setup run, source event identities and contiguous sequence,
saved trace bytes, controller reservations, selected endpoint, score replay,
and frozen offline checkpoint stage. It writes trace and stage artifacts with
create-only semantics, then appends one completion to the durable journal.
Pair receipts close only after both assigned cells pass audit. An isolated
provider failure is retained as score-invalid after replay, without retry.
The frozen provider-health rule pauses on two consecutive transport failures
or three provider failures in the last ten cells. Endpoint, price, integrity,
request, auth/quota, reservation, and cost problems stop collection. Unstarted
assignments remain missing, and no result-based replacement is permitted.

The postcheck is read-only. It compares every journaled source run, trusted
setup run, and validator replay against the dedicated PostgreSQL inventory;
rebuilds the frozen stage rows; checks pair and endpoint receipts; and computes
the predeclared all-assigned descriptive analysis from completed and missing
assignments. Unexpected analyzer exceptions abort the postcheck. The saved
result should include the exact journal and analysis hashes, observed score
validity, provider failures, and both observed and conservative cost ledgers.
This is an **excluded feasibility diagnostic**, not a confirmatory treatment
estimate. A stopped run stays stopped unless a separately reviewed and
explicitly approved amendment scopes any continuation.
