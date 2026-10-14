# M6.6.1 excluded feasibility result

**Status:** the frozen 96-assignment study finished on 2026-10-14. This is a descriptive feasibility result from an excluded engineering cohort. It is not a randomized estimate of checkpoint effects and does not supersede prior M6.6 studies.

## Frozen analysis and audit

The [byte-for-byte frozen analysis](m661-feasibility-v1-analysis.json) has SHA-256 `2e1d3dd9802a18eb0c14d96dc87b5b0e6c01c7e815736605c9e4f02a5784ca57`. The full read-only `research_ops/m661_cell92_recovery_postcheck.py` passed against the dedicated authoritative PostgreSQL database. It replayed the assigned outcomes, score and trace evidence, referenced run inventory, pair receipts, reservations, and the frozen analysis.

| Measure | Result |
| --- | ---: |
| Assigned rows | 96 |
| Completed source cells | 92 |
| Score-valid rows | 89 |
| Pair receipts | 48 of 48 |
| Recorded cumulative estimated model-token cost | $39.037077 |
| Conservative cost, charging both unknown outcomes at their full $1.80 ceilings | $47.752008 |
| Original cumulative estimated model-token cap | $172.80 |

Cells 1 and 4 remain infrastructure missing. Cells 13, 14, and 91 retain their recorded provider failures. Cells 86 and 92 started but have unknown outcomes; their task-owned model and action reservations remain active and were neither settled nor retried. The frozen analysis represents those two missing outcomes with `validation_failed` placeholder rows; that label is **not** a claim that either source run reached a validation failure. These seven rows are score-invalid. The two unknown outcomes and the infrastructure failures limit complete-case and resource comparisons.

The final approved continuation ran only cells 93–96 once. Its journal SHA-256 is `592f8303ecc794ff17ad3d2a485862024539e02f25244fa48d63170defd7f2bb`; its log SHA-256 is `6da35fe6906741c061fb71d02c654a5ff89672c19762b0054be090dbf192c5b2`. The collector exited successfully. Pair 46 was closed only from retained cell 91/92 events, followed by pairs 47 and 48. Prior journals, logs, and studies were preserved.

## Descriptive observations

Each checkpoint-by-variant stratum has eight assigned seeds. The frozen `full_transition` curve counted two transitions in B2-S3 vulnerable (2/8 assigned, rate 0.25; frozen missingness bounds 0.25–0.25). The other eleven strata counted zero. B2-S3 vulnerable also had four completed experiments and two offline complete chains. No stratum recorded a root-class submission or complete trusted witness. These distinct measures should not be combined into a success count.

Some zero-count strata have missing outcomes, so their all-assigned rates remain undefined and only bounds are reported. For example, the frozen full-transition bounds are 0–0.25 for B2-S0 patched and 0–0.125 for B2-S3 patched and B3-S0 patched. The [analysis artifact](m661-feasibility-v1-analysis.json) contains all twelve strata, paired seed contrasts, and the original resource fields. Missing resource totals remain missing instead of being inferred from incomplete events.

The observed transitions are descriptive within this excluded cohort. Missingness, provider failures, the small eight-seed strata, and checkpoint-specific comparability limits preclude a causal claim about either checkpoint or patch. No further paid calls, retries, replacements, or reservation settlements are part of this result.

## Frozen provenance

- Source commit: `e302b182a6987c84ac542f50c36932de249c99c7`
- Protocol commit: `d0cd222be911c72fd26eef777bd77bd6234ed383`
- Manifest SHA-256: `88cda1e9d8977644cc7d7447c44dda15c562bb9e6899feacf935537344271373`
- Assignment SHA-256: `05373c9c3f9dd9099403bee7e998f92193072618422675aa9477f7a84a90d8f2`
- Final operational and approval-pin merges: `53760ac129119c01568c75534bf7a992d0b267ca`, `0a05c6c07ce673ce05fec173b1de9602642d144e`
