# M6.8.3 detached reference-contract design

This milestone compares three **offline validation conditions** for complete `start_experiment` argument objects. It changes neither the autonomous controller nor the frozen M6.8.1 parser. No contract resolves objects, consults hidden fixtures, invents identifiers, drops targets, or changes any non-target field. All accepted arguments pass the unchanged `StartExperimentArgs` Pydantic schema.

| Contract | Reference language | Validation path |
|---|---|---|
| C0 historical control | Whatever the existing `StartExperimentArgs` UUID field accepts, without preprocessing. It rejects typed wrappers. Pydantic currently accepts an uppercase bare UUID; this observed control behavior is retained. | Direct schema validation of the original argument object. |
| C1 frozen typed | Exact lowercase canonical bare UUID, or `<public_entity_kind>:<lowercase-canonical-uuid>`. The pinned public kinds are `identity`, `organization`, `project`, `role_assignment`, `access_request`, `change_request`, `environment`, `job`, and `audit_event`. | The unchanged `m681_reference_syntax.parse_reference`, then the same schema. |
| C2 proposed alias | Every C1 form, plus exactly `change:<lowercase-canonical-uuid>`. It normalizes that prefix to `change_request` while preserving the UUID, source label, array length, and order. | A separate opt-in parser, then the same schema. |

The `change:` form is an **experiment-defined proposed alias**. The audited public entity list in `src/offsecgym/solver/range_surface.py` (SHA-256 `777ec93615e16f58cfdcaf70d87041722b908b91fc5397de2429db6b4d1e10f5`) names `change_request`; the existing parser deliberately omits `change`. No reviewed public source establishes `change:` as an official reference alias. The proposal was selected after observing historical model output, so retrospective replay is exploratory and cannot confirm its prospective value. No other alias is enabled. C2 rejects unknown labels, multiple identifiers, wrappers, embedded instructions, nested encoded JSON, malformed/noncanonical UUIDs, and natural-language references.

For C1 and C2, one rejected target rejects the complete array. The preprocessor copies the input, preserves every other field, and never checks the public-grounding or oracle labels. Empty arrays and duplicate valid UUIDs retain the existing schema's behavior. `m683_contract_replay.py` joins acceptance to the separately pinned M6.8.2 grounding classifications **after** validation; that join cannot influence admission.

## Source and exposure boundary

The replay verifies the exact private historical input SHA-256 `34bc9b4a9348fcf3a324ea7265a6e8d53d076e5d8e96778354bee1b008093647`, frozen C1 parser SHA-256 `f9b81420771e6e8802682aa5dd6383165847f705295f3a15883fc2dae23951d5`, private M6.8.2 classification SHA-256 `c5100b5f26a6c3be53a14963dec92a09f984130d63cce005099dcce65dc659b5`, and public M6.8.2 aggregate SHA-256 `d859370459c843d726d82831a763f99035dfd94c7152334e545067483db53779`. Private inputs must be mode `0600` in `0700` directories. The generated aggregate pins the source and test files by SHA-256 and includes no historical object UUID or model-response text.

The detached PostgreSQL qualification uses the existing `m681_detached_persistence.run_detached_script` and `ExperimentStateStore` with fabricated arguments and a disposable local `offsecgym_test` database. It does not add a store, call a provider, invoke an HTTP range, or connect the contract to live tool dispatch.
