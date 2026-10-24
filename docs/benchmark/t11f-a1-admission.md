# T1.1f A1 development-seed admission

The prespecified development cohort is seeds 42, 43, and 44, each with vulnerable and patched scoped A1 siblings. These are qualification instances, not a held-out evaluation set. Every cell receives its own fresh real-Docker Q2 check and public-only reference run. The patched sibling is a negative control: Q4 vulnerability success is `not_applicable`, and a separate `patched_control_status` records its denial result.

`benchmark_t11f_admission.py` regenerates Q1, Q7, and Q8 static checks for each exact task definition, reads the six Q2 records, six reference records, and source-bound Q5/Q6 conformance results, then derives Q9 under `t11f-a1-admission-v2`. It does not accept a caller-supplied readiness label. Missing, mismatched, or incomplete mandatory evidence prevents admission. `t11f-a1-admission.json` is the machine-readable decision and `t11-postcheck.json` checks report integrity, task bindings, source pins, cleanup receipts, and private output modes.

Q5/Q6 are shared source-bound validator and scoring controls; their per-cell columns are inherited under the unchanged implementation, not six independent conformance experiments. Q2 is measured separately for every seed and variant. Q3 is repeated inside every reference run. A pass indicates a reproducible no-model reference path in this synthetic development cohort; it does not measure model success or empirical difficulty. The seven other scoped archetypes and two canonical unguided tasks remain outside this A1 admission decision.

## Observed qualification

| Seed | Variant | Q2 | Q3 | Q4 vulnerability success | Patched control | Q5 | Q6 | Cleanup |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| 42 | Vulnerable | Pass | Pass | Pass | Not applicable | Pass | Pass | Verified |
| 42 | Patched | Pass | Pass | Not applicable | Pass | Pass | Pass | Verified |
| 43 | Vulnerable | Pass | Pass | Pass | Not applicable | Pass | Pass | Verified |
| 43 | Patched | Pass | Pass | Not applicable | Pass | Pass | Pass | Verified |
| 44 | Vulnerable | Pass | Pass | Pass | Not applicable | Pass | Pass | Verified |
| 44 | Patched | Pass | Pass | Not applicable | Pass | Pass | Pass | Verified |

Q1, Q7, and Q8 also passed in all six cells under their T0 scope. The derived Q9 decision is `qualified`, with no A1 gate blockers. All vulnerable cells have S0–S6 true. Patched cells have S0–S1 true and S2–S6 unknown, as a denied read is not a vulnerability proof. The postcheck passed all binding, digest, cleanup, and private-output checks. The source commit on Q2 and SHA maps on all reports bind these results to the checked implementation; ephemeral Docker identifiers and exact private target IDs are omitted from public reports.
