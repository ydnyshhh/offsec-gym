# M6.6 confirmatory protocol design

**Status: design, unapproved and unrun.** No confirmatory manifest is frozen and
no paid model collection is authorized by the v1/v2 pilot approvals. This
document preserves those excluded pilots as separate feasibility records. It
does not pool their outcomes or infer a treatment effect from the three valid
v2 pairs.

## Question and fixed treatment

Does the generic witness ledger and bounded reminder improve mechanically
complete, ordered evidence for an assigned state-changing root, compared with
the same monolithic structured agent without those tools? The source/model
policy, `MonolithicSecurityAgent`, `start_witness`/`get_witness`, reminder,
WorldState projection, range/validator semantics, known-routes visibility,
120,000 total tokens and 20 model calls per run, Range A bootstrap 32, and
Range B bootstrap 40 stay as qualified in the v2 source commit. This design
changes assignment, failure handling, and offline analysis only. Before a
freeze, confirm that the selected Kimi K3 revision and Moonshot AI endpoint
remain available; any revision substitution is a new protocol decision.

## Assignment and sample

The proposed cohort has **30 fresh vulnerable seed pairs in each family**:
30 × 2 families × 2 randomized arms = 120 trajectories. A predeclared subset
of 10 of those seeds in each family also gets both patched arms: 40 more
trajectories. Total: **160 assigned cells**. Each family/seed/variant is one
pair with independently instantiated, fixture-matched control and witness
arms. Randomize and freeze arm order per block before collection; balance
first-arm counts within family and variant. A Range A seed and a Range B seed
may share a numeric value because the assignment key is
`(range_family, seed, variant, arm)`. The patched subset is selected without
looking at fixtures or outcomes. Publish a versioned historical exclusion
snapshot and deterministic selection algorithm with the final manifest.
No stopped or failed cell is retried or replaced, and no pilot seed enters the
confirmatory cohort.

The 20,000-replicate [hypothetical precision simulation](m66-confirmatory-precision-design.json)
uses independent paired binary root outcomes and **no v1/v2 treatment
outcomes**. With 30 pairs, median approximate total 95% interval width is
0.392 under 30% discordance, versus 0.440 with 24 pairs. At 40% discordance
the 30-pair median width is 0.458, slightly above the 0.45 design target.
An exact paired test detects a hypothetical 10-point improvement in only
about 9% of the 30-pair simulations. The design is useful for estimating large
effects and documenting uncertainty; it cannot reliably rule out modest
effects. Range B's three roots are correlated within seed. Its by-root
precision is the one-root scenario; family estimates resample whole seeds.
The simulation's normal width is a sizing approximation, while final
intervals use the predeclared seed-cluster bootstrap.

For cost planning only, the stopped v2 pilot used $2.931390 estimated model
tokens across seven started cells, about $0.42 each. That suggests roughly
$67 for 160 cells under similar usage. The configured worst bound at 120k
tokens all priced at the $15/M output rate is **$288**. Neither figure is an
authorization or final stop cap. A live endpoint/price check and explicit
cumulative cap must precede any paid collection.

## Outcome and analysis

The primary assigned root opportunities are `MEMBER-REFUND` for vulnerable
Range A and `B1-SOD`, `B2-REVOKED-ROLE`, `B3-CANCELLED-JOB` for vulnerable
Range B. For every assigned cell/root, the trusted offline extractor sets
`Y=1` if a complete ordered before/action/after witness appears **before that
run terminates**, else `Y=0` when its trace is auditable. Terminal status and
oracle score validity do not erase earlier witnessed actions; this includes
`provider_failed`. An outcome is missing only when the trace cannot be
audited. The collector or postcheck must verify fixture/pair bindings,
contiguous authoritative events, action/evidence identities, response
artifacts, trace hashes, and terminal boundary before passing a record to
`m66_confirmatory_analysis.py`. Pass the complete frozen manifest assignment
keys as a separate argument; the analyzer rejects any omitted or extra cell,
including an unstarted cell after a global stop.

Primary contrasts are witness minus control per **all assigned vulnerable
root opportunities**, separately for Range A and Range B. Range B also reports
each of B1/B2/B3. No missing arm is silently removed or assigned zero. If all
outcomes are auditable, report the point contrast and 10,000-replicate
seed-cluster 95% bootstrap interval. If any outcome is truly unmeasurable,
report the all-assigned denominator, observed counts, missing counts, and
worst/best bounds: missing witness roots set to 0/control to 1 for the worst
bound, reversed for the best. Do not publish a point estimate or bootstrap
interval pretending those outcomes were observed.

Secondary diagnostics: a matched **both-score-valid** pair contrast clearly
labeled conditional; an opportunity-weighted A+B pooled contrast clearly
labeled secondary because B contributes three roots per seed; stage funnels,
validated distinct roots, patched submissions as false claims with rejected
and inconclusive verdicts separately, resource use, and failure counts/rates
by arm/family/status. Patched seeds do not enter the primary witness
denominator. If a patched trace is unauditable, its false-claim count is
missing rather than zero. The final report must identify observed, missing,
and conditional denominators in every table.

## Provider attrition and stopping

An isolated transport/provider failure is retained once, with no request or
cell retry. Reconcile its source run against the authoritative event store,
audit any pre-terminal witness evidence, perform a fresh endpoint/price check,
then execute the other arm of the already randomized pair and continue in
frozen order **only if** the global health gate passes. This prevents first-arm
failure from censoring its partner by policy. A source failure in the other
arm is handled the same way. Agent, environment, and budget terminal statuses
are retained and audited under their own labels; they are not recoded as
provider failures.

The proposed global gate pauses collection on **two consecutive transport
failures** or **three provider failures among the latest ten started cells**.
Endpoint, selected revision, upstream provider, or price drift; auth/quota
failures; trace integrity failure; and a cumulative cost-cap breach stop
collection immediately. A pause requires a recorded operational amendment
and review before any continuation. Never backfill an arm or replace a seed
to restore balance. If collection closes early, keep all 160 assignments in
analysis with unstarted cells as missing and publish attrition bounds.

## Freeze gates before collection

1. Merge the v2 closeout only after its exact result-head CI passes. Keep the
   v2 manifest, journal, audits, and descriptive report immutable.
2. Review and test the new extractor-to-analysis adapter with synthetic
   auditable `provider_failed`, unauditable, unstarted, and asymmetric pair
   cases. Verify root ontology, Range B timing, patched verdicts, and no
   post-terminal evidence.
3. Freeze one source commit, protocol commit, model revision, selected
   endpoint, price receipt, file hashes, 160 ordered assignments, seed
   exclusion registry, configuration, offline extractor/analyzer, provider
   health rule, cost stop, and analysis output schema in a **new** manifest.
4. Pass focused and exact-head full CI. Obtain separate approval for that
   final manifest and cumulative paid-call cap. Run the 160 cells once in
   frozen order only after approval.

The current artifact implements the analysis contract and a design precision
study. It is not an executable collector or a frozen confirmatory protocol.
