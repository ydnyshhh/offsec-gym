# M6.6 excluded pilot v1: Range B bootstrap failure

The approved eight-cell v1 pilot stopped after starting its Range B vulnerable
pair. The frozen manifest SHA-256 is
`0bda0f40d75c8a683752f20da0cbf09ea8a5e7d1cd39eaaf6c77725bac8aabd8`.
The stopped private journal SHA-256 is
`10b205586cacccfa12d2fc4e9ef64654b2b27cfa28ee7a6d278066a5c0620695`.
The source and protocol commits, model, endpoint, prompt, budgets, selected
seeds, and cell order remain frozen.

Cells 1–4, both Range A vulnerable and patched pairs, are journaled and score
valid. Their cumulative estimated model-token cost is **$1.874538**. Cells 5
and 6, the Range B vulnerable control and witness arms, are authoritative
`environment_failed` runs. Each has 550 contiguous PostgreSQL events, 32
completed bootstrap GETs, no `PrerequisiteBootstrapCompleted`, no model call,
no finding, and one terminal `RunCompleted` at event 550. Both used the same
Range B build. Cells 7 and 8, the Range B patched pair, have not started.

The Range B fixture has 18 visible users and three organizations. The pinned
bootstrap implementation reads `/api/me` for each user and five collections
for a representative of each organization: `18 + 3 × 5 = 33` GETs. The v1
bootstrap cap is 32 actions and 32 HTTP requests. The causal label
`bootstrap_action_budget_exhausted` is an **inference** from the source code,
fixture, and event boundary; `RunCompleted` records `environment_failed` but
does not persist that detailed failure string. There is no evidence of a
provider failure or model behavior in either Range B run.

`research_ops/m66_reconcile_bootstrap_failure.py` is a read-only PostgreSQL
audit followed by a create-only local journal reconciliation. It pins the
stopped journal bytes, both run IDs, build and pair binding, fixture size,
all 550 event identities per run, terminal status, 32 successful bootstrap
actions, absence of model work and active reservations, and the unchanged
manifest/approval and source/protocol checkouts. `--check-only` passed against
the authoritative event store before this amendment was committed. After
exact-head CI, the script may save each existing trace and mark cells 5 and 6
score invalid in the private journal. It cannot dispatch, reset, retry, or
replace a cell. A receipt will preserve event and journal hashes.

The v1 pilot **fails its infrastructure feasibility gate** and cannot support
an eight-cell policy comparison. We will not start the deterministic-failure
Range B patched pair under v1. A new protocol would need a separately hashed
manifest, a justified Range B bootstrap cap, fresh endpoint and price check,
and separate approval before any paid model call. The v1 pilot remains
excluded from confirmatory M6.6 analysis.

A separate read-only extraction attempt on the first score-valid witness
trace found an offline analysis defect. The frozen
`research_ops/m66_extract_stages.py` passes the full run trace to
`build_reporter_bundle`, but that helper rejects `FindingValidated` and
`RunCompleted` events because it was designed for a pre-reporting source
prefix. The live trace contains ten validation events and one run completion,
so extraction stops before returning stages. The pinned v1 analysis code is
unchanged; no v1 stage result is claimed. A future protocol must define a
versioned read-only extraction fix and test it on a completed fake-provider
trace before freezing a new pilot.

The v1 model-token cost above comes from collector accounting using the
frozen $3/$15 per-million input/output prices. Its experiment configs did not
set per-token prices, so `ModelCallCompleted.estimated_cost_usd` and the
offline extractor's summed event cost do not represent that operational cost.
This measurement limitation is preserved in the v1 record.
