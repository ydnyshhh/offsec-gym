# Range runtime and gateway

The first range is a stateless `hello/health_check` service. The compiler accepts only that
scenario and rejects unsupported topology, identities, patches, and vulnerability entries.
It hashes the canonical `RangeSpec` and template bytes into a deterministic build ID, then
writes a self-contained Compose bundle under `.offsecgym/builds/`. Rebuilding the same input
returns the same bundle. A build contains no run credentials or instance identifiers.
The base image is pinned to a multi-platform digest; the built image ID is recorded per instance.

The runtime has separate `build(spec)`, `create_instance(build_id)`, `start_instance(id)`,
`instance_status(id)`, `reset_instance(id)`, `stop_instance(id)`, and `destroy_instance(id)`
operations. The CLI's `range start SPEC` builds, creates, and starts as a convenience;
`range start INSTANCE_ID` resumes a stopped instance. `range inspect-build BUILD_ID`
verifies a build without treating it as an instance. The instance manifest lives under
`.offsecgym/instances/` and records the build ID, project name, nonce, state, generation,
and built image ID. Generation starts at zero, survives stop/start, and increments after
reset discards target state, even if restart fails. `destroy` retains a tombstone.
Override the state root with `OFFSECGYM_STATE_DIR`.

Build manifests list SHA-256 digests for every generated bundle file and, for SaaS,
separate digests for hidden oracle files. Reuse, instance creation, startup, and artifact
reads verify them and reject missing, changed, extra, or symlinked files. The Docker image
ID recorded on the instance is distinct from the deterministic source build ID. Integrity
checks detect accidental or manual changes within a trusted local controller; the state
root is not a defense against an attacker who can rewrite both files and manifests.

Both `hello` and the trusted HTTP worker attach only to the project's `internal: true`
network. Neither publishes ports. Containers run as a non-root UID, with a read-only root
filesystem, all Linux capabilities dropped, no new privileges, and bounded resources.
The controller has Docker access; agents do not. The controller invokes a fixed Python
worker command using `docker compose exec -T` and passes a typed request over stdin. The
worker can contact only the `hello` service and never follows redirects. No model-provided
text becomes a shell command.

The gateway checks run/instance IDs, generation, service, method, path, action budget,
and rate limit. It saves a restricted-permission request artifact for **every** attempted
action, including blocked actions, with credential-like body keys and query parameters
redacted. `ActionRequested` v2 contains the artifact ID, worker ID, instance ID, and
generation plus raw path/body hashes. A completed response gets a separate evidence file
that references the request artifact and records the same instance/generation. Neither
response evidence nor events copy the raw request body. A crash or cancellation can leave
an outcome-unknown request for later reconciliation. PostgreSQL remains the event store;
the lifecycle CLI does not create an experiment run.

The gateway and lifecycle operations share an in-process per-instance guard. Reset cannot
relabel an action that is already being dispatched. One run-wide gateway lock and a full
event-history read per action still serialize actions and make budget accounting O(N²)
over a long run. PostgreSQL-backed reservations and projections are required before
multi-agent concurrency; distributed controllers are not supported yet.

`range start` waits for the target health check. A failed start tears down the attempted
Compose project and leaves a stopped manifest. `stop` preserves containers; `destroy`
removes the project's containers, network, and volumes but never deletes a shared image or
another project's resources. Reset destroys and recreates only the same instance project.

Tests cover deterministic builds and artifact integrity, generation across lifecycle
operations, gateway provenance and redaction, redirect handling, external egress denial,
cross-instance separation, and teardown ownership. The hello range has no accounts, so it
generates a unique instance nonce but no credentials. The authenticated SaaS range is
documented in [saas-range.md](saas-range.md).
