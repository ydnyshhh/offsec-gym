# M6.6 confirmatory v2: cell-227 provider-health pause

The collector stopped after **227 of 280** frozen assignments. The exact
append-only journal SHA-256 is
`1e40a9cfdac4ca8c5245aa412bf509c432d7672800ef32a304288340d52db809`.
Cumulative estimated model-token cost is **$99.426024** under the original
$504 cap. Cell 228 never started, and pair 114 has no postcheck receipt. No
completed cell may be retried or replaced.

Cells 226 and 227 ended `provider_failed/provider_unavailable`. The frozen
provider-health policy paused after consecutive provider transport failures.
The selected route remained Moonshot AI | `moonshotai/kimi-k3-20260715` at
$3/M input and $15/M output. A full read-only PostgreSQL postcheck passed:
**227 source runs, 218 score-valid cells, nine retained provider failures,
17 referenced validator replays, 244 total runs, and 236,404 events**. Event
identities, trace hashes, and score replay matched; there was no extra run
or active action/model reservation. Cell 226's event-identity SHA-256 is
`04e3d6335207f89d8c3a11fb631f929e540a47167cc03286d8fe4fc3a0313f84`
(353 events), and cell 227's is
`d19779443287eb032f335b61faa524a5db92a620cab330b3ec9354354f5ade98`
(779 events). The original study evidence was not changed by the postcheck.

## Proposed operational continuation

This proposal binds the one-time wrapper to the exact 227-cell journal,
stopped log, pair-113 receipt, pair-114 endpoint preflight, prior cell-208
offline amendment, and the two failed run identities. Pair 114 is partial:
cell 227 is retained as failed, while its assigned partner cell 228 has not
started. A separately pinned, create-only paid-call approval would authorize
**only frozen cells 228–280 once**, with no retry or replacement, one new
provider-health epoch at 228, and the same $504 **cumulative** cap. The
approval hash remained zero in that draft, so the paid path was closed. The
user subsequently approved this exact scope after PR #29 passed both
exact-head CI checks. PR #29 merged as
`0f9f6eafabbdd051b17283b4ff6d6f3a5fe41195`, preserving its three
individual commits. A separate, create-only private approval receipt at
`.offsecgym/m66-confirmatory-v2/cell227-approval.json` has SHA-256
`cdcc2f9f659acf7326bdb44aaf35f9cb048dabf4d0026f4662ffdfdac6664074`.
The operational execution guard pins only that hash; the private receipt is
not committed.

If approved, the wrapper must replay the 227-cell PostgreSQL evidence and
perform a fresh public selected-endpoint and price check before cell 228. It
then runs that partner exactly once, audits it, closes pair 114, and requires
the full postcheck to pass. Only then can the unchanged collector execute
frozen cells 229–280. Any new provider-health pause or other gate failure
stops collection again. The wrapper cannot use a new epoch at 229 or treat
the two failed cells as scored results.

The source and protocol commits, manifest, model request and prompt, range,
validator, budgets, seeds, arm order, and all earlier terminal outcomes stay
fixed. This amendment changes operational admission and evidence closure,
not the model treatment or frozen analysis.

## Interpretation limits

The new health epoch is a post-start operational deviation. All nine provider
failures remain in the assigned schedule, and the final report must show
attrition by arm, range, and variant. An eligible-only estimate is conditional
on surviving provider and infrastructure failures. A public route/price
check confirms metadata, not future provider availability. Completion of the
remaining cells is not guaranteed.
