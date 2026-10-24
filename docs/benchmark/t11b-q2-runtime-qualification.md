# T1.1b: A1 runtime integrity qualification

This milestone evaluates Q2 for the scoped A1 `tenant_boundary_v2` task, vulnerable variant, seed 42. It runs without a model or provider call. The versioned mandatory check inventory and fail-closed aggregation are in `q2_runtime_qualification.py`. The canonical machine-readable result is `t11b-q2-runtime-qualification.json`.

The runner first reruns the T1 runtime prerequisite checker for the exact A1 manifest. It then starts two disposable instances from the same build, checks the actual Docker attachment inventory, runs a controlled local canary probe, inserts a trusted SQLite marker into one instance, resets that instance, and checks both instances' logical storage. It reruns the T1.1 gateway integrity test against the same source revision. All created resources are checked for removal.

Run `research_ops/benchmark_t11b_q2_qualify.py --archetype A1 --seed 42 --dry-run` to inspect scope without Docker. Execute with `--execute --output-private .offsecgym/benchmark/t11b`; raw evidence stays in the ignored private directory with restricted permissions. The tracked JSON contains only scoped statuses, source pins, and evidence digests.

Q2 is an infrastructure admission result. It does not establish public discoverability, reference task success, negative security controls, scoring agreement, or full benchmark readiness. Q3–Q6 remain unevaluated. The other seven scoped tasks and two unguided tasks retain their prior status.
