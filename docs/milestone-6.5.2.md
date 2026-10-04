# M6.5.2 design: equal-compute reporting context

**Status:** opt-in engineering implementation with fake-provider and PostgreSQL
branch tests; no frozen M6.5.2 manifest, paid pilot, or sample has run.
[M6.5.1](diagnostics/m651-witness-recovery-v2-results.md) is
complete and frozen. Its 4/9 recovery result used extra reporter inference;
it cannot identify whether a fresh context helped relative to spending the
same inference on a continuation of the probing model.

## Question and estimand

For a **single immutable probing prefix**, does a fresh reporting context
recover more distinct roots than continuing the probe model's actual context
when both receive the same read-only evidence access and reporting allowance?
Every eligible prefix is evaluated in both arms. The primary paired contrast
is fresh minus continuation in newly validated distinct roots, conditional
in the offline analysis on a root having complete source-trace proof and no
validated finding **before the split**. These conditions are determined from
the shared pretreatment prefix, not from either arm's outcome. Also report
unconditional per-root coverage and paired patched false findings.

This estimates the effect of the specified **context carryover policy** on
reporting from frozen evidence. It does not estimate the benefit of extra
compute over no continuation, nor the effect of changing exploration. The
existing structured monolithic agent carries only its most recent model
output and bounded working state between turns; it does not carry a full
transcript. The protocol must name and hash exactly which state is carried,
rather than describe the treatment as generic long-history attention debt.

## One source prefix, two isolated branches

1. Select new paired vulnerable/patched seeds before running or inspecting
   their fixtures. Keep the M6.5.1 ten seeds and both pilots out of the
   confirmatory sample. Pin a model, selected endpoint, range/compiler,
   bootstrap, visibility, validator, source-probe policy, price snapshot,
   branch order, budgets, stopping rules, and analysis code in a new manifest.
2. Run one probe per build to a deterministic controller checkpoint after
   processing all tools from its last model turn. Persist the exact source
   event prefix, request/evidence artifacts, model response and opaque
   reasoning items, structured working state, already submitted findings,
   usage, and hashes. No validator decision or hidden oracle data enters an
   arm's model input. A provider-failed or unreconstructable prefix is
   retained and handled under a prespecified invalid-prefix rule.
3. Fork **that same byte-identical prefix** into two isolated branch event
   stores and artifact namespaces. Preserve source action/evidence IDs and
   run/generation bindings while ensuring that arm A cannot observe arm B's
   findings or model turns. The current M6.5.1 runner appends one reporter to
   one run; it is not a branch primitive. Branch-specific finding sinks,
   validation contexts, and immutable source references must be implemented
   and fake-provider tested before live collection.
4. Both arms receive the same reporting-phase instruction, frozen evidence
   index, read-only retrieval tools, typed finding tools, validator, and
   token/call/output/retrieval/submission/wall limits. Neither arm can issue
   HTTP or reset the range after the split. The **continuation-context arm**
   additionally receives the exact hashed model carry state at the checkpoint;
   the **fresh-context arm** begins without that carry. Both may retrieve the
   same source artifacts. Any worldview snapshot supplied automatically must
   be identical in both arms or in neither arm.
5. Randomize which arm executes first for each source prefix and record wall time
   and selected endpoint. Validation and offline oracle analysis run only
   after both branches close. Source evidence remains immutable; stateful
   transition replay uses separate branch-safe clones.

The provider adapter makes stateless Responses requests with `store: false`
and explicitly carries encrypted reasoning output. The opt-in probe now saves
a private post-tool checkpoint containing its base items, exact provider carry
items, rendered working state, and source/request digests. A PostgreSQL branch
store binds two private event streams to the same immutable source prefix and
rejects gateway events. The paired runner gives both arms the same
`ReadOnlyReporter` instruction, tools, evidence packet, and added budget; only
the continuation arm receives the checkpoint carry. This is specifically a
**reporting-context carryover** treatment, not a continuation of the probe's
original instruction or tool set. Branch-specific model artifacts, findings,
validation, and deterministic score replay are implemented. The checkpoint
artifact is verified against its event, but a production pilot still needs a
frozen manifest and an audit of exact continuation-request reconstruction.

## Equal compute and reachable compute

Both branches receive the same **additional** nominal token and model-call
allowance after the common probe. The manifest must state the shared source
probe spend separately from each branch's added spend; source compute is
counted once for total study cost. Record actual input/output tokens,
request-size preflight reservations, reachable calls, context size, and
terminal reason per arm. A preflight failure is a measured treatment outcome,
not a reason to add tokens to that arm or rerun its pair. Do not condition the
primary paired contrast on both arms reaching the same number of calls:
reachability is affected by context policy. Pilot the fixed allowance and
context bound on non-sample prefixes for infrastructure feasibility only.

## Outcomes and analysis

- **Primary:** within-prefix paired difference in newly validated distinct
  roots among pretreatment complete-proof, integrated-missed opportunities.
  Preserve discordant-root counts and uncertainty resampled by seed pair;
  never treat roots from one seed as independent samples.
- **Controls:** patched new false findings, rejected claims, duplicate
  validated submissions, exact evidence citation/provenance, and no
  post-checkpoint gateway actions.
- **Resource outcomes:** branch input/output tokens, realized cost, calls,
  preflight failures, context size, retrievals, latency, and evidence reuse.
  Report nominal and reachable compute separately.
- **Stage accounting:** useful transition/exploit executed, complete witness,
  appropriate finding submitted, and validator acceptance. Keep refund
  separate because complete ordered proof was rare in M6.5.1.

The analysis must retain all enrolled prefixes, invalid source prefixes,
provider failures, and branch failures in a flow table. A score-invalid
branch is never silently replaced. The original M6.5.1 sample is not a
control arm for this experiment.

## Gates before a costed live pilot

- A fake-provider paired run proves identical source-prefix hashes, distinct
  branch event streams, equal evidence/tool/budget policy, no cross-arm
  leakage, no post-split HTTP, and independent deterministic score replay.
- A checkpoint round trip reconstructs the next continuation request byte
  for byte, including post-tool outputs and encrypted reasoning items, without
  replaying a paid model call.
- Range generation, source artifacts, and transition replay remain valid in
  each isolated branch. Controller reservations close after branch failure.
- A versioned pilot manifest, explicit endpoint and price snapshot, worst-case
  cost bound, stopping rules, and separate paid-call approval are recorded
  before any live model request. Pilot outcomes cannot tune the confirmatory
  prompt or seed selection.

No scheduler, M6.5.1 reporter cap, or frozen result is changed by this design.

## Implementation boundary

`PairedReportingContextRunner`, `ProbeCheckpointStore`, and
`ReportingBranchStore` implement the opt-in execution path. The read-only
`audit_paired_branches` verifies source hashes, branch attribution, first
request context, equal non-context request fields, model-call limits, and
absence of post-split gateway actions. `analyze_paired_reporting` provides the
pre-treatment proof-eligible paired contrast and seed-pair bootstrap from
audited records. The historical collectors keep their source-drift checks;
their tests pin the original collection commits so later development does not
rewrite a frozen protocol.

This is **not yet a runnable confirmatory collection**. Before paid use, freeze
new seeds, endpoint and price snapshot, probe checkpoint rule, branch caps,
artifact retention, cost stop, invalid-prefix disposition, and collector
reconciliation in a versioned manifest. Run a non-sample pilot only after its
costed protocol and approval. Source plus both branches count toward total
study cost; score-invalid branches remain recorded rather than replaced.
