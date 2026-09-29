# ADR 0002: Generation-bound range and oracle contracts

Status: accepted for Milestone 2.5.

The compiler emits versioned, validated ground-truth and attack-graph manifests. Canonical
property, root-cause, node, and edge IDs are UUIDv5 values derived from scenario, seed, and
semantic slug, so paired variants refer to the same concepts. A discriminated expectation
union models authorization, field exposure, and state transitions. Solver findings contain
claims and evidence but no hidden oracle IDs. The graph models a boundary test separately
from successful exploitation; the test stays available in patched variants while the
violation edge becomes inactive.

Build and instance IDs have separate APIs. Every instance has a generation that changes
when reset discards target state. Action request events, request artifacts, evidence, and
controller metadata carry the instance ID and generation. A local per-instance guard
serializes reset and dispatch. Build and hidden-oracle files have SHA-256 digests checked
before reuse and operation. The integrity boundary assumes a trusted local state root.

SaaS variants can patch individual properties while preserving a common fixture and pair
ID. The default agent-visible range context omits the controller's identity roster.
`WorldFact` has a typed bounded value and source provenance, while storage and adjudication
remain for a later milestone. The gateway's run-wide lock and full event-history scan must
be replaced before multi-agent execution; this ADR does not claim distributed controller
safety.

Generated v1 build and instance artifacts are incompatible. Stop and destroy live v1
instances with the older version, then rebuild under a fresh state directory. Existing
v1 input examples and legacy trace events remain parseable where their semantics are
unambiguous; legacy events cannot supply generation-aware validation provenance.

Milestone 2.5.1 makes the range-start event and validation context explicit about build,
instance, and generation. The oracle store resolves ground truth by verified build ID;
arbitrary oracle paths are excluded. Ground-truth proof requirements are semantic typed
checks rather than SaaS-specific strings. A named compiler identity version participates
in the build ID and must change whenever target, oracle, or graph semantics can change.
Old generated SaaS oracles use an unsupported schema and require rebuilding. Gateway
preflight rejects invalid instance contexts before writing artifacts, and build-integrity
failures propagate as environment failures rather than ordinary agent action failures.
