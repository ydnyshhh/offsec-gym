# M6.5 common-bootstrap monolithic control: preflight

## Protocol boundary

The [separate control manifest](../../experiments/manifests/m65-common-bootstrap-monolithic-v1.json)
pins 100 proposed cells: ten `tenant_boundary_v2` seeds (1001–1010), paired
vulnerable/fully patched builds, and 40k, 60k, 80k, 120k, and 160k model-token
budgets. Every proposed monolithic cell is policy-feasible. The manifest
pins runtime source `327f1a6`, the exact control config, collector, analysis,
build pair IDs, fixture hashes, randomized order, and historical M6.4
manifest hash. It is **planned, not collected**. The manifest SHA-256 is
`3b8cdb79ef3cc6efb8d0381881fb2928999730b739fc29d981ec0985901f5f79`.

This is a matched **historical** comparison with the frozen M6.4 worker
observations. Seeds 1001–1010 and their worker outcomes are already known;
the control is not a fresh held-out or concurrently randomized architecture
test. At 40k–80k, only opportunity-aware workers have observed worker cells.
Fixed and parallel workers were structurally infeasible under their frozen
117k minimum, not zero-recall model observations. At 120k–160k, the control
can be compared descriptively with all three worker arms. The predeclared
[analysis](../../src/offsecgym/research/m65_monolithic_analysis.py) reports
seed-paired differences and 10,000-resample intervals while retaining
unscored failures separately.

## Information and runtime checks

The new `bootstrapped_monolithic` orchestrator reuses the exact worker
`PrerequisiteBootstrap` and its controller budget accounting. The bootstrap
has a 32-action/32-HTTP cap, 120-second cap, and zero model calls/tokens.
The agent phase has 60 action/HTTP, 20 model-call, 8,192 output tokens per
call, 900-second, and curve-specific total-model-token caps. The global
controller action/HTTP declaration is 92/92; the deterministic seed-1101
bootstrap actually makes 17 GET dispatches. Both phases use structured
WorldState, `known_routes`, the deterministic validator, Kimi K3 high
reasoning, and a Moonshot AI upstream request. The agent's first context
includes cited bootstrap facts and checked actions reconstructed from
request/evidence artifacts, without oracle or patch-state data.

A fake-provider Docker run on **non-sample v2 seed 1101** completed the 17
GET bootstrap, generated one monolithic model request, and scored validly.
Its bootstrap snapshot SHA-256 was
`0e50d8ea223b31b19729e859054c8d4c3398e31aeec059ee4ecff3acbe8ba4a1`,
identical to the frozen M6.4 seed-1101 worker pilot. The first model input
contained checked identities, previously checked requests, and known entity
facts. The existing transcript and structured monolithic fake-provider
range tests also passed. The unit suite passed **163 tests** after the
runtime and analysis commits. These tests make no paid model calls; they
do not establish live provider behavior or finding quality.

The [collector](../../src/offsecgym/research/m65_monolithic_execute.py)
rebuilds every spec/hash from the pinned manifest, checks the historical
journal and trace hashes, records one start/completion per cell, forbids
silent interrupted-cell retry, requires 17 bootstrap requests, checks run
and controller replay, and stops on selected-endpoint drift or an unscored
outcome. The requested selected endpoint is
`moonshotai/kimi-k3-20260715` / `Moonshot AI`, based on the completed M6.4
calls; each new completed model call must confirm it. No collection process
is running.

## Resource review

The 100 configured budgets total **9.2 million model tokens**, with at most
**2,000 model calls**. On 2026-10-03, the [OpenRouter Kimi K3 provider
listing](https://openrouter.ai/moonshotai/kimi-k3) showed the selected
Moonshot AI upstream at **$3 per million input** and **$15 per million output**
tokens. Applying the output price to all 9.2 million configured tokens gives
a conservative **$138 token-only planning bound**. This is neither a provider
invoice nor a strict billing cap: provider usage can exceed the preflight
reservation, prices/fees can change, and a synchronous provider request can
outlive coroutine cancellation. The collector requires an explicit
cumulative estimated-cost threshold no greater than $138 and checks it
before each next cell and after each completion. The manifest deliberately
leaves `usd_ceiling` unset pending a live collection decision.

For scale only, applying M6.4's observed input/output mix to all 9.2 million
tokens at this selected-upstream price would give about **$39.7**. A
monolithic agent can use a different mix or fewer/more reported tokens, so
that figure is not a forecast. The configured 900-second agent plus
120-second bootstrap limits sum to **28.3 hours** over 100 serial cells
before range lifecycle and cancellation overhang. The actual elapsed time
needs a bounded live pilot.

## Remaining gate before paid collection

1. Complete exact-head CI on the manifest/documentation commit.
2. Review a bounded, separately journaled vulnerable/patched live pilot on
   non-sample seed 1101 at 40k, with a declared per-run cost cap. Compare its
   bootstrap snapshot, selected endpoint, score validity, event replay, and
   observed usage/latency against this preflight.
3. Confirm a cumulative estimated-cost threshold and explicit authorization
   for the 100-cell historical control. Keep any pilot cells out of the
   control sample. An interrupted or unscored cell remains in the journal
   and requires event-store reconciliation; it is never silently retried.

The prospective evidence-to-finding study on **new held-out seeds** is a
separate M6.5 protocol. This control manifest does not include it and its
cost is not part of the $138 planning bound.
