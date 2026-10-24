# T1.1d A1 independent scoring replay

`a1_scoring.py` rebuilds candidate, validated, rejected, inconclusive, duplicate, and distinct-root counts from authoritative `FindingSubmitted` and `FindingValidated` events. It checks finding/verdict identity and run lineage, maps accepted verdicts to active oracle roots, and compares the resulting score with `evaluate_run()`. The replay does not use a reference-policy success flag or copy the validator's vulnerability predicate.

The control includes one accepted root, a duplicate accepted submission, and a rejected candidate. The duplicate cannot raise distinct true positives. The fully patched oracle has zero active roots, so recall is undefined. Infrastructure failure is unscored. `t11d-score-replay.json` records the source-bound check results; private output retains the controlled events and canonical score.

Observed result: all five score checks passed, including event replay agreement, duplicate deduplication, rejected-candidate accounting, patched recall, and unscored infrastructure behavior.
