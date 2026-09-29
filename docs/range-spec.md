# Range specification v1

`RangeSpec` is a versioned declarative input. The v1 foundation includes `family`,
`scenario`, nonnegative `seed`, `patched`, a service topology, identity counts, and
vulnerability descriptors (`family`, `component`, `variant`). Unknown fields fail
validation. The [hello example](../examples/hello-range.yaml) and
[SaaS pair](../examples/saas-range.yaml) exercise the schema.

The SaaS compiler produces a Compose definition, seeded fixture, public identity roster,
hidden ground-truth manifest, and attack graph. The oracle tests live in the range test
suite. A seed fixes scenario structure and public object identifiers. A separate instance
nonce derives ephemeral credentials. Matched vulnerable/patched builds share the same
scenario seed, fixture, identity IDs, and pair ID; only the five security predicates differ.

Future schema versions will add structural mutation rules and an explicit pair field in
the input spec. The compiler currently derives a stable pair ID from the SaaS spec without
the `patched` flag.
Changes in v1 meaning require a migration rather than silent reinterpretation.
