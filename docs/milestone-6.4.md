# M6.4: orchestration policy evaluation

## Research boundary

M6.3.1 is the frozen opportunity-aware baseline: implementation `f00e966`,
protocol `d2f2fc3`, and single-smoke diagnostic `1cc9b19`. Keep its task
contracts, minimum budgets, and utility weights fixed during M6.4. Do not
rerun seed 42 to tune its score. Ticket's 29,000-token minimum remains a
minimum; refund's 40,000-token admission remains a worker execution issue
when its worker fails to use the route.

The question is whether the architecture and admission policy generalize
across new range instances and compute budgets, while preserving valid
findings on vulnerable ranges and avoiding false findings on matched
patched ranges. Any change to worker prompts, task contracts, utility
weights, or minima starts a separately named protocol.

## M6.4.0: make held-out ranges meaningful

The current `tenant_boundary_v1` fixture varies entity UUIDs and invoice
amounts with the seed. Workspace names and assignments, object placement,
document/ticket text patterns, cross-object references, and decoys remain
fixed. A ten-seed sweep of this fixture would mostly test ID robustness.

Introduce a versioned `tenant_boundary_v2` generator, leaving v1 and its
frozen diagnostics unchanged. Make the seed vary at least workspace labels
and order, target placement and decoy clue placement, cross-object reference
location, document/ticket wording, and which member/admin identity witnesses each
property. Keep the same five vulnerability families and a stable count of
configured roots per vulnerable build. Every patched counterpart must
share the vulnerable build's public fixture and differ only in patch state.
Validate each generated pair with scripted oracle/validator tests, unique
root IDs, target reachability, and no accidental cross-tenant access in the
patched sibling. Freeze the generator version, ten held-out seeds, fixture
hashes, and vulnerability manifest before any model run. Treat old v1
results as diagnostics, not observations in the new statistical sample.

The implementation uses seeds **1001–1010**. It varies account-to-workspace
assignment, witness member, foreign target workspace, object ordering,
document/ticket wording, and retired UUID clues. The clues do not create
additional live assets or undeclared vulnerabilities. The ten paired
Docker/scripted/validator checks passed: each vulnerable build produced five
validated roots and each fully patched sibling produced none. The v1
seed-42 build ID remains `c435a866-da5a-5f65-a2b5-ba2e4c2cc4db`.

## M6.4.1: align the comparison arms

Primary worker-policy arms:

| Arm | Existing mode | Interpretation |
| --- | --- | --- |
| Fixed sequential | `bootstrapped_sequential_workers` | Six fixed worker slices, sequential execution |
| Matched parallel | `bootstrapped_parallel_workers` | Same worker policy with parallel scheduling |
| Opportunity-aware | `admitted_sequential_workers` | State-based admission and protected elastic grants |

These arms share the deterministic 32-action/32-HTTP, zero-model-token
bootstrap, known routes, structured worldview, model provider, validator,
and global worker ceilings. Their distinct task scheduling and budget
ownership are the policies under evaluation. Do not include hard partition
or greedy elastic as primary arms; they remain informative frozen
diagnostics of specific allocation failures.

Include a structured monolithic arm only after aligning its initial
information boundary. Today the monolithic runner has no prerequisite
bootstrap. Either add a versioned monolithic control that receives the same
audited bootstrap facts and charges the same bootstrap HTTP actions, or
report current monolithic-versus-worker results as a broader system-package
comparison. Do not label that unaligned comparison a pure orchestration
effect. Freeze the exact arm code/config SHAs and check all arms' visible
range, validator, model, reasoning setting, action cap, model-call cap,
token cap, and wall-time handling before the matrix starts.

## M6.4.2: predeclared matrix and analysis

The worker-policy matrix uses ten held-out v2 range seeds, each with
vulnerable and fully patched counterparts, and worker token budgets of
40k, 60k, 80k, 120k, and 160k. Three currently aligned worker arms define
`10 × 2 × 5 × 3 = 300` cells. The fixed sequential and matched parallel
implementations reject budgets below **117,000** tokens before starting a
worker: six reserved turns require `6 × (15,500 input + 4,000 output)`.
Therefore 120 cells at 40k/60k/80k are marked **policy infeasible**, with
no model run. The other **180 cells** are prospective live runs with
**20.4 million configured model tokens** and **3,600 model calls** in total.
Provider-reported token use can exceed a preflight reservation, so the token
sum is an allocation ceiling rather than an absolute billing cap. If a
common-bootstrap monolithic control is added, its cells and cost are a
separate manifest revision. This is a proposal, not authorization to spend
model credits. First publish a machine-readable manifest with the exact
seeds, pair/build IDs, configurations, arm SHAs, model revision/upstream,
budget dimensions, run order, and expected maximum calls/tokens. Estimate
cost and wall time from the manifest and a bounded infrastructure pilot;
review the concrete matrix before launching it. Pilot traces can validate
instrumentation but do not enter the confirmatory sample.

The locked prospective matrix is
[`m64-v2-worker-primary-1.json`](../experiments/manifests/m64-v2-worker-primary-1.json),
generated from source commit `4de2249`. Its `order` field covers only the
180 feasible cells. A twelve-run **fake-provider** pilot exercised all three
arms at their smallest and largest feasible token budgets on v2 seed 1001
in both patch states; it passed score-validity, bootstrap, packet, and
lease-cleanup checks without billed model calls.
The paired scripted checks covered all ten seeds. A bounded live provider
pilot on non-held-out v2 seed 1101 ran vulnerable and patched siblings at
40k tokens and a $1 configured cost cap each. It verified selected endpoint
metadata, matched bootstrap snapshots, score validity, and clean controller
replay for both runs; see the [preflight diagnostic](diagnostics/m64-preflight.md).
Its scores are excluded from the confirmatory sample. No confirmatory model
run has started.

The [predeclared analysis code](../src/offsecgym/research/m64_analysis.py)
rejects incomplete cells and changed model revisions, keeps invalid score
states out of paired means, and reports their counts separately. It computes
seed-paired differences and a normalized trapezoidal AUC over the common
feasible 120k–160k interval, plus a separately labeled 40k–160k policy
opportunity curve with structural infeasibility explicitly shown. Completed
OpenRouter model-call events now record the selected endpoint revision and
upstream provider when response metadata supplies them. All 12 response
artifacts from the earlier M6.3.1 smoke identified the same selected
`moonshotai/kimi-k3-20260715` / `Moonshot AI` endpoint; a current live
pilot verified that all six new completed-model events populated both fields.

At the 2026-10-02 [OpenRouter Kimi K3 provider listing](https://openrouter.ai/moonshotai/kimi-k3),
the pinned Moonshot AI upstream lists **$3 per million input tokens** and
**$15 per million output tokens**. If all 20.4 million permitted tokens
were billed at that snapshot's output rate, the token-only planning bound
would be **$306** for the configured allocation. Actual usage can exceed
that allocation; input/output mix, any provider price changes, and fees
affect the actual bill. The
manifest deliberately leaves `usd_ceiling` unset because the experiment
configs do not yet enforce a USD budget. One previous 120k live smoke took
333.59 seconds; the proposed 900-second per-run limit implies 45 hours of
serial run time for 180 cells if every run reaches that limit, excluding
range lifecycle overhead. The current synchronous provider request can
outlive coroutine cancellation, so this is not a strict wall-time ceiling.

The user approved collecting the 180 feasible cells on 2026-10-02 after
this cost review. The collector applies a $306 cumulative **estimated**
token-cost threshold and stops on endpoint drift, unscored runs, or
controller replay failure. That threshold does not promise an exact provider
bill. The collection journal and per-run trace exports remain outside Git
in `.offsecgym/`.

Report structural infeasibility separately from agent failure. For a
common-feasible model-behavior comparison, use the 120k and 160k cells.
Across the full 40k–160k opportunity curve, show the fixed policies'
infeasible region explicitly. If a scalar policy opportunity AUC counts
those cells as zero reachability, label it as a combined feasibility and
model-performance measure; do not present it as observed model recall.

Hold model-call, action, HTTP, and per-call output ceilings fixed across the
token-budget curve, and report which ceiling actually ends each run. Set a
wall-time limit that permits the 160k trajectory or treat timeouts as
predeclared censoring. Randomize run order within matched seed/patch/budget
blocks to reduce provider-time effects. Persist the resolved model revision
and upstream for every turn; partition or restart the analysis if those
change during collection.

Primary outcomes are distinct validated root-cause recall on vulnerable
ranges and false findings per run on patched ranges. Report candidate count,
validation status, duplicates, score validity, and failure reason separately.
For each budget, report recall per actual model token and paired differences
between arms on the same seed and patch state. Summarize model-performance
efficiency with trapezoidal recall-budget area over the common-feasible
**120k–160k** interval, normalized by that interval's width. Report a
separate, clearly labeled **40k–160k policy opportunity curve** that includes
structural infeasibility; do not extrapolate either curve to zero.
Use seed-clustered uncertainty intervals for paired arm differences.
An architecture-by-budget interaction is a secondary analysis; with ten
seeds, report its uncertainty rather than a score-winner claim.

## Objective-stage accounting

Keep separate metrics for where each configured non-identity objective
stops. In the worker arms, define:

- `ReadyCoverage`: distinct objectives whose explicit prerequisites become
  satisfied, divided by configured objectives.
- `AdmissionCoverage` (the operational opportunity-coverage measure):
  distinct READY objectives granted at least their frozen viable minimum,
  divided by distinct READY objectives. Report the count of configured but
  never READY objectives separately.
- `ExecutionCoverage`: distinct admitted objectives with a matching
  `WorkerObjectiveAction` tied to a completed gateway action, divided by
  admitted objectives. Report HTTP response codes separately; a denial can
  still be a completed security probe.
- `AdmissibleUnusedTokens` and `OpportunityDisplacement`: use the frozen
  M6.3.1 event-derived definitions, alongside budget and reservation
  conflict counts.

For the M6.3.1 seed-42 smoke, all five non-identity objectives eventually
became READY, four were admitted, and three reached their objective route:
`AdmissionCoverage=4/5`, `ExecutionCoverage=3/4`. These are diagnostic
reference values, not part of the held-out sample. Monolithic has no worker
admission stage; mark that metric inapplicable and compare its route/evidence
coverage separately. Fixed workers have no explicit READY predicate, so
their ready/admission coverage is inapplicable until a common readiness
ledger exists; their execution coverage remains measurable from packets and
objective actions. Do not turn absence of a `WorkerObjectiveAction` into
proof that no security-relevant action occurred.

Use an offline, oracle-audited stage ledger to classify each missed root as
prerequisite not ready, ready but not admitted, admitted but objective not
executed, objective executed without a witness, witness observed without a
finding submission, or submitted but not validated. The stages need explicit
event/evidence witnesses and a fixed precedence rule so one missed root is
counted once. Keep hidden oracle state out of agent packets and scheduling.

## Gates before confirmatory runs

1. Paired v2 fixtures match publicly and pass scripted vulnerable/patched
   oracle checks across all frozen seeds.
2. All arms use the same model settings and range visibility; every budget
   dimension and bootstrap action is accounted for in a frozen manifest.
3. Event replay reconstructs controller usage, worker ownership, and
   admission/coverage stages; run failures retain typed causes.
4. The infrastructure pilot exercises both patch states, the smallest and
   largest **feasible** token budgets for each arm without changing frozen
   worker policy. Structurally infeasible cells get a no-provider preflight
   check. Failures trigger a new protocol version before confirmatory data.
5. The confirmatory analysis code and denominator rules are fixed before
   model runs. No policy-weight search or seed-42 hill climbing is included.

Resumable or preempted worker trajectories are a separate future milestone.
