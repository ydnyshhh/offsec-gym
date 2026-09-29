# Safety boundary

OffSecGym accepts only explicitly provisioned synthetic targets. The first runtime uses a
unique Compose project and an internal network per range instance. Target ports are not published.
The gateway is the only target-facing executor. Agents receive typed tool access, no Docker
socket, no host shell, and no arbitrary network client.

The first gateway enforces an allowlisted service, method, path, action and HTTP request
budgets, and a per-run rate limit before dispatch. Its worker returns redirect responses
without following them. Browser support later must route every navigation and subrequest
through the same boundary. The controller's model-provider traffic is separate from agent
tool traffic.

For the first range, the controller sends typed requests to a fixed Python worker through
Docker exec. The worker and target share only an internal network and publish no ports.
This avoids giving the worker an external route merely to make it reachable from the host.

The validator's hidden oracle and control database are outside the agent-visible network and
API. Milestone 1 has no oracle or credentials. It saves local evidence under the instance
directory; later authenticated ranges need per-run credential isolation and export scrubbing.

Milestone 1 acceptance tests prove: external egress blocked, another instance's service
unreachable, disallowed destinations rejected, external redirects not followed, target
ports unpublished, and one instance's cleanup preserving another. Runtime mutations check
ownership labels before acting. Docker configuration is a starting mechanism, not proof of
containment for future agent code with broader capabilities.
