# M6.7.1 excluded feasibility paid approval pin

The human approved the exact frozen 128-cell, 64-pair M6.7.1 feasibility run
once, with no retries or replacements, the selected Moonshot AI
`moonshotai/kimi-k3-20260715` endpoint at $3/M input and $15/M output, and the
$230.40 cumulative estimated model-token stop. The approved manifest SHA-256 is
`d51ba9d9dffe12c6e809b32196f2c48937f72e33768ffb2ad050218d4be41e2f`.

The operational collector in PR #54 passed exact-head CI and merged as
`ff12fefa0dc1c6fdb75bb779fde7a82976de4ee3`, preserving its five branch
commits. The private create-only approval receipt is stored outside Git at
`.offsecgym/m671-feasibility-v1/paid-approval.json` with mode `0600`. Its
SHA-256 is
`d99150957c3465058abece0afda41d79cb56b9c903a540e2f98de77067353180`.
The exact-scope parser accepted the receipt without exposing its contents.

This change pins the approval digest and requires the reviewed operational
merge in the execution checkout's history. Before any paid call, collection
must use a clean checkout at this pin PR's merge, the unchanged qualified
source and protocol, the frozen manifest, an empty dedicated PostgreSQL
database, a fresh selected-endpoint and price check, and the private approval
and key. Only one collector may run. Any interrupted or unauditable cell stops
the study without a restart under this approval. The earlier M6.6, M6.6.1,
and M6.7 studies remain untouched.
