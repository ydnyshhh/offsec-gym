# M6.5.1 v2 feasibility amendment and preflight

**State:** the v1 two-cell non-sample pilot completed and failed its reporter
continuation budget gate. Its [exact result](m651-witness-pilot-v1-results.md),
manifest, journal, and traces are preserved. The v2 one-cell non-sample
feasibility recheck and the 20-cell sample have **not** run.

## Amendment boundary

The v1 pilot consumed an estimated `$1.304514` and both reporter trajectories
stopped after one model call with `reporter_preflight`. Reconstructing their
second requests from the frozen packets and actual retrievals showed minimum
reporter caps of `33,395` and `44,190` tokens. This is an infrastructure
feasibility failure. No pilot finding outcome was used to change the prompt,
tools, validator, probe policy, or sample selection.

V2 raises only the separately accounted reporter token cap from `30,000` to
`80,000`. The reporter retains four model calls, 4,096 output tokens per call,
24 read-only retrievals, 12 submissions, and a 300-second wall cap. The
monolithic probe remains at 120,000 tokens, 20 calls, 60 actions/HTTP requests,
the same structured memory, prompt, bootstrap, reasoning request, and validator.
The combined controller reservation becomes 200,000 tokens and 24 calls.

The source commitment is `efef7b7e166b701e0e58e056a6969907ea710308`.
The exact reporter prompt hash remains
`795d55ef09e5ee75a8528a29333d159344568d88140e6f6a72652af88cca875c`;
the tool-schema hash remains
`3cdbc12458673878964931ee0f8828bc3feac60f5461f79dbab42484f039e59a`.
The selected endpoint must still be `moonshotai/kimi-k3-20260715` on
`Moonshot AI`, with high reasoning via OpenRouter.

## Pins and cost bound

- V2 full manifest SHA-256: `dd8d8ee0060643833f9644658af07d87675c2d865abc64942fc2f0a5996138ec`.
- V2 one-cell pilot manifest SHA-256: `60a2690b646954fcf8cc23bb67e9b47d46c3e0eacee12a433c88e62baecbfe3c`.
- Probe config SHA-256: `1de21e52cdcc9bebcb0e0889127a06fc26bdfcdb0019d93152a196cc0cab1391`.
- Reporter config SHA-256: `019f43d2747347d0056a6a8bfbe1cc43e76314203854fd7ed452043b7f018fd6`.
- Validator SHA-256: `5f36f008e579c6a43022a360995400622de12da086e1a2c7bdb8735de3a27053`.
- Predeclared analysis SHA-256: `2fe19a4d6a8fc3e312fb05abff1cca1a43baa9594af34b7f72e2c45cf0aabeaa`.

The input/output price snapshot remains `$3/$15` per million tokens. The
conservative cost reservation now uses the output-token ceiling as well as
the total-token ceiling. The probe can spend all 120,000 tokens as output in
the worst case, for `$1.80`. The reporter's four 4,096-token output caps limit
output to 16,384; of its 80,000 total, at most 63,616 can then be input.
Its worst-case cost is `$0.436608`. Thus:

```text
per cell       <= $2.236608
20-cell sample <= $44.732160, below the $45 sample stop
v2 recheck     <= $2.236608
v1 actual + v2 recheck bound <= $3.541122, below the $4.50 pilot approval
```

The collector reserves this upper bound *before* each cell and still checks
actual estimated token cost afterward. The configured output caps are part
of the frozen model request. A provider violation of its cap remains an
operational risk and would stop collection after the affected cell.

The ten selected sample seeds, vulnerable/patched builds, and randomized cell
order are identical to v1. The frozen sample seeds remain `234443, 880863,
316054, 56072, 883328, 403459, 52266, 193727, 995038, 833452`.
The one-cell v2 feasibility recheck uses vulnerable seed `633648`, the
non-sample sibling with the larger v1 packet. It is a declared new protocol
cell, not a retry or an addition to the sample. Its purpose is to confirm
continuation feasibility with the revised cap; its findings are not used to
tune the reporter.

The local suite passed **205 tests** with **57 PostgreSQL/Docker integration
tests skipped**; Ruff lint and formatting passed. Both v2 manifests rebuilt
byte-for-byte from the pinned inputs. Exact-head hosted CI must pass before
the paid v2 recheck.

## Prepared commands — neither v2 command executed

Run from the repository root with `OPENROUTER_API_KEY` and
`OFFSECGYM_DATABASE_URL` set. These are subject to the approved cumulative
pilot and sample limits.

V2 non-sample feasibility recheck:

```bash
PYTHONPATH=src .venv/bin/python -m offsecgym.research.m65_witness_execute \
  --manifest experiments/manifests/m651-witness-recovery-pilot-v2.json \
  --journal .offsecgym/diagnostics/m651-witness-pilot-v2/journal.jsonl \
  --state-dir .offsecgym/m651-witness-pilot-v2/range-state \
  --max-estimated-usd 2.236608
```

Full sample, only if the v2 recheck clears packet, retrieval, model
continuation, validation, replay, endpoint, and cost gates:

```bash
PYTHONPATH=src .venv/bin/python -m offsecgym.research.m65_witness_execute \
  --manifest experiments/manifests/m651-witness-recovery-v2.json \
  --journal .offsecgym/diagnostics/m651-witness-sample-v2/journal.jsonl \
  --state-dir .offsecgym/m651-witness-sample-v2/range-state \
  --max-estimated-usd 45
```

The v1 full manifest remains unrun and is superseded for collection. V2
sample results, if collected, use only the original held-out 20 cells and
the v2 predeclared analysis. Neither pilot contributes to that denominator.
