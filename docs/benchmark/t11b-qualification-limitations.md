# T1.1b interpretation and limits

- Scope: A1 scoped audit, `tenant_boundary_v2`, seed 42, vulnerable variant only.
- Active network evidence is a positive local control plus a negative probe from the target namespace; it does not enumerate all destinations.
- State independence uses a trusted test-only SQLite marker. No vulnerable business route or target record is changed.
- Gateway evidence comes from a fresh execution of PR #76's harmless `/api/me` and denied-request test, with the test-file digest and source commit recorded privately.
- The public report omits random container IDs, instance secrets, raw artifacts, and unrelated Docker resources. Private evidence is in `.offsecgym/benchmark/t11b`.
- Q3–Q6 remain `not_evaluated`. Q2 alone never makes A1 benchmark ready.
