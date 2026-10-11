# M6.6 v2 cell 249 continuation approval provenance

After reviewing CI-green PR #31, the user explicitly approved merging the operational amendment and running only frozen assigned cells 250–280 once. The approval retains every prior outcome, forbids retries and replacements, preserves the provider-health epoch begun at cell 228 without a reset at 250, and keeps the original $504 cumulative estimated model-token cost ceiling.

PR #31 merged as `572e0c99a335f84580d0964310a622dca4422327`. The create-only private receipt is held outside Git at `.offsecgym/m66-confirmatory-v2/cell249-approval.json`; its SHA-256 is `073ed88c330ab88f16fa88a650261b8a35a45a84a8662ec04d6f9257bbe1cb47`. This commit pins only that digest and tests it. The original manifest SHA-256 remains `a8aa98654360945ad69f350c38153dccaf827434a4e157819f558ae8da05916c`. The source and protocol commits remain `6bfb14dc240ae6ab7e65bd04025b74688312a806` and `9fe5b24e8fa1550ed681e9345673cbc1457e7e23`.

Before any paid continuation, require exact-head CI on this approval pin, a clean execution checkout at its merge commit, the exact stopped 249-cell journal SHA-256 `866c0306e643c0b9fd33d88440acbab5ade171fd7bd362207ff18c11b350975d`, authoritative PostgreSQL replay, absence of another collector, and a fresh public selected endpoint and price check. The separate receipt does not authorize any cell outside 250–280.
