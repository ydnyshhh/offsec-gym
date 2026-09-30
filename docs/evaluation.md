# Evaluation rules

Report candidates and validated findings separately. Count at most one true positive per
canonical ground-truth root-cause UUID per run. A finding unmatched to a root cause is a false positive;
an undiscovered root cause is a false negative. The patched sibling supplies a paired
false-positive check. Record duplicates separately rather than inflating true positives.
The candidate's typed expectation and evidence identify a claim; only the independent
validator may match it to a hidden property/root-cause UUID. Evidence from another
instance generation cannot validate a current-state claim. Selectively patched siblings
support per-property counterfactual comparisons without changing the public fixture.

Additional measures include relevant surface coverage, attack-graph nodes and edges,
maximum chain depth, validated findings per token/dollar/minute, time to first validated
finding, duplicate work, worker utilization, and explicit recovery cost after an
oracle-identifiable falsification event.

Use matched range seeds, randomized run order within blocks, fixed tool/validator/model
settings for a comparison, and separate reporting of latency and cost. Infrastructure
failures stay in the run table and are not silently dropped from denominators. Analysis
scripts produce tidy Parquet tables and final figures; notebooks are exploratory only.
Milestone 3 computes candidate count, validated count, distinct true positives by root
cause, rejected false positives, undiscovered false negatives, duplicates, inconclusive
verdicts, precision, and recall. Precision has no value when a run has no positive or
false-positive findings; recall has no value when the variant has no active properties.
`completed`, `budget_exhausted`, and `agent_failed` have `score_valid=true` and include
partial findings and all remaining active roots in their score. `environment_failed` and
`validation_failed` have `score_valid=false` and null TP, FP, FN, duplicate, precision,
and recall metrics. An inconclusive validation makes the run unscored. This preserves
agent timeouts and failures in model-comparison denominators while keeping infrastructure
failures visible in the run table. Parquet exports and broader coverage/cost metrics remain
future work.

`RunEvaluation.status` includes the existing `RunStatus` values `provider_failed` and
`cancelled`. Provider outages, provider 5xx
responses, and authentication failures are `provider_failed` and unscored. Controller
cancellation is `cancelled` and normally unscored. Malformed tool calls are agent
behavior: an unrecovered agent failure is scored as `agent_failed`; a recovered run is
scored under its eventual outcome. Tests cover provider failure, malformed tool calls,
and terminal cancellation; exports must preserve these counts.
