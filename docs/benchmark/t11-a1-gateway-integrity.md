# T1.1 A1 gateway integrity: partial evidence

The real-Docker test [`test_benchmark_t11_gateway_integrity.py`](../../tests/ranges/test_benchmark_t11_gateway_integrity.py) exercised the audited gateway against a fresh scoped A1 synthetic range, vulnerable variant, seed 42. It made only a harmless `GET /api/me` request and deliberately rejected synthetic requests. No document was selected or read, no finding was submitted, and no model or provider was called.

| Check | Observation |
| --- | --- |
| Gateway permitted request | `GET /api/me` completed with HTTP 200 through the gateway. |
| Event and artifact provenance | Request, completion event, request artifact, and response evidence agreed on run, action, instance, generation, and response digest. Restricted artifacts had mode 0600. |
| Forbidden route and destination | `/api/login` and a synthetic unapproved destination were blocked before target dispatch. |
| Identity, run, and generation binding | Invalid identity, wrong run, and stale generation were blocked without target dispatch or valid evidence. |
| Budget and duplicate action | Action and HTTP caps were enforced; a repeated action ID was rejected. |
| Teardown | The range was destroyed. A separate Docker inventory found no remaining range containers or networks. |

The test uses one identity from **controller metadata solely to test the gateway**. This does not establish that an agent could discover that identity under the intended model visibility contract. It supplies no public-only A1 reference result.

The PR #75 seed-42 runtime-prerequisite records cover lifecycle, internal-network configuration, and reset isolation for A1. This T1.1 test adds gateway checks. An isolated local canary for active packet-level egress and an application-state sibling-isolation test are still absent. Accordingly **Q2 remains not evaluated as a complete gate**. Q3–Q6, model visibility parity, patched negative controls, three-seed task success, S0–S6 qualification, and benchmark admission remain unevaluated. A1 is not benchmark-ready.
