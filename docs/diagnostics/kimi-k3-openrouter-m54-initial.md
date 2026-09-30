# M5.4 initial three-run smoke: action index visibility gap

This is the first smoke batch under the [predeclared M5.4 gates](../milestone-5.4.md),
using `kimi-k3-structured-m54.yaml` and implementation commit `6fc31a3`.
It is preserved separately from the targeted rendering correction that follows.
The model, high reasoning, pinned Moonshot AI upstream, seeds, range, budgets,
and surface visibility match M5.3. No transcript or large-N run was added.

| Run | Run ID | Status | Calls | HTTP actions | Exact / unchanged repeats | Findings submitted / validated | Last input (peak) |
| --- | --- | --- | ---: | ---: | ---: | ---: | ---: |
| 1 | `c1eb488b-5dfe-40ba-99cf-8b3de9029bb8` | agent_failed | 7 | 33 | 0 / 0 | 3 / 3 | 8,773 (9,068) |
| 2 | `c2465841-f523-44b4-a81b-cfcdee7b5b50` | budget_exhausted | 14 | 48 | 10 / 9 | 8 / 6 | 7,825 (8,784) |
| 3 | `5d76db9b-8762-44ba-9382-22e1a75a467a` | budget_exhausted | 12 | 51 | 10 / 8 | 13 / 10 | 7,478 (10,079) |

All 33 completed model calls selected the pinned Moonshot AI upstream. The
automatic context was present exactly once per model request. Maximum carried
world-tool output was 2,296 characters, and maximum automatic context plus
world-tool output was 9,500 characters. `get_entity` was invoked 2, 3, and 1
times; `query_worldview` once in run 2. All clean long runs stayed below the
12,000-token late-input gate. No run repeated `/api/me`.

## Repeat classification

The 17 unchanged exact repeats across runs 2 and 3 were checked against the
persisted model input immediately preceding each action. An exact checked
request line with the same method, path, and identity counted as prior action
state visible. Nine repeats lacked that line: five in
run 2 and four in run 3. Eight occurred with the exact checked request visible:
four in each run. None of these repeats came from two requests generated in
the same model call. The first group is action-history eviction from rendered
context; the second is policy-side redundancy. Three other exact rereads had
changed response hashes and are reported as state verification, outside the
unchanged-repeat gate.

The controller retained up to 32 unique request fingerprints, but the
2,600-character checked-action section displayed only about ten long lines in
the run 1 context before finding submission. Two document actions were only
10–12 HTTP actions old: they remained stored but were not displayed. The
model's carried reasoning abbreviated each action ID to eight hexadecimal
characters. Its later finding calls supplied invented UUID suffixes. Those
two action IDs did not match any gateway action in the run, and the finding
sink rejected the calls. A third rejected authorization finding call repeated
one of these invalid citations, reaching the agent's three-invalid-call
threshold and producing `agent_failed`. This fails the zero-fabricated-ID
gate for tool arguments. The three findings actually submitted in run 1
validated. Run 2 also had one rejected `http_request` with the string
`"null"` instead of JSON null for `identity_id`; it is a separate malformed
tool call.

All submitted finding citations in the three runs resolved to the intended
gateway action/evidence pair and object; no cross-object association was
found among submitted findings. The run 1 invented citations were rejected
before submission, which is why this gate is reported separately from
cross-object associations.

## Gate decision and correction

The initial batch **does not freeze M5**. It clears persistent checked
identities, retrieval carryover, full-input growth, and validated-finding
gates, but misses zero fabricated typed IDs and the at-most-one
eviction-driven unchanged repeat gate (nine observed). The correction stays
within M5.4: expose more of the already retained action index by grouping
requests under identity and using compact labels for the exact action and
evidence UUIDs. Do not enlarge the 7,500-character automatic-context ceiling
or add document facts. The same gates remain in force for a new three-run
verification. Original events and model artifacts remain local and immutable.
