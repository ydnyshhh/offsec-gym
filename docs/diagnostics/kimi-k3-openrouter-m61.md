# Kimi K3 OpenRouter M6.1 sequential-worker diagnostic

## Protocol

This is a six-run, vulnerable-range diagnostic of the **current-code M5.4
monolithic structured policy** (C) against the **M6.1 deterministic sequential
workers** (W). It is not a comparison with the historical M5.4 executable or
its frozen traces at `0f27533`. The [control](../../experiments/configs/kimi-k3-structured-m61-control.yaml)
and [worker](../../experiments/configs/kimi-k3-structured-m61-workers.yaml)
specs differ only in experiment name and `orchestrator`. Runs alternated C/W
three times. Every run used the same M6.0 atomic PostgreSQL controller, range
build `c435a866-da5a-5f65-a2b5-ba2e4c2cc4db`, vulnerable seed 42, known
routes, deterministic validator, `moonshotai/kimi-k3`, high reasoning,
OpenRouter with Moonshot AI pinned and fallback disabled, and global limits of
60 actions, 60 HTTP requests, 20 model calls, 120,000 tokens, 8,192 output
tokens per call, and 600 seconds. The coordinator made zero model calls.

The token preflight calibration was fixed before this batch: it reserves
`ceil(serialized UTF-8 request bytes / 2) + 1,024` input tokens plus the
output cap. Model events separately retain reserved and actual input/output
tokens and their signed errors. This is a prospective stopping rule; it can
stop before all 120,000 tokens are actually spent. Each worker also receives a
deterministic local budget slice, while the PostgreSQL reservation enforces the
global limit. A worker can exceed its local slice on its final model response.

All 69 completed model calls selected the pinned Moonshot AI upstream with
high reasoning requested. Across those calls, no actual input exceeded the
preflight input estimate; the smallest input overreserve was 3,601 tokens and
the largest 6,042. There was no observed global token oversubscription or
unsettled reservation. Model prices were absent from the frozen specs, so
**cost is unavailable**; token counts are the compute measure. An earlier CLI
summary displayed a misleading zero cost for this case; the reporting fix
now emits `null`.

## Six scored traces

`Roots` is distinct validated root causes out of five. `TP`, `FP`, and `dup`
are evaluator counts and should not be conflated with exact HTTP repeats.
`First valid` is elapsed time from run start to the earliest submitted finding
that subsequently validated. The worker statuses are `completed`; the
monolithic controls stopped at preflight budget exhaustion. All six have
`score_valid=true`.

| Arm | Run ID | Roots | TP / FP / dup | Findings submitted / validated | Model calls | HTTP dispatches | Exact repeats | Input + output tokens | First valid |
| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| C1 | `d75efb02-5291-48e0-9b9f-a086760e7ca3` | 4/5 | 4 / 1 / 3 | 8 / 7 | 12 | 42 | 3 | 87,850 + 13,097 = 100,947 | 337 s |
| W1 | `fbae1d94-abf4-4939-8a5a-617ac17e53c9` | 2/5 | 2 / 3 / 1 | 6 / 3 | 11 | 25 | 1 | 97,964 + 10,668 = 108,632 | 179 s |
| C2 | `c4669f05-e2b0-4518-8610-9f927ca27112` | 4/5 | 4 / 2 / 7 | 13 / 11 | 12 | 41 | 2 | 86,346 + 16,548 = 102,894 | 364 s |
| W2 | `af876325-acaa-4981-aef5-91cb24354258` | 1/5 | 1 / 0 / 1 | 2 / 2 | 12 | 26 | 0 | 98,353 + 7,183 = 105,536 | 273 s |
| C3 | `884bb05a-2d28-460f-8496-d4a5a1cd921c` | 3/5 | 3 / 4 / 5 | 12 / 8 | 11 | 38 | 1 | 79,633 + 16,963 = 96,596 | 245 s |
| W3 | `53d3f86e-9520-4a22-a89d-b86b1f77bcdd` | 1/5 | 1 / 0 / 0 | 1 / 1 | 11 | 34 | 0 | 90,240 + 8,644 = 98,884 | 165 s |

Controls found document, invoice, and public-exposure roots in all three
runs, plus the ticket root in C1 and C2. W1 found document and invoice roots;
W2 found public exposure; W3 found documents. Neither arm found the refund
root. These observations are useful for tracing lost work, but three pairs
cannot support a statistical claim about orchestration quality. C1 ran at
`aed7a45`, before worker-only packet trimming and slice fixes; the later
controls and final workers ran at `5b75867`. The controller and monolithic
policy path did not change between those commits, but this is not a claim that
all six traces came from a bit-identical executable.

## Orchestration and trace inspection

Each final worker run recorded six bounded packets, six debriefs, six finished
workers, and no remaining worker slots, active action reservations, coverage
leases, or model-token reservations. Replaying controller events reproduced
the persisted action, HTTP, model, token, reservation, and worker counters for
all six runs. The largest packet in W1/W2/W3 was 9,744/9,953/9,857 serialized
characters, within the 10,000-character contract. Model-call allocation
across the six workers was `[2,3,2,2,1,1]`, `[4,2,2,2,2,0]`, and
`[4,2,2,2,1,0]`, respectively. The late refund worker got zero model calls
in W2 and W3 despite its local slice because the shared global preflight
could not reserve another full prospective call. In W1, the public and refund
workers each got one call but issued no HTTP request. The final worker tasks
therefore received less useful exploration than the fixed decomposition
suggests.

Worker packets omitted prior checked actions, evidence, entities, and entity
details to remain bounded. Summed over six packets, omissions were
`14/4/10/8` in W1, `14/4/9/1` in W2, and `33/8/8/4` in W3, in that order.
These are handoff compression counts, not proof that each omitted item was
needed. Peak model input per call was 11,200/11,283/11,831 tokens for W1/W2/W3,
versus 8,826/9,909/9,408 for C1/C2/C3. The worker handoff did not lower
per-call input size in these traces; it added packet/context overhead while
the six workers collectively used similar or more model tokens.

Exact HTTP repeats were 6/121 dispatches across controls and 1/85 across
workers. The single worker repeat was an unchanged cross-worker `GET /api/me`.
Its exact fingerprint was already in the receiving worker's packet, so this
was a decision to reread visible prior work rather than a missing memory
entry. The controls had three unchanged and three response-changing exact
repeats. One additional C1 refund POST revisited the same route and identity
with a corrected body after a 400 response; that was a corrective retry, not
an exact duplicate. We did not exhaustively classify semantic equivalence
across different assets or identities. The cross-worker duplication metric is
not applicable to monolithic controls and is now reported as `null` there.

Cross-worker evidence appeared in 4/6 W1 submitted findings, 0/2 W2, and
1/1 W3; among validated findings the corresponding counts were 3/3, 0/2,
and 1/1. This demonstrates attributable handoff through shared state, but
does not establish that the reused evidence improved scores. Workers created
three active coverage claims per run and completed none; the coordinator
released all three on exit. This is an abandoned/unclosed coverage proxy,
not proof that all three objectives were fully abandoned. No run submitted
a hypothesis fact or produced a contradicted-fact adjudication, so recovery
after a falsified hypothesis was **unobserved**. Rejected findings were mostly
object-authorization property mismatches (three W1; one C2; three C3);
controls also had proof-missing workflow submissions (one in each C run).
Coordinator model overhead was exactly zero calls, though packet construction
and debrief time are not yet separately timed.

## Excluded implementation diagnostics

Two earlier worker traces helped repair implementation defects and are not
part of the six matched scored traces:

- `5ef8bf41-468a-4b8a-9f7d-266712351a0d`: the fifth packet exceeded
  10,000 characters and raised a Pydantic validation error. The runner
  classified it `agent_failed` with `score_valid=true`, an infrastructure
  misclassification. Packet trimming and exception classification were fixed
  in `3a38eb3`. This trace had only four packets/debriefs.
- `338eb790-ffc1-47a8-a017-dd78fefa447e`: packet bounds held, but early
  workers consumed the global token budget and later public/refund workers
  got zero calls. Fixed deterministic local slices were added in `5b75867`.
  The final runs show that local slices alone still do not guarantee useful
  turns for late workers under a conservative global prospective preflight.

## Decision and limits

M6.1 plumbing passes its fake-provider, Docker, and event-replay checks. The
live traces show earlier first valid findings and fewer exact repeats, but
less root-cause recall, large packet/input overhead, and late objectives with
little or no exploration. **Do not infer that sequential workers outperform
the monolithic control or proceed directly to M6.2 parallelism from these
data.** Inspect and address the late-worker budget/trajectory behavior first,
with a predeclared follow-up that keeps the current six traces intact.

The restricted request/response artifacts and full event streams are local
under `.offsecgym/`. A local PostgreSQL dump is preserved at
`.offsecgym/diagnostics/m61-postgres.dump` (SHA-256
`176d298b21b93373b2dae8a82b7359af424500fe9b36a8ef743492d72a246031`).
Neither the dump nor raw model turns are committed. M6.2 also needs source
action sequence ordering for concurrent response facts, a cross-process range
reset/dispatch barrier, and stale-reservation recovery before live parallel
workers can be interpreted safely.
