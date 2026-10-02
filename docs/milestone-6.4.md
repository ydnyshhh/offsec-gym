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
and order, target and decoy placements, cross-object reference location,
document/ticket wording, and which member/admin identity witnesses each
property. Keep the same five vulnerability families and a stable count of
configured roots per vulnerable build. Every patched counterpart must
share the vulnerable build's public fixture and differ only in patch state.
Validate each generated pair with scripted oracle/validator tests, unique
root IDs, target reachability, and no accidental cross-tenant access in the
patched sibling. Freeze the generator version, ten held-out seeds, fixture
hashes, and vulnerability manifest before any model run. Treat old v1
results as diagnostics, not observations in the new statistical sample.

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

The proposed primary matrix uses ten held-out v2 range seeds, each with
vulnerable and fully patched counterparts, and worker token budgets of
40k, 60k, 80k, 120k, and 160k. With four aligned arms, that is
`10 × 2 × 5 × 4 = 400` live runs for one model before any replication or
second-model study. This is a proposal, not authorization to spend those
model credits. First publish a machine-readable manifest with the exact
seeds, pair/build IDs, configurations, arm SHAs, model revision/upstream,
budget dimensions, run order, and expected maximum calls/tokens. Estimate
cost and wall time from the manifest and a bounded infrastructure pilot;
review the concrete matrix before launching it. Pilot traces can validate
instrumentation but do not enter the confirmatory sample.

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
between arms on the same seed and patch state. Summarize efficiency with
trapezoidal recall-budget area over the predeclared **40k–160k** interval,
normalized by that interval's width; do not extrapolate the curve to zero.
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
coverage separately. Do not turn absence of a `WorkerObjectiveAction` into
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
   largest token budgets, and each arm without changing frozen worker
   policy. Failures trigger a new protocol version before confirmatory data.
5. The confirmatory analysis code and denominator rules are fixed before
   model runs. No policy-weight search or seed-42 hill climbing is included.

Resumable or preempted worker trajectories are a separate future milestone.
