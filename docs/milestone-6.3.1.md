# M6.3.1: opportunity-aware admission and evidence-based dependencies

## Frozen comparison boundary

M6.3.0 remains frozen at `be51ab6`, including its failed public-funding and
objective-route gates. Its single live trace is not rerun. M6.3.1 changes the
coordinator's admission policy and controller accounting. It keeps the same
worker goals, packets, prompts, route contracts, model, provider, range,
visibility, validator, bootstrap, global budget, and sequential execution.
The implementation baseline is `f00e966`.
The single-smoke file is
`experiments/configs/kimi-k3-m631-admitted-sequential.yaml`.

The M6.3.0 minima and maxima remain unchanged: identity 20,000/40,000
tokens; document, invoice, ticket, and public 29,000/55,000; refund
40,000/65,000. Grants below these minima are forbidden. Worker-local maxima
and the 120,000-token, 20-call global worker pool are unchanged.

## Scheduling contract

The coordinator first evaluates trusted world and action evidence. The
identity objective is satisfied when each visible identity has a
controller-observed role from a successful gateway action and every member
or workspace admin has a workspace-membership fact. The bootstrap marker
and bootstrap action origins make a complete initial map independently
auditable. Refund becomes ready only when bootstrap identified invoice
targets and a successful invoice-detail objective action produced an
observed invoice status for a known target. Worker exit status alone never
satisfies that dependency: `budget_exhausted` may still carry sufficient
evidence, while `completed` may carry none.

At each quiescent scheduling point, the controller calculates free tokens,
calls, actions, and HTTP requests after actual usage and existing worker
accounts. The coordinator enumerates the ready-task subsets and picks the
highest fixed, logged utility sum that fits all four minimum dimensions.
When invoice is admitted and refund is pending, it also protects a
40,000-token refund forecast. The fixed utility values are refund 240,
invoice 220, public 140, identity 100, document 90, and ticket 80. These
are deterministic scheduling weights, not estimates of exploit success.

PostgreSQL holds protect every admitted task's minimum. Activation
atomically converts one ready hold into a worker grant. A worker extension
can take only unheld slack. On each worker finish, state predicates and the
admission set are recalculated; obsolete holds are released and new holds
are acquired in one run-locked transaction. `TaskStateEvaluated`,
`AdmissionDecision`, `TaskBudgetHeld`, `TaskBudgetHoldActivated`, and
`TaskBudgetHoldReleased` make those decisions replayable with the existing
grant, extension, and release events.

## Predeclared single-smoke gates

1. No worker extension consumes another admitted task's protected minimum;
   no global budget or worker grant is exceeded.
2. Identity receives no worker when the bootstrap identity predicate is
   satisfied. Refund readiness follows the explicit invoice evidence
   predicate, regardless of invoice worker exit status.
3. All admission holds, grants, extensions, activations, and releases replay
   into final PostgreSQL balances. No active hold or reservation remains at
   termination.
4. Every task admitted with a minimum trajectory receives its grant. If
   the scheduler records four fundable non-identity tasks across replans,
   all four must launch with their declared minimums.
5. Report `AdmissibleUnusedTokens` and `OpportunityDisplacement` from the
   trace. The first is final uncommitted tokens when a final READY task's
   minimum fits. The second sums extension tokens that cross a previously
   fundable, unadmitted READY task's minimum threshold. Zero displacement
   is the intended accounting result.

Validated root-cause recall, false findings, HTTP actions, model tokens,
worker routes, and latency remain observational. There is no score gate.
Run one live vulnerable smoke after unit, concurrent-controller, and fake
range tests pass. Freeze its observed gates and trace without tuning Kimi or
rerunning the same protocol.

## Frozen outcome

The single live run and gate audit are recorded in
[the M6.3.1 diagnostic](diagnostics/kimi-k3-openrouter-m631.md). All five
scheduler gates passed. The run itself ended `agent_failed`; refund reached
readiness through invoice evidence but its worker did not execute the refund
route. Ticket remained unfunded because its 29,000-token minimum exceeded
the final 20,182-token balance. These observations are frozen without a
rerun or worker-policy change.
