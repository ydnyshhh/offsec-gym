# T0 static qualification result

The no-model T0 command compiled and reverified the eight default scoped vulnerable task definitions and their fully patched siblings on seed 42. Q1 checked deterministic regeneration, build/oracle digest integrity, expected active-root isolation, patched sibling closure, pair identity, and identical paired fixtures. Q7 checked the three limited fixture/spec counts used as difficulty metadata. Q8 checked that the public envelope schema excludes private fields and root slugs. Q9 records the readiness classification derived from those results. These gates passed for A1–A5 and B1–B3.

| Archetype | Root class | Q1 | Q2–Q6 | Q7 | Q8 | Q9 | Readiness | Public-only path |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| A1 | DOC-CROSS-TENANT-READ | pass | not evaluated | pass | pass | pass | partial | unknown |
| A2 | INVOICE-CROSS-TENANT-READ | pass | not evaluated | pass | pass | pass | partial | unknown |
| A3 | TICKET-CROSS-TENANT-READ | pass | not evaluated | pass | pass | pass | partial | unknown |
| A4 | PUBLIC-INVOICE-METADATA | pass | not evaluated | pass | pass | pass | partial | unknown |
| A5 | MEMBER-REFUND | pass | not evaluated | pass | pass | pass | partial | unknown |
| B1 | B1-SOD | pass | not evaluated | pass | pass | pass | partial | unknown |
| B2 | B2-REVOKED-ROLE | pass | not evaluated | pass | pass | pass | partial | unknown |
| B3 | B3-CANCELLED-JOB | pass | not evaluated | pass | pass | pass | partial | unknown |

Q2 runtime integrity, Q3 public-information reachability, Q4 public-only end-to-end reference success, Q5 negative controls, and Q6 canonical scoring agreement were **not evaluated by this command**. Positive and negative control statuses therefore remain `not_evaluated`. No real Docker instance or gateway was used by the T0 command. Existing range tests and prior studies are context, not substitutes for a same-task public-only proof. B2 and B3 retain their known target-identification, timing, and follow-up-observation questions; neither is benchmark-ready.

The [machine-readable aggregate](t0-qualification.json) and [registry](t0-task-registry.json) contain task IDs, gate statuses, reason codes, and source SHA-256 pins. They omit fixture anchors, oracle UUIDs, reference request bodies, and model responses. `record_set_sha256` commits to the deterministic record set before that field is added.
