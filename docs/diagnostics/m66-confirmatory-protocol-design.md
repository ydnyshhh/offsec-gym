# M6.6 confirmatory protocol design

**Status: freeze preparation, unrun.** No confirmatory manifest is frozen and
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
Range B bootstrap 40 stay as qualified in the v2 source commit
`950bdb746e0d8ae9d68a324f58e5b763c7ddbc1d`. This design
changes assignment, failure handling, and offline analysis only. Before a
freeze, confirm that the selected Kimi K3 revision and Moonshot AI endpoint
remain available; any revision substitution is a new protocol decision.

## Assignment and sample

The proposed **smallest effect size of interest (SESOI) is 0.20**, a 20
percentage-point improvement in complete witness probability per assigned
root pair. This is a design threshold, not a result or a minimum effect the
study promises to find. The proposed cohort has **60 fresh vulnerable seed
pairs in each family**: 60 × 2 families × 2 randomized arms = 240
trajectories. A predeclared subset of 10 of those seeds in each family also
gets both patched arms: 40 more trajectories. Total: **280 assigned cells**.
Each family/seed/variant is one
pair with independently instantiated, fixture-matched control and witness
arms. Randomize and freeze arm order per block; balance first-arm counts
within family and variant. A Range A seed and a Range B seed
may share a numeric value because the assignment key is
`(range_family, seed, variant, arm)`. The patched subset is selected without
looking at fixtures or outcomes. Publish a versioned historical exclusion
snapshot and deterministic selection algorithm with the final manifest.
No stopped or failed cell is retried or replaced, and no pilot seed enters the
confirmatory cohort.

Globally interleave families before collection. For epoch `j=1..60`, assign
one vulnerable block from each family, with their A/B order from a frozen
balanced randomized list (30 A-first, 30 B-first). If epoch `j` is in the
preselected 10-epoch patched subset, insert the corresponding patched block
for each family immediately afterward in the same A/B family order. Within
each block, use a separate balanced randomized control/witness order. Thus
there are two frozen randomizations: **family block order** and **arm order
within block**. The global schedule has at most two consecutive blocks from
one family, and patched blocks appear across the collection rather than at
the end. Pin both pseudorandom seeds, the generated schedule, and its hash in
the final manifest. Any early stop retains every unstarted assignment.

The 20,000-replicate [hypothetical precision simulation](m66-confirmatory-precision-design.json)
uses paired binary root outcomes and **no v1/v2 treatment outcomes**. Under
the hypothetical witness-only 0.25 / control-only 0.05 discordance scenario,
the two-sided exact McNemar test rejects at 0.05 in about **40% of 30-pair**,
**70% of 50-pair**, and **80% of 60-pair** simulated samples. A hypothetical
10-point improvement still has only about 22% detection probability at 60
pairs. The 60-pair choice targets the stated 20-point SESOI under the stated
scenario; other discordance structures change power. A nonsignificant result
cannot establish that the ledger has no useful effect.

Range B's three roots share one trajectory. The simulation also varies
within-seed root correlation `ρ` over 0, 0.5, and 0.9, always computing the
family interval from whole-seed averages. At 60 pairs, its hypothetical
median total interval width ranges from about 0.147 to 0.248 across those
scenarios; it never treats 180 roots as independent seeds. Each B root's
individual precision follows the one-root scenario, and the final family
bootstrap resamples whole seeds.
The simulation's normal width is a sizing approximation, while final
intervals use the predeclared seed-cluster bootstrap.

For cost planning only, the stopped v2 pilot used $2.931390 estimated model
tokens across seven started cells, about $0.42 each. That suggests roughly
$118 for 280 cells under similar usage. The configured worst bound at 120k
tokens all priced at the $15/M output rate is **$504**. Neither figure is an
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
`m66_confirmatory_extract.py` clips trace events, completed gateway actions,
and requests at `RunCompleted.sequence_number` before applying the frozen root
predicates. A post-terminal after-read cannot complete a pre-terminal
witness; the adversarial B1 test covers a `provider_failed` terminal between
action and after-read.

Primary contrasts are witness minus control per **assigned vulnerable root
pair** (one root in both arms), separately for Range A and Range B. The
denominator `assigned_root_pairs = N_seeds × N_roots` is per arm; the dataset
contains `assigned_arm_root_outcomes = 2 × assigned_root_pairs`. Thus the
contrast is `W_witness/N − W_control/N`. Range B also reports
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
to restore balance. If collection closes early, keep all 280 assignments in
analysis with unstarted cells as missing and publish attrition bounds.

## Freeze gates before collection

1. Merge the v2 closeout only after its exact result-head CI passes. Keep the
   v2 manifest, journal, audits, and descriptive report immutable.
2. Review and test the new extractor-to-analysis adapter with synthetic
   auditable `provider_failed`, unauditable, unstarted, and asymmetric pair
   cases. Verify root ontology, Range B timing, patched verdicts, and no
   post-terminal evidence.
3. Freeze one source commit, protocol commit, model revision, selected
   endpoint, price receipt, file hashes, 280 ordered assignments, seed
   exclusion registry, configuration, offline extractor/analyzer, provider
   health rule, cost stop, and analysis output schema in a **new** manifest.
4. Pass focused and exact-head full CI. Obtain separate approval for that
   final manifest and cumulative paid-call cap. Run the 280 cells once in
   frozen order only after approval.

The preparation branch adds a versioned 162-seed exclusion registry, a
deterministic 280-cell assignment generator, terminal-bounded stage adapter,
provider-health rule, source/journal audit, and an execution wrapper closed by
zero-value manifest and approval hash pins. The frozen manifest must be
generated once after a green protocol merge and a fresh public endpoint/price
check. The wrapper stays closed until the user separately approves that exact
manifest hash and cumulative cost ceiling. The $504 bound is the configured
worst-case estimated model-token cost (280 × 120,000 tokens at the output
rate); it is a proposed ceiling, not authorization or an expected bill. No
third excluded pilot is planned.
