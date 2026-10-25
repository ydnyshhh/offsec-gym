# R0 passive provenance audit

`audit_retained_observation` accepts already recorded synthetic `RequestArtifact`, `Evidence`, and action events. It validates task and visibility bindings, a visible GET route, run/instance/generation/action identity, request/evidence pairing, request path and response hashes, HTTP status, unique ordered events, a complete JSON response, and the claimed top-level field/value. Candidate reference types are document, support ticket, and invoice.

The audit issues no gateway calls and generates no target IDs. A valid field claim establishes only record integrity and provenance under the supplied contract. It does not qualify A3 Q3 or change Q4–Q6. Tests reject wrong bindings, fields, values, digests, event counts, and generations.
