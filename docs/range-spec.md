# Range specification v1

`RangeSpec` is a versioned declarative input. The v1 foundation includes `family`,
`scenario`, nonnegative `seed`, `patched`, a service topology, identity counts, and
vulnerability descriptors (`family`, `component`, `variant`). Unknown fields fail
validation. The [hello example](../ranges/hello/hello-range.yaml) exercises the schema.

The compiler will eventually produce a Compose definition, fixtures, account identities,
hidden ground-truth manifest, oracle tests, attack graph, and network policy. A seed fixes
scenario structure and public object identifiers. A separate run nonce generates ephemeral
credentials. Matched vulnerable/patched builds share the same scenario seed and visible
fixture structure; only the relevant security predicate differs.

Future schema versions will add structural mutation rules and explicit pair identifiers.
Changes in v1 meaning require a migration rather than silent reinterpretation.
