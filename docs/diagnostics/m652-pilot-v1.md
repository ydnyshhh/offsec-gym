# M6.5.2 non-sample feasibility pilot

The one-cell vulnerable pilot used frozen manifest SHA-256
`8199fcf6b9ec12e506e671d7849462754980d38dcb5206432dec432df53dd61c`
and seed 851493. Exact-head CI for the frozen protocol passed on
[run 37191659224](https://github.com/ydnyshhh/offsec-gym/actions/runs/37191659224)
before the pilot. The source run ID was
`d44896e5-864f-4306-9338-45e38a3d2782`; it was recorded in the journal
before dispatch. No cell or model request was retried.

The pilot completed with a score-valid source and two score-valid reporting
branches. The source stopped at its declared ten-call checkpoint. Both
branches made a model request, and both first request bodies reconstructed
byte for byte from the checkpoint and frozen evidence packet. The actual
selected endpoint was `moonshotai/kimi-k3-20260715` on Moonshot AI. The
continuation arm made one reporting call and the fresh arm made three; this
is feasibility data, not an outcome comparison. No post-split gateway action
or active controller reservation remained. Estimated token cost was
**$0.831483**, below the pilot's $2.50 stop.

## Read-only audit amendment before the sample

The frozen v1 postcheck failed on a Python representation comparison: the
replayed audit held empty finding IDs as `()`, while the JSON journal stored
the same field as `[]`. The serialized audits were equal. This was a
postcheck implementation defect, not a source or branch trace mismatch.
The v1 failure is retained; the pilot was not rerun.

[`m652_postcheck_v2.py`](../../research_ops/m652_postcheck_v2.py) changes
only that comparison to a JSON round trip. The [audit-only amendment](../../experiments/manifests/m652-audit-amendment-v1.json)
pins both script hashes, the unchanged pilot and sample manifest hashes,
and the pilot journal and receipt hashes. The corrected read-only postcheck
passed against PostgreSQL: 467 source events, 62 branch events, 529 distinct
event IDs, source and branch score replay, selected endpoint, fixture/pair
binding, source-only eligibility, request reconstruction, and byte-for-byte
pilot analysis. This amendment changes no model prompt, budget, seed, order,
gateway policy, scoring rule, or stored event.

The frozen feasibility gates passed. The 24-seed confirmatory sample may
start with its original manifest and the corrected read-only postcheck.
Pilot recovery outcomes are excluded from confirmatory analysis.
