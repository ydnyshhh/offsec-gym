# M6.8.3 historical reference-contract comparison

This is a deterministic, offline replay of the **same 18 saved proposals and 43 references** under three detached contracts. The source admission rules and exact grammars are in [the design](m683-reference-contract-design.md). The [canonical JSON aggregate](m683-reference-contract-comparison.json) is identifier-free, SHA-256 `b6ff7cb27616c4f4afc5798937390d00d29535739164e9928b7f26b5b779f187`; individual references remain in a new ignored, restricted private output. The historical frozen parser, outcomes, reservations, and scores were not changed.

| Contract | Accepted references / 43 | Complete schema-valid proposals / 18 | Complete eligible proposals with every reference publicly grounded / 18 |
|---|---:|---:|---:|
| C0 direct Pydantic UUID | 0 | 0 | 0 |
| C1 frozen typed grammar | 27 | 4 | 3 |
| C2 proposed `change:` alias | 39 | 15 | 10 |

All four C1-complete proposals passed the unchanged post-normalization schema. All 15 C2-complete proposals also passed it. No invalid reference was removed to obtain either count. Historical acceptance is a counterfactual validation result, **not** a model response to a treatment or a measure of security-task success.

## Grounding remains independent (43-reference denominator)

| Contract | Accepted and grounded | Accepted without established prior grounding | Accepted with grounding unknown | Rejected despite grounding | Rejected without established grounding | Rejected with grounding unknown |
|---|---:|---:|---:|---:|---:|---:|
| C0 | 0 | 0 | 0 | 34 | 8 | 1 |
| C1 | 22 | 5 | 0 | 12 | 3 | 1 |
| C2 | 31 | 7 | 1 | 3 | 1 | 0 |

The grounding labels come from M6.8.2's independently verified, typed, public-source and delivery audit. The one unknown reference appeared in general context and in a separate setup-run source, but delivery from that source was not established. Neither syntax acceptance nor an isolated private-oracle match makes an object model-visible.

## Marginal C1 → C2 contribution

The proposed alias newly accepts **12 references**. Nine were independently publicly grounded before proposal, two had no established prior grounding or verified public entity, and one retained unknown delivery. Ten resolve to a unique typed public entity and have a confirmed declared-kind correspondence; two are type-indeterminate. Seven have a private target-root match in the separate oracle comparison, five do not. Those oracle labels do not measure model intent or public visibility.

At the whole-proposal level, **11 additional proposals** become schema-valid: seven have every target publicly grounded, three do not, and one has unknown complete grounding. Eight have every declared type confirmed. The number of complete C2 proposals is therefore 15, not 4 + 12: references share proposals and whole-array acceptance is required.

C1 leaves 13 mixed accepted/rejected arrays; C2 leaves two. Three C2 proposals still have a syntactically unsupported entry despite containing at least one grounded reference. The remaining C2 reference rejections comprise three wrappers with otherwise publicly grounded UUIDs and one natural-language reference without a verified public match. The alias alone does not solve those representations or ground the newly accepted but unverified IDs.

## No-model persistence and negative controls

The [detached qualification](m683-no-model-qualification.json), SHA-256 `1337ccefc44e352c28d842a6d5db5aa7f4f782fb7cb722c43210b671557311aa`, used fabricated objects in a disposable local PostgreSQL database. Each accepted C0, C1, and C2 input produced three authoritative state events, survived a fresh database connection, preserved the target UUID, planned steps, and hypothesis, and reached synthetic completion. An invalid reference produced no state; separate run IDs remained isolated; zero security/task-verdict events were emitted. The qualification ran twice with byte-identical scientific projections. It does not establish live controller integration, model uptake, later state use, or a prospective effect.

Focused contract/replay tests, full unit tests, and the isolated PostgreSQL test exercise strict-control equivalence, C1 delegation, C2 allowlist, identity and order preservation, whole-array rejection, schema failures, grounding independence, source admission, and detached persistence. Exact test results and hashes are recorded in the PR verification report.
