# M6.6 design: witness-oriented exploration

**Status:** the opt-in oracle-free witness ledger and policy runner are
implemented for SaaS and enterprise change control. The approved excluded
eight-cell v1 pilot [closed with failed infrastructure
gates](diagnostics/m66-pilot-v1-results.md). Four Range A cells are score
valid; two Range B vulnerable cells are score invalid after exhausting the
frozen 32-action bootstrap cap; the final patched pair remains unstarted.
The two failed runs were reconciled without retry. A separate v2 excluded
pilot [is frozen but unrun](diagnostics/m66-pilot-v2-freeze.md) with fresh
seeds and a 40-action Range B bootstrap cap. Its manifest does not authorize
paid calls; no v2 paid call or confirmatory M6.6 sample has run.
[M6.5.1](diagnostics/m651-witness-recovery-v2-results.md)
found 19 successful unauthorized refund actions across ten vulnerable runs,
yet only two runs held a complete ordered, same-identity paid-before /
refund / refunded-after witness. A read-only reporter cannot generate a
missing before or after observation.

## Question

Does a generic, explicit witness-construction state improve the rate at
which an agent turns a state-changing authorization hypothesis into complete
trusted evidence, without increasing unsupported findings or exceeding the
same action and model budget?

This intervention must not tell the agent that the refund route is
vulnerable. It applies whenever the agent proposes to test a state-changing
authorization or workflow hypothesis. The controller exposes **what evidence
is still missing**, not whether the hidden policy says the behavior is a
vulnerability.

## Generic witness ledger

Represent an in-progress witness with a typed key such as
`(subject_identity, object, candidate_transition, range_generation)`, and
three event-backed evidence slots:

```text
before:  read of the same object by the same identity, with state and sequence
action: candidate state-changing request and observed result
after:   read of the same object by the same identity, after the action
```

Each slot cites an exact gateway action/evidence ID and records whether the
response was absent or truncated. `complete` requires
`before.sequence < action.sequence < after.sequence`, a stable actor and
object, and mechanically verified state values. A failed, blocked, or
unobserved action remains distinct from a successful transition. The ledger
never marks a security violation from response status alone and never reads
hidden oracle/fixture properties during agent execution. It may say “the
after-read is missing”; only offline validation decides whether the resulting
witness proves a configured root.

The agent can create or inspect a witness hypothesis through bounded typed
tools. Automatic context should show a small active set of incomplete
witnesses and their exact missing slots. A hypothesis is created from a
model-proposed target and route or a gateway-observed state-changing action,
not from hidden ground truth. Event replay must reconstruct the ledger across
worker or process boundaries. The existing action reservations and coverage
leases continue to apply; the witness ledger does not bypass route or budget
policy.

## Controlled comparison

Use a new frozen, paired vulnerable/patched sample. Run both policies on
separate, fixture-matched instances of each seed/build and randomize their
execution order: the existing exploration policy versus that policy plus
the generic witness ledger and bounded reminder. Hold model request,
selected endpoint, range visibility, bootstrap, validator, total
token, call, action, and HTTP budgets and stopping rules fixed. Do not change
the scheduler in the same protocol.
Treat extra ledger interactions and reminders as part of the intervention
and count their context, tokens, and action cost. The control may still build
witnesses unaided.

The primary intention-to-treat outcome should be **complete, mechanically
verified ordered witnesses per assigned root/run opportunity**. Also report
distinct validated findings and patched false findings. Conditional conversion
from successful transition to complete witness is important diagnostically, but
it cannot by itself be the causal primary contrast: the intervention may
change which transitions get attempted or succeed. Predeclare the complete
`attempt → success → before/action/after proof → submission → validation`
stage table, with failed/invalid runs retained and uncertainty clustered by
seed pair.

For Range B's queued change, preserve a finer event-derived stage table:

| Stage | Trace question |
| --- | --- |
| Queued | Was a deployment job enqueued, and at which logical tick? |
| Cancel attempted | Was cancellation requested before the job's due tick? |
| Cancel committed | Did the canonical change reach the cancelled state? |
| Worker execution | Did the queued job execute and change deployment state? |
| Witness | Was ordered before/action/after evidence completed? |

Record intervening HTTP requests and logical ticks between queue and cancel
for both arms. Extra witness reads can change the opportunity itself, so the
stage breakdown is needed to interpret a difference in complete witnesses.

To assess transfer beyond one refund workflow, add a second synthetic
state-changing family with a different dependency structure **before** a
confirmatory generalization claim. Initial fake-provider tests may use the
current SaaS range; success there alone supports only that range. The new
family needs its own selectively patched sibling, hidden policy, deterministic
validator, and trace-proof predicate. Do not turn the generic tool into a
list of known vulnerable routes.

## Engineering and preflight gates

- Mechanical slot matching rejects cross-identity, cross-object,
  cross-generation, out-of-order, absent, and truncated evidence. It handles
  a state change that occurred before the before-read as **incomplete**, not
  retroactively proven.
- Fake-provider tests show the same range action permissions and budget
  accounting in both arms, replayable event state, no oracle leakage, and
  independent validation on vulnerable and patched builds.
- The manifest freezes new seeds, range family, policy text/tool schema,
  checkpoint and stop conditions, selected endpoint, price snapshot,
  analysis code, and explicit estimated-cost ceiling. Pin both the source
  commit and the separate hashes of the model policy, tool schema, range
  surface, pair runner, witness policy, and analysis. The Range B qualification
  bundle hash covers range mechanics only. Run a non-sample
  feasibility pilot before requesting approval for paid collection.
- Record witness completion, duplicate/abandoned witness attempts,
  additional reads, time and tokens to proof, distinct-root recall, and
  false claims. A higher witness rate purchased by large redundant action
  volume is reported as such.

M6.6 addresses the action-to-proof bottleneck. It does not retune the
read-only reporter or answer M6.5.2's fresh-versus-continued-context question.

## Implementation boundary

`EventWitnessLedger` persists only a typed model-proposed hypothesis. It
projects before/action/after slots on demand from authoritative gateway events
and verified response artifacts, so a process restart can reconstruct its
state without a mutable process-local ledger. It requires the same actor,
object, range generation, ordered action sequences, complete responses, a
successful candidate action, and an observed state change. An intervening
state-changing request to the object makes the proof ambiguous. A complete
ledger entry is an **observed transition**, not a security verdict or proof of
causality beyond the observed sequence. Hidden oracle information is not read
by the agent-facing tool.

`WitnessPlanningExperimentRunner` enables the typed `start_witness` and
`get_witness` tools plus a bounded reminder while otherwise inheriting the
M6.5 monolithic control path. `analyze_witness_policy` defines a four-cell
seed-block analysis with the intention-to-treat assigned-root denominator,
seed-pair bootstrap, conditional transition-to-witness diagnostic, patched
false findings, and resource totals. No sample collector or historical control
has been repurposed for this study.

The current implementation does not automatically open a hypothesis for every
state-changing gateway action; the model must use the opt-in tool. The second
synthetic family is implemented and passed no-model qualification. Before live
collection, freeze the policy and predeclared trace-stage extraction, create a
costed manifest and stop rule, and run a separately approved non-sample pilot
on one Range A and one Range B seed. The fake-provider pair verifies runner
parity and validation rejection, but it does not establish model behavior or
a complete-witness improvement. No generalization claim follows yet.
