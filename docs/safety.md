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
API. The hello range has no oracle or credentials. SaaS oracle files stay outside the build
context; instance credentials derive from a nonce held by the controller. Request and
response artifacts live under the instance directory with mode-0600 permissions. Query
parameters and recursive JSON keys that look like credentials are redacted in saved request
artifacts. Exports still require explicit scrubbing policy before being shared.

Milestone 1 acceptance tests prove: external egress blocked, another instance's service
unreachable, disallowed destinations rejected, external redirects not followed, target
ports unpublished, and one instance's cleanup preserving another. Runtime mutations check
ownership labels before acting. Docker configuration is a starting mechanism, not proof of
containment for future agent code with broader capabilities.

The SaaS range uses the same network and container restrictions. Its gateway accepts only
identities listed in the experiment context, derives their credentials on the trusted
controller, and logs in through a fixed worker. It blocks direct login actions and never
records passwords in action events or response evidence. The request event contains a hash
and artifact ID; the saved request artifact redacts credential-like fields even for blocked
actions. A new instance gets a fresh secret; reset rotates it and increments the generation.
The secret is visible to trusted Docker administrators through container
configuration, so Docker host access remains part of the trusted control boundary. Ground
truth and attack-graph files are outside the image build context and never mounted into
the target or worker.
