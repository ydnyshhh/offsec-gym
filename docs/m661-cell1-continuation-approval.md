# M6.6.1 cell-1 continuation approval provenance

The human approved the exact reviewed recovery scope after draft PR #38 passed
both exact-head CI runs and merged as
`479c2395d46935e5c27df304058edae974ffc5cb`.

The private create-only receipt is outside Git at
`.offsecgym/m661-feasibility-v1/cell1-continuation-approval.json`, mode `0600`,
SHA-256 `5b5f7604f5930054eafb6dba6b3e69f169a1386b197d127e624220b1cf5d2860`.
Its contents are not committed. The execution guard verifies its bytes, privacy,
timestamp, human approval, and scope before any continuation artifact or model
call. This commit pins only that hash and its fail-closed test.

The authorized scope is **only frozen cells 2–96 once**, with cell 1 retained as
an assigned, outcome-missing `environment_failed` infrastructure failure and
zero independently audited model-token cost. There is no retry or replacement.
The original `$172.80` cumulative estimated model-token cap still applies.
Source, protocol, manifest, assignment order, model, prompts, checkpoint
interventions, budgets, and provider-health rules remain frozen. The original
stopped journal SHA-256 is
`3c763b636487e39b25fd921570e0ec9d3ab45d18eae10fdd7e14d2d53fb026ae`.

Execution remains gated on a clean exact merged checkout, authoritative
PostgreSQL reconciliation, no active collector, healthy environment and fresh
public selected-endpoint/price check. A new interruption leaves remaining
assigned cells missing until separately reconciled and authorized.
