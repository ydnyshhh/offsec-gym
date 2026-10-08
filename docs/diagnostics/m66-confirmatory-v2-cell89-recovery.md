# M6.6 confirmatory v2: cell 89 provider interruption and guarded continuation

The frozen 280-cell assignment and model protocol remain unchanged. On 2026-10-08,
the sole collector stopped after cell 89. Its second model request failed with
`provider_unavailable`; the run was scored invalid and journaled in its original
position. The immediate public endpoint recheck then failed DNS resolution
before cell 90 started. There is no pair-45 postcheck receipt.

The stopped journal has SHA-256
`1164b037d3f61c9ef921951b1e5d5b530ff21928d3f0251595ce18bff3d4b6ec`.
It contains 89 completed cells, 88 score-valid cells, one provider-failed cell,
and $38.833569 cumulative estimated model-token cost. Cell 89 is
`d22b46a80a5008c9`, run `ce9a6401-17d0-49ed-a120-187b4927b6b7`.
The read-only postcheck replayed all 89 authoritative PostgreSQL streams,
their event identities, trace hashes, scores, stages, and run inventory; it
found no extra run or active reservation.

`research_ops/m66_resume_after_provider_failure.py` is a one-time operational
continuation for this exact stopped prefix. Before any paid call it requires:

1. The unchanged source and protocol commits, frozen manifest and approval
   hashes, and unchanged execution collector/audit files from merge
   `85c0c885ea6f3ed73ecc4ceaaed0adda0af29082`.
2. Byte-identical stopped journal and authoritative PostgreSQL replay, including
   cell 89's retained invalid score and no run for cell 90.
3. The frozen provider-health decision of `continue`, sufficient remaining
   approved cost, and an exclusive journal lock.
4. A fresh public endpoint check matching pair 45's original Moonshot AI route
   and $3/M input, $15/M output prices. Its receipt is create-only.

It then executes only the already assigned partner cell 90, audits it, writes
pair 45's receipt if eligible, replays the 90-cell prefix, and delegates cells
91–280 to the unchanged collector. Any new interrupted cell or failed gate stops
without retry or replacement. The original v1 stopped sample and the v2 frozen
manifest, seed order, prompts, budgets, validator, and analysis are untouched.

This is an operational amendment after collection began. The provider-failed
cell remains in the intention-to-treat assignment. Any estimate must retain it
as missing/invalid according to the frozen analysis rather than replacing it.
