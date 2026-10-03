# M6.5 prospective witness-to-finding study: implementation preflight

This separate branch develops the prospective reporter protocol while the
approved common-bootstrap monolithic control collects from the main checkout.
No Study B model calls are authorized or running.

## Seed and analysis declaration before new fixture inspection

The prospective paired seed set is **2001–2010**, inclusive, for
`tenant_boundary_v2`: ten vulnerable and ten fully patched builds. It excludes
the known M6.4/M6.5 historical seeds 1001–1010 and the seed-1101 monolithic
pilot. The seed set is declared here before generating or inspecting those
builds. There is one frozen probing trajectory per build; the fresh reporter
receives the resulting trusted action evidence. There is no second probe run
for the reporter condition.

The primary descriptive outcome is incremental distinct validated root causes
from the reporter after the original integrated submissions. Record original
and reporter false findings, duplicate validated roots, reporter model calls,
input/output tokens, and each configured root's Ready → Admitted → Executed →
Trace proof → Submitted → Validated stages. Report the refund root separately.
Patched roots are inapplicable in the root pipeline and patched false findings
are recorded separately. A provider-failed probe or reporter is retained,
unscored for the affected stage, and never silently retried.

The reporter has no live HTTP tool. Its packet includes ordered action IDs,
trusted request/response references, separate route-target and response-object
IDs, and existing candidates. Full response bodies require explicit read-only
lookup. The packet builder checks same-run/same-generation artifacts and
response hashes; finding citations must refer to those packet actions. Oracle
and patched-state labels enter only the offline evaluator after validation.

The proposed live assay uses the unchanged opportunity-aware sequential worker
policy, Kimi K3 with high reasoning and the same selected Moonshot AI endpoint
as M6.4. The separate manifest below pins the exact request, budgets, source,
build pairs, cell order, cost threshold, and stopping rules. **No paid Study B
run will start until exact-head checks and a separate cost decision are
complete.** This recovery assay measures reportability with
extra inference; it is not an equal-compute estimate of reporter benefit.

## Reviewable offline protocol

The [20-cell manifest](../../experiments/manifests/m65-witness-recovery-v1.json)
was generated after the seed declaration commit `1048837`. Its SHA-256 is
`c7f9da7d873430b59a322769643fc08f62eb73ea3849942c715253b2554ac45a`;
it pins source commit `7108976`, both configs, paired build IDs and fixture
hashes, deterministic cell order, the reporter contract, and source hashes.
The probe keeps the original opportunity-aware worker policy and its 120,000
model-token/20-call task budget. The fresh reporter has 80,000 tokens, four
calls, a 4,096-token per-call output cap, and 300 wall seconds. The controller
declares a combined 200,000-token/24-call budget for atomic reservations;
the worker task budget remains 120,000. This split needs an end-to-end
PostgreSQL fake-worker check before any paid call to show that the larger
controller declaration does not expand probe admission or trajectory budget.

The selected endpoint is pinned as `moonshotai/kimi-k3-20260715` from
`Moonshot AI`. At the observed $3/M input and $15/M output rates, treating
**all** 200,000 configured tokens in every one of 20 cells as output gives a
conservative $60 estimated token-cost ceiling. That is a proposed threshold,
not an approval or a prediction of actual spend. The collector reserves this
worst-case $3 per cell before starting it and stops on endpoint drift,
unscored stages, artifact or packet mismatch, incomplete cells, or a failed
controller replay. A provider failure remains in the journal without retry.

Offline checks completed so far:

- The reporter and packet expose evidence lookup and finding submission only;
  no live HTTP dispatch tool is present.
- Same-run and same-generation artifact checks, response hashes, foreign
  citation rejection, oracle isolation, model-call artifacts, provider-error
  preservation, and a full fake probe→reporter→validator lifecycle passed
  targeted tests.
- The prospective ledger checks packet hashes and the reporter boundary,
  counts configured roots once, and marks patched roots inapplicable.
- All 179 score-valid frozen M6.4 traces were accepted by the packet builder
  in an offline *format-feasibility* check; this says nothing about future
  reporter success.

Before a pilot, require exact-head CI, a PostgreSQL fake-worker budget check,
and a review of the committed manifest and cost ceiling. A two-cell
non-sample pilot could use paired seed 2101 with the same policy and at most
$6 estimated token cost, but that pilot is not authorized. It must stay
outside the 20-cell prospective sample and cannot be silently folded into
the primary analysis.
