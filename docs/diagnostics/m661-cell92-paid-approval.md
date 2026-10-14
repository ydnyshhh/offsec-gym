# M6.6.1 cell-92 recovery approval pin

The human approved **only frozen assigned cells 93–96 once** under the
original $172.80 cumulative estimated model-token cap. Cells 1 and 4 stay
infrastructure missing; cells 13, 14, and 91 retain their provider-failure
outcomes; cells 86 and 92 remain outcome missing. Their task-owned model and
action reservations remain active and are charged conservatively. Neither
cell is retried, replaced, or settled.

The operational safeguards in PR #49 passed exact-head CI and merged as
`53760ac129119c01568c75534bf7a992d0b267ca`, preserving its four commits.
The separate private, create-only approval receipt is stored outside Git at
`.offsecgym/m661-feasibility-v1/cell92-paid-continuation-approval.json` with
mode `0600`. Its SHA-256 is
`27c836e35825d79adaf73b4c6898ddb072cef9d6936860502e3c0a7c9b987568`.
The wrapper pins that digest and the operational merge commit. Its exact-scope
parser accepted the receipt without exposing its contents.

Before any paid call, execution must use a clean checkout at this pin's merge
commit and pass the exact stopped-evidence, authoritative PostgreSQL,
reservation, build, endpoint, price, and budget gates. Only the separate
cells-93–96 collector may run; any interruption or unauditable result stops
the study without a restart. The frozen source, protocol, manifest, model,
checkpoints, seeds, assignment order, and analysis remain unchanged.
