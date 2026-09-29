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
Milestone 2.5 supplies evaluation-compatible contracts; the evaluator and metric pipeline
are scheduled for Milestone 3.
