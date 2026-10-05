# M6.5.2 reporting-context preflight

## Protocol boundary

The single-configuration design is recorded in
[M6.5.2](../milestone-6.5.2.md). It compares fresh and authentic
post-tool continuation context on the same frozen probing prefix. Both arms
use identical read-only reporting instructions, tools, source evidence,
model, selected endpoint, and added budget. The treatment is context
carryover under this reporting role; it is not a continuation of the
probe's original HTTP-capable instruction.

The confirmatory matrix has 24 new vulnerable/patched seed pairs, one
120k-token ten-call probe budget, and one 80k-token four-call allowance per
arm. A separate one-cell vulnerable pilot uses another new seed for
feasibility only. No existing M6.5.1 sample cell is reused. Model endpoint
pricing was checked against the public
[OpenRouter Kimi K3 endpoint metadata](https://openrouter.ai/api/v1/models/moonshotai/kimi-k3/endpoints)
on 2026-10-04: Moonshot AI `moonshotai/kimi-k3-20260715`, $3 per million
input tokens and $15 per million output tokens. The collector checks that
exact endpoint price before every paid cell and checks the selected
response metadata after each model turn.

The maximum configured estimated token cost is $2.216256 per cell,
$2.216256 for the non-sample pilot, and $106.380288 for 48 sample cells.
The frozen stops are $2.50 for the pilot and $108 for the sample. These
figures are ceilings from the token caps at the frozen price snapshot,
not provider invoices. Pilot and sample need explicit paid-call approval.

## Mechanical gates before paid use

- Source code, seed selection, configs, endpoint, price snapshot, model
  request, checkpoint semantics, prompt/tools, arm/cell order, budgets,
  validator, audit, postcheck, and analysis are committed in immutable
  manifests. Exact-head GitHub CI passes.
- The checkpoint references a completed model turn and verifies its exact
  request/response artifacts. Its carry begins with the provider's output
  items and has exactly one ordered tool output per function call. Opaque
  encrypted reasoning items are retained.
- The reporter's first request is rebuilt using the same serializer as the
  provider adapter and matches the private ordered request artifact byte
  for byte. A deliberately changed instruction fails this audit.
- Fake-provider/PostgreSQL tests show both arms share one source prefix,
  receive identical reporter tools and allowances, cannot dispatch HTTP,
  and replay independent scores. The source run ID is journaled before
  dispatch so a crash can be reconciled without guessing or retrying.
- A non-sample pilot must score validly in source and both arms, select the
  pinned endpoint, make at least one request in each arm, pass exact request
  reconstruction, and leave no post-split gateway action or controller
  reservation. Pilot root recovery is not a tuning gate.

## Collection and analysis rule

One source probe and two branches run per cell. A model-behavior failure
before the checkpoint is retained as an ineligible prefix and is not
replaced. Provider, endpoint, artifact, validation, or event-replay failure
is journaled and stops collection for authoritative reconciliation. An
interrupted cell is never automatically retried. After all cells complete,
the read-only postcheck compares private trace hashes with PostgreSQL event
identities, verifies source/branch scores and first-request bytes, recomputes
source-only proof eligibility, and requires byte-for-byte equality with the
predeclared analysis. The primary paired contrast is fresh minus
continuation newly validated roots among source proof-capable roots missed
before the split, with seed-pair bootstrap uncertainty. Invalid-prefix flow,
patched false findings, preflight censoring, and resource use remain visible.

## Freeze record and current status

| Artifact | Frozen identity |
| --- | --- |
| Runtime/source and tests | `dd3b1b0d1275a18647a6aaf0bf5b22a263bd172a` |
| Fresh-seed selection commit | `7b545b880a3e029adc89b073ef29439b747dee1c` |
| Non-sample pilot manifest SHA-256 | `8199fcf6b9ec12e506e671d7849462754980d38dcb5206432dec432df53dd61c` |
| Confirmatory 48-cell manifest SHA-256 | `2afc5caaea1cfc330ea507783e4b8900fbd1255cc99e03f88bcf34b4a240197b` |
| Pilot and sample estimated-cost stops | `$2.50` and `$108.00`, respectively |

The pilot seed is 851493; it is excluded from the 24 fresh sample seeds.
Both manifests rebuilt byte for byte from their committed inputs after seed
selection. Local preflight passed Ruff, 229 unit tests, and three focused
PostgreSQL branch tests. Exact-head GitHub CI and explicit paid-call approval
remain. No M6.5.2 model call or sample collection has started.
