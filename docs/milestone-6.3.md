# M6.3.0: dependency and budget-aware worker scheduling

## Frozen protocol before the live smoke

M6.2.3 remains frozen at `81516b5`. Its two traces are not rerun. This is a
single M6.3 mechanics smoke, not a sequential-versus-parallel effect estimate.
The implementation baseline is `95fd3fe`.
The experiment file is
`experiments/configs/kimi-k3-m63-elastic-sequential.yaml`. It retains the
M6.2.3 vulnerable range, seed, bootstrap, Kimi K3 high reasoning, OpenRouter
Moonshot upstream, known routes, validator, global 120,000-token/20-call
worker budget, and sequential execution. The change is scheduling and budget
allocation. No model prompt or task contract is tuned for this smoke.

The fixed priority is identity, invoice, refund, public, document, ticket.
Bootstrap IDs make identity, invoice, public, document, and ticket READY at
start. Refund becomes READY after invoice worker completion. This ordering
tests a dependency while funding public and refund before lower-priority
tasks. It does not claim the invoice worker's evidence is true. A failed
invoice worker blocks refund. Each decision records all task states, ready
tasks, selected task, reason codes, free resources, and a state hash.

| Kind | Protected minimum tokens | Minimum calls | Maximum tokens | Maximum calls |
| --- | ---: | ---: | ---: | ---: |
| Identity | 20,000 | 1 | 40,000 | 2 |
| Document, invoice, ticket, public | 29,000 | 2 | 55,000 | 4 |
| Refund | 40,000 | 3 | 65,000 | 5 |

The protected minimum is a preflight allowance, not predicted actual use.
The controller can extend a running task's token/call lease atomically up to
its cap if shared capacity remains. It releases unused capacity on finish or
expired-lease recovery. The worker's local cap is its task maximum, while
PostgreSQL enforces the current grant. Grants are serialized by the run row
lock. The live task budget has no dollar cap, matching M6.2.3.

## Predeclared smoke gates

1. No READY task ends after one model call solely because its initial
   protected tranche is too small. A task that completes its objective in
   one call is not a failure of this gate.
2. At termination, less than 25% of global worker tokens remain unused if
   any eligible, unresolved task can still receive its minimum trajectory.
   If none can, report the unused balance and the denied minimums exactly.
3. Public and refund each receive their declared protected minimum if their
   dependencies are satisfied and the pool has that minimum when selected.
4. At least four of five non-identity objectives reach their matching route.
5. Every grant, extension, and release replays into the final PostgreSQL
   account limits and usage; no worker exceeds its grant. No duplicate task
   grant and no global budget oversubscription occur.

Report validated root-cause recall and false findings without a score gate.
If a gate fails, freeze that observation and diagnose it before any comparison.
One live smoke follows fake-worker concurrency tests; no automatic rerun.
