# Milestone 2.5 contract-hardening design

## Current issues

The SaaS oracle and attack graph are unvalidated dictionaries with string property and
root-cause labels. `CandidateFinding` and `ValidationResult` use UUIDs, and the sole
`SecurityProperty` contract cannot describe field exposure or a refund state transition.
The runtime infers whether a UUID names a build or an instance, and its status mixes
`built` with container states. Reset replaces target state without an epoch. Action
events and evidence cannot identify that state generation. Build reuse checks only the
manifest, so modified Compose, fixture, and oracle files can be trusted accidentally.
The gateway stores only successful response evidence and reads the full event stream
under a run-wide lock for every action. The SaaS server itself handles one request at a
time. Controller metadata currently contains more identity information than an agent
should receive by default. `WorldFact` lacks typed values and useful provenance.

## Contract decisions

- Versioned, frozen `GroundTruthManifest` and `AttackGraphManifest` models replace raw
  dictionaries. Compiler-owned property, root-cause, node, and edge IDs use UUIDv5 from
  scenario, seed, and semantic slug. The patch mode and build ID do not enter those IDs,
  so counterfactual siblings share canonical identities. Human-readable slugs remain
  separate. Oracle and graph loaders validate referential closure.
- `SecurityExpectation` is a discriminated union of authorization, field exposure, and
  state transition contracts. Both solver claims and hidden properties use that
  vocabulary. `CandidateFinding` carries a claim and evidence, never hidden property or
  root-cause IDs. `ValidationResult` may contain matched UUID property and root-cause IDs.
- The core runtime uses `build`, `create_instance`, `start_instance`,
  `instance_status`, `reset_instance`, `stop_instance`, and `destroy_instance`.
  `BuildManifest` records build existence; `RangeInstanceStatus` has only instance
  states. CLI `range start SPEC` remains a convenience; UUID commands name instances.
- `InstanceManifest.generation` starts at zero. Reset increments it after target state
  is discarded and before restart, even if restart fails. Stop/start preserves it.
  Action events, request artifacts, response evidence, and controller metadata carry
  instance ID and generation. The gateway holds an in-process instance guard from
  provenance snapshot through dispatch and evidence write so reset cannot relabel an
  in-flight action. Cross-process controllers remain unsupported.
- Build manifests record SHA-256 digests for every required Compose bundle file and
  separate digests for hidden oracle files. Reuse and startup verify all files and the
  manifest identity, and fail on missing or altered files. The built Docker image ID
  remains distinct from the source build ID. This detects accidental or manual bundle
  modification; a fully malicious writer with access to the state root is outside the
  integrity boundary.
- `ActionRequest.json_body` accepts recursive JSON with an object root. The dispatched
  encoded body remains capped at 4096 bytes; depth and element count are bounded.
  Each attempted action, including a blocked one, gets a restricted-permission request
  artifact with sensitive keys and query parameters redacted. Events retain hashes and
  the artifact ID. Response evidence refers to the request artifact rather than copying
  raw request secrets.
- Trusted `RangeControllerMetadata` and policy-derived `AgentVisibleRangeContext` are
  separate types. The default agent view has no account roster. `AgentContext` accepts
  only the agent-visible type. `WorldFact` gains source IDs, bounded typed values,
  confidence, timestamps, contradiction, and supersession links; world-state storage
  and adjudication are deferred.
- The SaaS compiler derives an internal per-property patch set. Existing `patched:
  true` means all five properties patched; a selective set can patch one property while
  preserving the common pair ID and fixture. Graphs retain active boundary-test edges
  in both variants; successful violation edges are inactive in patched variants.
- The SaaS target uses `ThreadingHTTPServer`, short-lived SQLite connections, and
  synchronized session state. Refund updates are conditional and atomic. The gateway's
  run-wide lock remains for Milestone 3 and is documented as a concurrency limit.

## Migration and compatibility

Generated build, instance, oracle, graph, and evidence formats change version. Existing
`.offsecgym` artifacts must be retired and rebuilt; loaders reject old manifest versions
with a clear message rather than interpreting them as the new shape. Operators should
stop/destroy any old live instance before upgrading, then use a new state directory or
remove only their generated inactive artifacts. The existing vulnerable and all-patched
YAML examples remain valid. An optional selective-patch input is additive. The CLI keeps
`range start SPEC`, but `range status` accepts only an instance ID and build inspection
gets a distinct command. PostgreSQL stores event payloads in JSONB; additive event
versioning needs no relational migration. Old events remain readable as legacy records
without generation provenance and cannot satisfy generation-aware validation.

## Verification

Contract tests use actual generated SaaS manifests to construct compatible findings and
validation results, and verify all oracle/graph references over seeds 0, 1, 2, 42,
1000, and 2147483647. Integrity tests mutate fixture, Compose, and oracle files and
assert reuse/start failure. Lifecycle tests distinguish build from instance and track
generation across stop/start and reset. Gateway tests inspect allowed and blocked request
artifacts, redaction, nested JSON, evidence provenance, and old-generation evidence.
Docker tests retain the five vulnerable/patched proofs, add a document-only patch case,
concurrent target requests, and the existing containment checks. PostgreSQL and hosted
CI run the complete suite.

## Deferred work

Milestone 3 implements the scripted solver, deterministic validator, replay, and
evaluator. This refactor supplies their contracts but no production verdict logic.
Before multi-agent experiments, replace the gateway's O(N²) event-history accounting
and run-wide action serialization with PostgreSQL-backed atomic budget reservations and
projections. Distributed controller coordination, crash reconciliation, a general
artifact manager, complete run manifests, and WorldState storage/retrieval also remain
future work.
