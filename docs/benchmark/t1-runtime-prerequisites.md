# T1 benign runtime prerequisites

The checker starts two disposable synthetic range instances for **one** scoped task definition. It checks source and build integrity, healthy startup, distinct instance identities, internal Docker networks, absence of published ports, reset isolation, and teardown. It performs no task action, gateway request, reference policy, verifier invocation, or model call.

Run one local check with Docker available:

```sh
PYTHONPATH=src python research_ops/benchmark_t1_runtime_prerequisites.py \
  --archetype A1 --variant vulnerable --seed 42 \
  --output /tmp/t1-runtime-a1-42.json
```

The `status` field describes only these prerequisites. `q2_gate` remains `not_evaluated`: task-specific gateway route policy and active external-egress verification are still missing. Q3–Q6, public-only solvability, and benchmark admission remain unevaluated. A failed check exits nonzero and retains a sanitized failure class; the checker always attempts to destroy the instances it created.

## Seed 42 observation

The [sanitized records](t1-runtime-prerequisites-seed42.json) report prerequisite passes for A1–A5 and B1–B3, vulnerable variant, seed 42. Each run used two disposable Docker instances and returned teardown success. After the matrix, a separate Docker inventory found no remaining range containers or networks. These observations do not qualify any task: Q2–Q6 remain unevaluated and there is no public-only task completion evidence. No patched variant, canonical unguided task, or second seed was exercised by this matrix.

This is a separate T1 evidence record. It does not mutate T0 task IDs, static qualification records, difficulty metadata, or stage results.
