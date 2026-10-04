# M6.5.2 eligibility amendment after the cell-2 stop

The [cell-2 stop record](m652-sample-stop-cell-2.md) remains frozen. It
identified a valid ninth-turn post-tool checkpoint followed by a rejected
tenth model reservation under the unchanged 120k-token probe budget. The
original collector retained the cell and stopped, as specified. The user
approved an explicit, narrowly scoped amendment before any resumption.

The [amendment manifest](../../experiments/manifests/m652-budget-preflight-amendment-v1.json)
pins the original sample manifest, two-cell stop journal, run ID, and the
versioned [resume controller](../../research_ops/m652_resume_after_preflight.py).
The resume controller changes **eligibility handling only**. A source may
be retained without reporting branches if all of the following hold:

1. Its source score is valid and its terminal status is `budget_exhausted`.
2. The latest completed-turn checkpoint and its private artifacts verify
   against the authoritative PostgreSQL stream.
3. The only events between that checkpoint and prefix rejection are exactly
   `ContextRetrieved`, `ModelReservationRejected` with
   `model_token_budget_exhausted`, and `ReportingPrefixRejected` with
   `invalid_source_prefix_ValueError`, in that order.
4. No reporting branch exists; no gateway action or model turn follows the
   checkpoint. The source trace and oracle score replay exactly.

Every other prefix-integrity rejection still stops collection. The
controller checks the original two-cell journal hash and reconciles cell 2
before opening any unstarted cell. It calls the pinned collector for one
new cell at a time, accepting only the exact verified preflight pattern;
this keeps a single resume process and prevents any cell retry. A crash
with a started but incomplete cell still requires separate event-store
reconciliation.

The original 48-cell manifest, 24 paired seeds, order, model, Moonshot AI
endpoint, high reasoning, prompts, probe and reporter allowances, range,
validator, source-only proof rule, primary paired analysis, and $108 sample
stop remain unchanged. Cell 2 stays enrolled as an invalid source prefix
in the flow table and contributes no reporting outcome. The controller is
reopened between cells, which can affect operational latency but does not
alter a model request within a cell. No result or interim recovery signal
was used to choose this rule.

Before paid resumption, the amendment script and manifest must be committed
separately, local strict-pattern and PostgreSQL reconciliation checks must
pass, and exact-head GitHub CI must pass. The resumed collection and final
postcheck must preserve this amendment and report all invalid prefixes.
