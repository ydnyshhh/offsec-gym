# M6.6 confirmatory v2: public metadata transport stop before cell 193

The sole approved continuation stopped after **192 of 280** frozen assignments.
The append-only journal SHA-256 is
`6a32da71e769868b0313cecfd5fa4c2a95b6b026704c31b93a93d4fb581fc53e`.
Cumulative estimated model-token cost is **$84.050823** under the original
$504 cap. Pair 96 has its authoritative receipt. Pair 97 has no endpoint
preflight or receipt; cell 193 never started and has no model request.

Cell 192 remains `provider_failed/provider_unavailable` and score invalid.
Earlier retained provider failures are cells 89, 90, 170, 186, 187, and 188.
Cell 162 remains score-valid `agent_failed`, with its separately recorded
offline stage exception. No stopped cell may be retried or replaced.

The collector's next operation was a public, no-key OpenRouter endpoint
metadata check before pair 97. That request ended with
`urllib.error.URLError: <urlopen error [Errno 54] Connection reset by peer>`.
The stopped collector log SHA-256 is
`4079246eb8e61d4fbb84681250332dab6150f4374e544a13460adce74237ed7f`.
This is a metadata transport failure; it is not evidence that the selected
model endpoint or price changed. The last selected endpoint remained Moonshot
AI | `moonshotai/kimi-k3-20260715` at $3/M input and $15/M output.

A read-only frozen postcheck against authoritative PostgreSQL passed for all
**192 source runs, 185 valid scores, seven retained provider failures**, trace
hashes, event identities, score replay, pair receipts, and no extra run or
active reservation. It wrote no original study artifact and made no model
call. The provider-health epoch started at order 189; its four terminals
through order 192 remain eligible under the frozen health rule. The full
historical terminal sequence still contains the earlier rate-limit pause,
so naive restart of the collector would misclassify that earlier pause as
current. A recovery must preserve the recorded order-189 epoch explicitly.

A temporary-copy rehearsal of the proposed wrapper passed the same
authoritative PostgreSQL postcheck and the collector's existing-pair replay.
The test forced a stop at the public metadata boundary before cell 193;
the temporary clearance was created, the order-189 health epoch was retained,
and neither the original journal nor any paid model request changed.

## Proposed operational recovery

The versioned wrapper is pinned to the exact 192-cell journal, cell-192 run,
pair-96 receipt, stopped log, prior rate-pause clearance, and cumulative
cost. It has a **zero-value approval hash** and is incapable of starting
paid work until a separate scope-bound approval artifact is reviewed and
pinned. No approval artifact for this recovery has been created.

After approval, the wrapper would replay all 192 PostgreSQL source runs and
the frozen pair receipts, require a fresh public selected-endpoint and price
check, then write a create-only transport clearance. The unchanged collector
would replay the stopped prefix and start only frozen assignments 193–280.
It would carry forward the existing health epoch from order 189; **it would
not reset provider health at order 193**. Any new health pause, metadata
failure, score or integrity gate, endpoint drift, or cap issue must stop
again without retry.

The proposed paid scope is only assigned cells **193–280**, once each, with
no retry or replacement, under the same **$504 cumulative** estimated-cost
cap. The source and protocol commits, model requests, prompts, range,
validator, budgets, seeds, arm order, manifest, and prior terminal outcomes
remain frozen. This is a post-start operational deviation; provider
failures remain in the intention-to-treat schedule. Any eligible-only
comparison has a conditional estimand and must report attrition by arm,
range, and variant. A successful public metadata check does not guarantee
that the next paid request will succeed.
