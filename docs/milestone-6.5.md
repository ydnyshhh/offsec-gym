# M6.5 research design: from action evidence to validated findings

This is a design for **new protocols**, not a revision of the completed
`m64-v2-worker-primary-1` study. M6.4 is frozen at `50c3374`: 180 feasible
cells were collected, 179 were score valid, and the single provider failure
remains unscored and unretried. Its worker prompts, scheduling policies,
manifest, results, and private traces must not be changed to improve a score.
The [M6.4 report](diagnostics/m64-confirmatory-results.md) is the empirical
starting point.

## What M6.4 supports

At 120k–160k, common-feasible recall AUC was 0.310 fixed sequential, 0.310
matched parallel, and 0.322 opportunity-aware. Every paired interval includes
zero. Parallel runs finished sooner in the observed sample, and
opportunity-aware workers alone were policy-feasible at 40k–80k. Those are
different outcomes: a missing fixed-worker cell is not an observed zero
recall. The post-collection stage ledger found 131 root/run cases where
trusted actions met static proof requirements but no matching finding was
submitted, alongside 130 validated roots. It establishes **action-level
proof capability**, not that an agent noticed, retained, or understood the
evidence. The ledger is descriptive because its code was written after
collection. Root-specific failures, notably the refund transition, warrant
prospective measurement before another scheduler change.

## Study A: common-bootstrap monolithic control

**Question:** How does one persistent structured-memory agent compare with
the frozen worker policies when the initial information boundary is aligned?
Use a new named manifest. Do not append cells to the M6.4 manifest or change
its primary analysis. Running this control on seeds 1001–1010 makes a useful
matched *historical* comparison, but those seeds and their outcomes are now
known to researchers; it is not a fresh held-out confirmatory test.

The current `MonolithicExperimentRunner` does not invoke
`PrerequisiteBootstrap`; `WorkerExperimentRunner` does. Implement a versioned
monolithic control that runs the same deterministic GET-only bootstrap,
records the same 17 expected bootstrap dispatches and snapshot hash, and
exposes its event-backed facts and checked actions to the monolithic agent.
Charge bootstrap actions/HTTP to a separate 32/32 cap in the same way as the
worker arms. Keep the agent-visible range, `known_routes`, structured memory,
provider request and selected endpoint, reasoning setting, validator, and
model-token, model-call, action, HTTP, output, and wall-time ceilings aligned.
The agent's model-token budget remains the stated 40k–160k curve; bootstrap
uses zero model tokens. A bootstrap mismatch or endpoint drift invalidates a
cell. Pin the new runner code, config, fixture hashes, paired build IDs,
cell order, analysis, and cost ceiling before any live call.

Analyze vulnerable recall and patched false findings for each of 40k, 60k,
80k, 120k, and 160k. At low budgets, compare monolithic only with the
opportunity-aware arm; fixed and parallel M6.4 cells were structurally
infeasible. At common budgets, show all four arms, with duplicate submissions,
action/HTTP use, tokens, latency, and root-stage outcomes. Label comparisons
to M6.4 as historical, since provider time and the new control code cannot
be randomized with the completed worker runs. Do not claim that any difference
isolates decomposition unless all relevant runtime and information boundaries
are checked and the remaining historical confounding is disclosed.

## Study B: prospective witness-to-finding conversion

**Question:** Given a frozen probing trajectory, how often can a separate
reporting stage turn trusted action evidence into a validated finding? Select
new paired `tenant_boundary_v2` seeds before looking at their fixtures or
running the model. Exclude 1001–1010 and the seed-1101 pilot. Pin the
exploration policy, prompts, model request and selected endpoint, range
compiler, validator, visibility, budgets, reporter packet schema, analysis,
and stopping rules before collection. Keep hidden oracle material out of all
agent and reporter inputs.

Start with a paired **read-only recovery assay**. Run the frozen probing
policy once per build, preserve its original integrated findings, and hand a
fresh reporter only the resulting trusted request/response evidence bundle,
entity associations, action order, and already-submitted candidates. The
reporter may submit findings through the same sink and validator but cannot
make range requests, alter actions, inspect oracle files, or see patched-state
labels. Record its model calls and tokens separately. Score both the original
integrated submissions and the reporter's *incremental distinct validated
roots*, plus new false findings and duplicate roots. A recovery shows that
some completed traces were reportable with extra inference; it does not by
itself prove that fresh reporting beats an equally funded integrated agent.

If recovery is material, predeclare a separate equal-global-compute comparison:
reserve a fixed reporting allowance from the same total model budget for
both conditions, use the same frozen exploration policy and evidence access,
and compare integrated continuation with a fresh reporter. This prospective
comparison must state precisely whether the two conditions share one probing
trace or use independently sampled trajectories. Do not mix the recovery
assay with this causal estimate.

## Prospective root-stage evaluation

Make the per-configured-root pipeline a primary object:

`Ready → Admitted → Executed → Trace proof → Submitted → Validated`.

Predeclare event/evidence witnesses and precedence before collection. Preserve
an `inapplicable` state where a stage is not part of an arm (for example,
worker admission for monolithic). Report counts and conditional conversion
rates with denominators for each root family, arm, budget, and patch state;
do not collapse distinct root/run opportunities into submission totals.
`Trace proof` means static validator requirements are met by authenticated
artifacts. State transitions additionally need ordered before/action/after
evidence. Unsubmitted transitions cannot be called validated because clone
replay only follows a submitted finding. Keep stage-ledger code versioned
and audit its output against event hashes and authoritative scoring.

Report the refund root separately at every stage. In M6.4 its 89 score-valid
vulnerable opportunities ended 30 not ready, 28 admitted but not executed,
30 executed without trace proof, one proof without submission, and zero
validated. On new seeds, test whether transition witnesses and submission
conversion lag static read/exposure roots. Cell 124 is a diagnostic example,
not a prompt-tuning target. Also report root-level duplicate submissions and
whether findings cite evidence first gathered by another worker; cited reuse
is only a provenance measure, not proof of useful cross-worker reasoning.

## Order and gates

1. Freeze M6.4 and publish this separate design. No further M6.4 cells,
   retries, worker tuning, or changes to its aggregate analysis.
2. Implement and fake-provider test the common-bootstrap monolithic runner.
   Require identical bootstrap snapshot semantics and controller replay.
3. Review a concrete monolithic manifest, endpoint and price snapshot, pilot
   result, projected cost, and stopping threshold before paid collection.
4. Version the prospective stage ledger and read-only reporter contract;
   test trace integrity, oracle isolation, finding provenance, and validator
   replay with fake runs.
5. Predeclare new held-out seed pairs, recovery-assay analysis, resource
   limits, and provider-failure handling. Review its cost before live calls.
6. After those studies, design a second range family with a different
   dependency structure. Treat it as an external-validity test, not another
   tuning set for `tenant_boundary_v2`.

No paid model collection is authorized by this design alone. Each live
protocol needs a reviewable frozen manifest and cost decision.
