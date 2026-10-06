# M6.6 excluded pilot v1: closed with failed infrastructure gates

The approved v1 pilot was stopped and closed without retry. **Four of eight
planned cells are score valid, two Range B vulnerable cells are score invalid,
and the Range B patched pair was never started.** The pilot does not provide
an eight-cell witness-versus-control comparison or a generalization result.
The [failure and causal boundary](m66-pilot-v1-bootstrap-failure.md) were
recorded before reconciliation.

| Frozen arm group | Journaled | Status | Model calls |
| --- | ---: | --- | ---: |
| Range A vulnerable witness and control | 2 | score valid, budget exhausted | 24 |
| Range A patched control and witness | 2 | score valid, budget exhausted | 22 |
| Range B vulnerable control and witness | 2 | score invalid, environment failed in bootstrap | 0 |
| Range B patched witness and control | 0 | unstarted | 0 |

The four Range A runs consumed 351,151 input and 54,739 output tokens. At the
frozen selected-endpoint rates of $3/$15 per million, cumulative **estimated
model-token cost was $1.874538**, below the approved $15 stop. This is the
collector's usage-based estimate; the v1 experiment configs omitted token
prices, so event-level `estimated_cost_usd` is absent. The Range B failures
incurred no model-token cost and no model call.

## Authoritative closeout

The read-only [versioned postcheck](../../research_ops/m66_pilot_v1_postcheck.py)
matched all six journaled source traces against PostgreSQL, verified 2,893
contiguous source events, build/pair/fixture bindings, model usage and selected
endpoint, controller reservation closure, and oracle score replay. It also
identified two cited validator replay runs, each 19 events and zero model
calls. These are validation work, not unjournaled pilot cells. Including them,
the dedicated event store contains 2,931 events across eight runs. The
[aggregate audit](m66-pilot-v1-postcheck.json) records each trace and event
identity hash without publishing private model or fixture artifacts.

The frozen manifest SHA-256 is
`0bda0f40d75c8a683752f20da0cbf09ea8a5e7d1cd39eaaf6c77725bac8aabd8`.
The final private journal SHA-256 is
`74f12ec40b29bfb15aba77e6fded0431466d30028bd3a5eaa907586048150309`.
The private Range B reconciliation receipt SHA-256 is
`2558a69ce5fb9b5547aada57bdae3c74bd08567ff54f0a136484c02db72509c3`.
The committed aggregate JSON SHA-256 is
`13cc690599a2dc1007d035e1b304a4b59fcfef38b621190e20d38b683205c9f3`.
The original source and protocol commits remain
`950bdb746e0d8ae9d68a324f58e5b763c7ddbc1d` and
`840992f760eae35e89ae4993f3283894e86b6fbe`.

The Range B fixture required 33 bootstrap GETs under the pinned source,
while the manifest allowed 32. Both already executed Range B vulnerable runs
stopped after 32 successful GETs with `RunCompleted(environment_failed)` and
no `PrerequisiteBootstrapCompleted`. The detailed
`bootstrap_action_budget_exhausted` label is inferred from that event
boundary and source code, not persisted in `RunCompleted`. Their private
traces were reconciled into the journal without dispatch, model requests,
retry, or replacement. The patched pair remained unstarted because the same
bootstrap shortfall would apply.

The frozen v1 stage extractor also fails on a completed witness trace: it
passes validation and terminal events to a helper that accepts only a
pre-reporting source. We preserve that failed analysis gate. No v1 stage
table or policy effect estimate is reported. The separate v2 draft fixes the
read-only extraction path and proposes fresh excluded seeds, a justified
Range B bootstrap cap, and serialized token prices. It requires a new frozen
manifest and approval before any paid call; none of v1's unused $15 approval
is carried forward.
