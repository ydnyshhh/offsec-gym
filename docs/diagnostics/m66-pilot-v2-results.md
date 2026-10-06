# M6.6 excluded pilot v2: closed after a provider transport failure

The separately approved eight-cell v2 feasibility pilot stopped at cell 7
without retry or replacement. **Six cells are score valid, cell 7 is retained
as `provider_failed` and score invalid, and cell 8 was never started.** Three
control/witness pairs passed their per-cell and pair-local infrastructure
checks. The fourth pair has a successful live endpoint preflight but no pair
postcheck receipt. This is a failed eight-cell feasibility pilot, not an
M6.6 witness-policy effect estimate or a confirmatory sample.

| Frozen group and arm order | Journaled | Terminal status | Score valid |
| --- | ---: | --- | --- |
| Range A vulnerable: control → witness | 2 | budget exhausted, budget exhausted | 2 |
| Range A patched: witness → control | 2 | budget exhausted, budget exhausted | 2 |
| Range B vulnerable: witness → control | 2 | budget exhausted, agent failed | 2 |
| Range B patched: witness → control | 1 | provider failed; control unstarted | 0 |

The seven journaled cells completed 73 of 74 started model calls and used
553,500 input and 84,726 output tokens. At the pinned $3/$15 per-million-token
prices, their cumulative **estimated model-token cost was $2.931390**, below
the separately approved $15 ceiling. This is an event-usage estimate, not a
provider billing statement. The failed call has no completed usage record.
The approved operational head `b0220079ac7248469dcb4bdaec869f646cfb831e`
passed exact-head CI runs 37506048234 and 37506041573 before collection.
An initial detached launch exited before creating a journal, range state, or
authoritative run; those zero-work boundaries were reconciled before the sole
persistent collector began. No pilot cell was retried.

## Failure boundary and authoritative audit

The [versioned, read-only failure postcheck](../../research_ops/m66_pilot_v2_failure_postcheck.py)
matched the final private journal (SHA-256
`7556e09c4cb7984d56a35ba110ff3ce40e26eaf464d74c6d0cb837a4ae97ea8b`)
against the dedicated PostgreSQL event store. The
[redacted aggregate](m66-pilot-v2-postcheck.json) records seven source trace
and event-identity hashes, **6,128 contiguous source events**, three prior
pair receipts, six pinned stage-extraction outputs, and oracle score replay
for every score-valid cell. The database contains exactly those seven source
runs and one cited validator replay run; the replay has 19 events and no model
work. There are no extra source runs or model calls. All completed model turns
selected the frozen Moonshot AI revision. Each of the four live pair preflights
matched that endpoint and the pinned prices.

Cell 7's authoritative trace has 1,044 contiguous events. Its Range B
prerequisite bootstrap completed **33 actions and 33 HTTP requests** under the
v2 allowance of 40, so the v1 bootstrap shortfall did not recur. After nine
completed model calls, a tenth call started and recorded
`ModelCallFailed(reason_code="provider_unavailable", http_status=None)`.
`RunCompleted(provider_failed)` is event 1,044. There are no findings or
validation verdicts in that run, no model or gateway work after its terminal
event, and no active worker, action, coverage, model, or admission reservation.
The event identifies a transport-level provider failure; without an HTTP
response it does not establish whether the fault was OpenRouter, its upstream,
or the network path. The collector journaled the score-invalid run and stopped
before the planned patched control arm.

The frozen manifest SHA-256 is
`b8dbdc28689e7299c4f5f099ce8e3f788483de7b558c0319043fae7ca43fe528`.
Model execution used source commit
`950bdb746e0d8ae9d68a324f58e5b763c7ddbc1d`; configs and stage
extraction used protocol commit
`ea56685ff55d2bb20207edd8429562a793ed904f`. A separate private v2
approval artifact (SHA-256
`5a894865c4a8e3a1ffc556ec5efa84d71dbcac94e57e48d6b1b8cb957685779f`)
bound this exact manifest and $15 ceiling. The unused v1 approval was not
reused. The [committed redacted audit](m66-pilot-v2-postcheck.json) has SHA-256
`3ec2c64b965a863873c173c43cd07bfe9c98931fcbf10896a5e4e025b8ec5e87`;
full traces, response artifacts, journal, and stage outputs remain private.
The complete private postcheck JSON has SHA-256
`fbd9eca1cae27774c1c5493b3845fbdfba238316186471f71971cddfc2304f45`.

## Interpretation and next boundary

V2 demonstrates that the corrected Range B bootstrap reaches model work and
that the corrected stage extractor handles all six completed, score-valid
cells. It does not complete the planned patched Range B pair. The six valid
cells are descriptive pilot observations only; no eight-cell policy contrast,
paired estimate, or v1/v2 pooled estimate is reported. V2 remains closed as
observed. Any new provider-attrition rule or further paid feasibility study
requires a separate predeclared protocol, fresh excluded cells, and new
authorization. The confirmatory M6.6 sample has not begun.
