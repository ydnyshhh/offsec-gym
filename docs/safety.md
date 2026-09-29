# Safety boundary

OffSecGym accepts only explicitly provisioned synthetic targets. The first runtime uses a
unique Compose project and an internal network per run. Target ports are not published.
The gateway is the only target-facing executor. Agents receive typed tool access, no Docker
socket, no host shell, and no arbitrary network client.

The gateway must enforce allowlisted service identities, destination resolution, HTTP
redirect targets, action and request budgets, rate limits, and experiment policy before
dispatch. Browser support later must route every navigation and subrequest through the same
boundary. The controller's model-provider traffic is separate from agent tool traffic.

The validator's hidden oracle and control database are outside the agent-visible network and
API. Evidence artifacts are scoped by run and scrubbed for secrets before export. Credentials
are generated per run and kept out of logs and model-visible manifests.

Milestone 1 acceptance tests must prove: external egress blocked, another run's services
unreachable, disallowed destinations and redirects rejected, targets unreachable from the
agent except through the gateway, and cleanup removes only resources bearing the run's
ownership labels. Docker configuration is a starting mechanism, not proof of containment.
