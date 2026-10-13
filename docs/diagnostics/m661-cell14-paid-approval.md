# M6.6.1 cell-14 paid continuation approval pin

The user separately approved merging the exact cell-14 recovery proposal and
running only frozen assigned cells 15–96 once. The private, create-only
approval receipt has SHA-256
`a9bfe3880e1f2d0eaa9773b588b9cde68138e5b4c19f7f3f643c4b78a2f3519a`.
It binds the operational merge
`33af183147b60d05d3e0539428769266fec5946e`, the frozen manifest and
assignment, both earlier missing runs, the retained provider-failed runs at
cells 13 and 14, all stopped journals and logs, pair-seven preflight, and the
original $172.80 cumulative estimated model-token cap.

The approved continuation closes pair seven only from retained events, starts
one new provider-health epoch at cell 15, and preserves all earlier outcomes.
No retry, replacement, result-based resizing, or change to frozen source,
protocol, model, checkpoints, seeds, or assignment order is authorized.

The receipt remains outside Git. This approval-pin change does not itself
start a collector, alter study evidence, or make a model call. Execution still
requires exact-head CI, a clean merge checkout, authoritative PostgreSQL
replay, no active collector or reservation, and fresh public endpoint and
price checks before the first paid cell.
