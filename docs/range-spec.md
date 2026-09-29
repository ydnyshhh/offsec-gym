# Range specification v1

`RangeSpec` is a versioned declarative input. The v1 foundation includes `family`,
`scenario`, nonnegative `seed`, `patched`, optional `patched_properties`, a service topology,
identity counts, and
vulnerability descriptors (`family`, `component`, `variant`). Unknown fields fail
validation. The [hello example](../examples/hello-range.yaml) and
[SaaS pair](../examples/saas-range.yaml) exercise the schema.

The SaaS compiler produces a Compose definition, seeded fixture, public identity roster,
hidden ground-truth manifest, and attack graph. The oracle tests live in the range test
suite. A seed fixes scenario structure and public object identifiers. A separate instance
nonce derives ephemeral credentials. Matched vulnerable/patched builds share the same
scenario seed, fixture, identity IDs, and pair ID; only selected security predicates differ.

For `saas/tenant_boundary_v1`, `patched: true` fixes all five properties. With
`patched: false`, `patched_properties` may list any of the five canonical slugs:
`DOC-CROSS-TENANT-READ`, `INVOICE-CROSS-TENANT-READ`,
`TICKET-CROSS-TENANT-READ`, `MEMBER-REFUND`, and `PUBLIC-INVOICE-METADATA`.
Unknown or duplicate slugs are rejected; `patched: true` and a nonempty list cannot be
combined. Empty list means all five remain vulnerable. The selection is a controller
input and is not automatically revealed to an agent.

Future schema versions will add structural mutation rules and an explicit pair field in
the input spec. The compiler currently derives a stable pair ID from the SaaS spec without
the `patched` flag and `patched_properties` list.
Changes in v1 meaning require a migration rather than silent reinterpretation.
