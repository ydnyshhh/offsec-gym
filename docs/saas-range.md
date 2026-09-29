# Milestone 2 SaaS range

The first SaaS scenario is a compact multi-tenant API named AcmeCloud. It has three
workspaces, a member and administrator in each workspace, one support agent, and one
platform administrator. The service covers login, membership, documents, invoices,
refunds, and support tickets. It uses an in-process SQLite database seeded from a
deterministic fixture. Reset recreates the container and therefore restores the fixture.
Support can read and reply to tickets across workspaces but cannot list tenant documents
or invoices. Workspace administrators can list members in their own workspace and approve
refunds there; the platform administrator can act across workspaces.

The compiler accepts `saas/tenant_boundary_v1` with the exact supported topology and
identity counts. The seed determines all public entity IDs and relationships. The
`patched` flag changes only five authorization or data-exposure predicates. Paired builds
therefore use identical fixtures and a common pair ID. A range instance supplies a fresh secret at startup;
passwords derive from that secret and are never part of a build. The controller gives the
gateway a selected identity ID, and the gateway obtains its credential and logs in through
a fixed worker. The agent sees the public identity roster, not raw credentials.

The compiler writes `ground_truth.json` and `attack_graph.json` to a private oracle
directory outside the Compose build context. Neither file is mounted into containers or
returned by target API endpoints. Ground truth records subject/object relationships,
expected policy, the observed vulnerable effect, root cause, and proof steps. The graph
records prerequisites and evidence needed for each transition. Milestone 3 will consume
these files for scripted proofs and validation; Milestone 2 oracle tests use them directly.

The five intended properties are cross-workspace document read, cross-workspace invoice
read, cross-workspace support-ticket read, member-initiated refund, and anonymous invoice
metadata disclosure. Ordinary membership listing, support replies, and administrator-only
membership listing are decoys with the same behavior in both builds. Reading a foreign
document reveals a related invoice ID; that creates a multi-step document-to-invoice path.

The SaaS service and HTTP worker run on one instance-owned internal Docker network with no
published ports. The worker can contact only the SaaS service. Requests have bounded
methods, paths, JSON bodies, response sizes, timeouts, and action budgets. No agent input
is executed as shell code. The in-process database is a deliberate first-range choice:
it keeps paired state reproducible without adding a database service that the agent could
reach directly. The control-plane PostgreSQL event store remains separate.

Milestone 2 acceptance requires each intended proof to succeed in the vulnerable build
and fail in its patched sibling using the same seed and identity relationship. Tests also
check ordinary authorized behavior, decoys, ground-truth consistency, reproducibility,
network containment, and teardown.
