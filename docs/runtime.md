# Milestone 1 range runtime

The first range is a stateless `hello/health_check` service. The compiler accepts only that
scenario and rejects unsupported topology, identities, patches, and vulnerability entries.
It hashes the canonical `RangeSpec` and template bytes into a deterministic build ID, then
writes a self-contained Compose bundle under `.offsecgym/builds/`. Rebuilding the same input
returns the same bundle. A build contains no run credentials or instance identifiers.
The base image is pinned to a multi-platform digest; the built image ID is recorded per instance.

`range start SPEC` creates a new UUID instance and Compose project. `range start ID`
resumes a stopped instance. The instance manifest lives under `.offsecgym/instances/` and
records the build ID, project name, nonce, state, and built image ID. `status`, `stop`,
`reset`, and `destroy` use that manifest; `destroy` retains a tombstone so repeating it is
safe. Override the state root with `OFFSECGYM_STATE_DIR`.

Both `hello` and the trusted HTTP worker attach only to the project's `internal: true`
network. Neither publishes ports. Containers run as a non-root UID, with a read-only root
filesystem, all Linux capabilities dropped, no new privileges, and bounded resources.
The controller has Docker access; agents do not. The controller invokes a fixed Python
worker command using `docker compose exec -T` and passes a typed request over stdin. The
worker can contact only the `hello` service and never follows redirects. No model-provided
text becomes a shell command.

The gateway checks run/instance IDs, service, method, path, action budget, and rate limit.
It appends `ActionRequested` before dispatch and one blocked, completed, or failed event
after normal execution. A crash or cancellation can leave an outcome-unknown request for
later reconciliation. A successful response is capped and saved in an instance-scoped evidence
file. The event store is PostgreSQL; the range lifecycle CLI itself is a developer operation
and does not create an experiment run.

`range start` waits for the target health check. A failed start tears down the attempted
Compose project and leaves a stopped manifest. `stop` preserves containers; `destroy`
removes the project's containers, network, and volumes but never deletes a shared image or
another project's resources. Reset destroys and recreates only the same instance project.

Milestone 1 tests cover deterministic builds, lifecycle and idempotency, gateway policy,
redirect handling, external egress denial, two-run network separation, and teardown
ownership. The hello range has no accounts, so it generates a unique instance nonce but no
credentials. The authenticated SaaS range and its per-instance credentials are documented
in [saas-range.md](saas-range.md).
