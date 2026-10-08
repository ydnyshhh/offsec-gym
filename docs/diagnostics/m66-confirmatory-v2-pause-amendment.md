# M6.6 confirmatory v2: proposed cell-90 provider-pause amendment

The study stopped after 90 of 280 frozen assignments. Cells 89 and 90 both
ended `provider_failed/provider_unavailable`; they remain score-invalid in
their original positions. The frozen provider-health policy correctly returned
`pause` after two consecutive transport failures. No pair-45 receipt was
written and cell 91 never started. The stopped journal is pinned by SHA-256
`1f47e18f9f8b993ea6ddcae3a240133235da4d9b904653b8c5dc932d681acf1d`.

This amendment proposes a **single, explicit new provider-health epoch at cell
91**, after a fresh public check confirms the selected Moonshot AI revision and
unchanged $3/M input and $15/M output prices. It does not retry or replace a
cell. It does not alter the runtime source commit, protocol commit, model
request, prompt, range, validator, budgets, 280-cell assignment, or original
$504 cumulative estimated model-token cap.

The one-time continuation first replays all 90 authoritative PostgreSQL runs,
trace hashes, score decisions, stage outputs, and build/fixture identities. It
reconstructs pair 45's missing postcheck receipt **from those existing events**;
the receipt explicitly marks retrospective closure. It then writes a separate
create-only pause-clearance receipt bound to the stopped journal, pair receipt,
selected endpoint check, and a private, separately approved artifact. The
normal collector replays the now complete 45-pair prefix before admitting cell
91. Its existing request, score, cost, and endpoint gates govern every later
cell. A further provider-health pause stops collection again.

The new approval artifact is separate from the frozen manifest. Following
explicit approval of this exact post-pause scope, the private create-only
receipt has SHA-256
`5da9c5502d26e94adb1bf6b823563db62e178c2cb151a7d6f27d3b4b4cb81018`.
It authorizes **assigned cells 91–280 only, no retry or replacement**, under
the **same $504 cumulative** cap. The code binds this exact receipt hash;
collection still requires exact-head CI and the full prelaunch reconciliation.

This is a post-start operational deviation. The two failed cells remain in the
intention-to-treat schedule; a completed-sample analysis must report their
missing outcomes and any resulting conditional estimand. The amendment must
not be described as part of the original frozen protocol or used to tune model
behavior based on interim scores.
