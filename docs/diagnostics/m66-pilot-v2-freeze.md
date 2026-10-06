# M6.6 excluded pilot v2: frozen, unrun

The first eight-cell pilot remains closed with failed infrastructure gates. Its
four Range A cells are score valid, two Range B vulnerable cells failed before
model work, and the patched Range B pair was not started. Those cells are not
retried or carried into v2. The [v1 result](m66-pilot-v1-results.md) retains
the failed gates and authoritative accounting.

V2 is a separate excluded feasibility pilot. Its model and witness policy are
pinned to source commit `950bdb746e0d8ae9d68a324f58e5b763c7ddbc1d`.
Configuration, stage extraction, and offline analysis are pinned to merged
protocol commit `ea56685ff55d2bb20207edd8429562a793ed904f` (PR #9).
Exact-head CI passed in [run 37469907758](https://github.com/ydnyshhh/offsec-gym/actions/runs/37469907758)
before that merge. The only model-protocol changes from v1 are the declared
Range B bootstrap cap of 40 and explicit selected-endpoint token prices; the
read-only stage extractor now filters completed-run verdict and terminal
events from its source evidence bundle.

The create-only [v2 manifest](../../experiments/manifests/m66-pilot-v2.json)
has SHA-256
`b8dbdc28689e7299c4f5f099ce8e3f788483de7b558c0319043fae7ca43fe528`.
It binds eight ordered cells on fresh seeds **712868** (Range A) and **551180**
(Range B), the versioned 160-seed exclusion registry, source/config/analysis
hashes, and the selected `Moonshot AI | moonshotai/kimi-k3-20260715` endpoint.
The final read-only OpenRouter endpoint check at
`2026-10-06T14:08:03.062847Z` returned `moonshotai/kimi-k3` at $3 per million
input tokens and $15 per million output tokens. The maximum configured
token-price estimate is $14.40 for all eight cells; the proposed cumulative
stop is $15.

Before this freeze, a no-model selected-seed qualification completed both
vulnerable and patched Range B control/witness pairs with 33 bootstrap GETs
and score-valid terminal runs. A read-only local check also ran the corrected
extractor against all four completed v1 Range A traces. Neither check is part
of the eight-cell live pilot.

The manifest has `paid_model_calls_authorized: false`. **No v2 paid model call
has started.** Execution requires a separate, later approval artifact bound
to the exact manifest hash and a new explicit approval for the additional
spend. The unused portion of v1's approval does not transfer. Once approved,
each cell runs at most once in the frozen order, with no result-based
replacement. Pilot gates concern infrastructure, score validity, endpoint,
trace replay, stage extraction, and cost; an effect-size threshold is not a
pilot gate.
