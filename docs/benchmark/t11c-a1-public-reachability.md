# T1.1c A1 public reachability

`benchmark_t11c_q3.py` runs a bounded read-only discovery policy against an owned synthetic Docker instance through `ComposeActionGateway`. It calls `GET /api/me`, lists documents in the member's observed workspace, then reads publicly listed own-workspace documents. A `reference_document_id` may become a candidate only when it appears in a checked response artifact. The auditor reopens the request and evidence artifacts and checks run, instance, generation, identity, action, event sequence, response hash, and field path. Exact IDs and the provenance graph remain private; the tracked JSON contains counts, digests, gate checks, and source pins.

The policy does not make a cross-workspace access request or submit a finding. Its result establishes whether a candidate is publicly discoverable under this bounded policy. It does not establish that the candidate belongs to another workspace before the decisive request. The separate `pre_decisive_relationship_known` and `post_decisive_relationship_confirmed` fields prevent that later information from being backdated. A failed bounded policy would be reported as undiscovered, rather than as proof that every possible public policy would fail.

Run with `--dry-run` for scope inspection or `--execute --output-private .offsecgym/benchmark/<fresh-name>` for a new local qualification. The executable uses no provider.

The seed-42 vulnerable execution passed all ten Q3 checks, found one publicly grounded candidate, and verified Docker cleanup. Pre-decisive foreign-workspace knowledge remains false; the later Q4 response provides the separate relationship confirmation.
