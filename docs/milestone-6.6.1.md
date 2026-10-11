# M6.6.1: critical-state branching for Range B

**Status:** engineering and no-model qualification only. No live M6.6.1 manifest is frozen, no paid model calls are authorized, and no M6.6 trace is continued or changed.

The completed M6.6 study found almost no complete Range B witnesses. This milestone makes fresh, mechanically verified starting states for two difficult roots so a later study can locate where behavior starts to recover. Its contract is `(seed, root, checkpoint, variant) → independently built, verified environment state`, followed by the unchanged monolithic agent with its full normal budget.

The [checkpoint design](diagnostics/m661-critical-state-branching-design.md) specifies B2 and B3 states, setup isolation, receipt provenance, paired seed analysis, and the validator limitation. Both S1 “target-localized” checkpoints are omitted because the objects already exist in the fixture; revealing the target would supply the answer. No checkpoint adds a root label or instruction to the model's memory.

The no-model [qualification report](diagnostics/m661-checkpoint-qualification.json) records 24 excluded engineering seeds, deterministic fixtures and vulnerable/patched build pairing, 288 simulated seed × checkpoint × variant state-machine cases, and five real Compose checkpoint/patch cases. These are infrastructure tests, not model outcomes. A future paid study needs a separate frozen assignment manifest and exact cost approval.
