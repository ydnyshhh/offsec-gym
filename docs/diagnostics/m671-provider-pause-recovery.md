# M6.7.1 provider-pause recovery proposal

The excluded feasibility collector stopped at its frozen provider-health gate
after cells 85 and 86 both finished with `provider_failed` /
`provider_unavailable`. The process exited without starting cell 87. This is a
provider-health stop, not evidence of task success or failure on either root.

The read-only PostgreSQL replay at the stop found 86 completed, trace-auditable
cells; 84 score-valid cells; 172 referenced source runs; 143,873 events; 42
complete pair receipts; and zero active model or action reservations. Recorded
estimated model-token cost is $35.802870. Charging the two cost-unknown
provider-failed cells their full $1.80 ceilings gives a conservative
$39.402870, below the frozen $230.40 cumulative stop.

## Immutable stop boundary

| Evidence | SHA-256 |
| --- | --- |
| Original journal | `bbbb3288994825bba77300b78a6ea62ab6958a5203caaec15b3da8f86116ca2a` |
| Original log | `f21f4df12c5e293f2f0ab12c5e3319c69337613bc01fd6668d3d4c98c224acec` |
| Digest of 42 pair receipts | `de94dc65c1fa4ed80130987d27eeb2e4a58e8b314975dcc1bc24f4efee03514b` |
| Digest of 43 endpoint preflights | `1cddf20b8f21196bd9ac001dcb9588b57e47ac2e8730602b9b3c0034dd54daef` |

The new code replays all 86 original cells from authoritative PostgreSQL,
checks every receipt and the exact database inventory, and refuses changed
evidence or active reservations. It closes pair 43 from the retained cell 85
and 86 events alone. A separate create-only journal holds only cells 87–128.
The original journal, log, source, protocol, manifest, model, checkpoints,
seeds, and assignment order remain unchanged.

The human separately authorized **only frozen cells 87–128 once**, without
retries or replacements, retaining cells 85 and 86 as provider failures and
the original $230.40 cumulative estimated model-token cap. A new private
receipt must bind that scope, the stop hashes, and the reviewed operational
merge. Its hash remains an all-zero closed gate in this proposal. A separate
approval-pin change is required before any paid call. Fresh public no-key
endpoint metadata and the original selected-provider response check remain
mandatory. The continuation starts one new provider-health epoch at cell 87;
another gate or interruption stops it again without automatic resumption.

Final analysis uses a read-only logical concatenation of the original and new
journals. The frozen M6.7.1 postcheck still requires 128 assigned rows, 64
pair receipts, exact source-run inventory, no reservations, and deterministic
byte-identical analysis. This remains descriptive excluded feasibility work,
not a confirmatory result.
