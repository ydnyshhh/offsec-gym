# M6.5.1 witness recovery: preflight and unrun commands

**State:** implementation and 10-pair seed/sample/pilot manifests frozen. No paid M6.5.1 OpenRouter call has been made. The two-cell pilot is the next gate; the 20-cell sample remains unrun. The reporter 30k allowance is a pinned provisional value, with any feasibility amendment required before sample collection.

## Source and sample pins

- Protocol: `m651-witness-recovery-v1`; pilot: `m651-witness-recovery-pilot-v1`.
- Implementation source commit: `fc56310d7f767c6cd0387a1a3d86f1c62d042a4c`. The later manifest and documentation commits contain no implementation changes.
- Seed-selection source commit: `0d066413356ae5ef5728fd1ffd3ac027f0da2bcc`.
- Seed-selection manifest SHA-256: `9c994c21816bea1627c8aa3f108e9906f100aafe15fefdc4c55777c5982519b9`.
- Full 20-cell manifest SHA-256: `936eb31c458aa098d400baf5c3e399e7fcf557a4e4207c8f45d66aa8a0ea183a`.
- Non-sample two-cell pilot manifest SHA-256: `677cfff6cb37542643a530a44093d9e43a28387ef2761e2b460b50752ee6c81d`.
- Probe config SHA-256: `1de21e52cdcc9bebcb0e0889127a06fc26bdfcdb0019d93152a196cc0cab1391`.
- Reporter config SHA-256: `7c3e1fe6b552d6a3073b68b21c191edf8cb766fe921c480e26cf129a0d93ae76`.
- Reporter prompt SHA-256: `795d55ef09e5ee75a8528a29333d159344568d88140e6f6a72652af88cca875c`.
- Reporter tool schema SHA-256: `3cdbc12458673878964931ee0f8828bc3feac60f5461f79dbab42484f039e59a`.
- Validator SHA-256: `5f36f008e579c6a43022a360995400622de12da086e1a2c7bdb8735de3a27053`.
- Predeclared analysis SHA-256: `2fe19a4d6a8fc3e312fb05abff1cca1a43baa9594af34b7f72e2c45cf0aabeaa`.

The deterministic selector scanned existing structured manifests, configs, and diagnostics and explicitly excluded prior local witness-branch seeds. Its recorded exclusions include 0, 1, 42, 1001–1010, 1101, 2001–2010, and 2101. It mapped SHA-256 outputs into the integer range 3000–999999, skipped exclusions/collisions, and took the first ten sample candidates. **Frozen sample seeds:** `234443, 880863, 316054, 56072, 883328, 403459, 52266, 193727, 995038, 833452`. **Distinct pilot seed:** `633648`. The selection declaration was committed before building any of these pairs. The later planner read fixture bytes only to verify paired build identities and SHA-256; it did not inspect root outcomes.

The manifest pins ten vulnerable/patched sibling pairs, 20 deterministically shuffled cells, selected endpoint `moonshotai/kimi-k3-20260715` on `Moonshot AI`, the Kimi K3 high-reasoning request, bootstrap semantics, range compiler version, source/config/validator/analysis hashes, evidence packet v3, prompt/tool hashes, budgets, and no-retry policy. The reporter does not receive variant labels.

## Budgets and offline checks

| Resource | Probe | Reporter |
| --- | ---: | ---: |
| Model tokens | 120,000 | 30,000 provisional |
| Model calls | 20 | 4 |
| Output tokens per call | 8,192 | 4,096 |
| Wall seconds | 900 | 300 |
| HTTP/action limits | 60 / 60 | zero active tools |
| Read-only retrievals | — | 24 |
| Finding submissions | existing probe policy | 12 |

Bootstrap has a separate 32-action/32-HTTP/120-second cap and zero model calls. The controller model reservation is 150,000 tokens/24 calls; the probing task remains capped at 120,000/20. The packet contains a full hashed audit index, including terminal attempts without citable response evidence; the initial model turn receives a compact index and retrieves exact evidence by ID. A read-only format check over the 20 completed historical 120k M6.5 traces measured full packet sizes of 33,910–55,700 characters, compact first-input indices of 19,779–29,751 characters, and Kimi request preflight estimates of 14,867–20,369 input tokens. The largest initial estimate plus the 4,096 output reservation was 24,465, below the 30k reporter cap. Those historical traces had zero terminal attempts without response evidence, so the non-sample pilot must also check that the added attempt index remains bounded. These are **format feasibility measurements**, not reporter effectiveness or held-out outcomes. Later turns may still need the pilot to establish a usable trajectory within 30k.

Fake-provider tests cover packet provenance and isolation, unknown/network tool rejection, foreign evidence rejection, retrieval/submission events, full probe→reporter→validator recovery, duplicate non-incrementality, patched rejection, static document/invoice/ticket/public proof, refund same-identity ordering defects, blocked attempts without false proof, terminal bookkeeping, and paired-seed analysis. The full local suite passed **204 tests**, with **57 PostgreSQL/Docker integration tests skipped** because their local services were unavailable; Ruff checks and formatting passed. The clean manifest rebuild independently matched both pinned files. Exact-head hosted CI should be checked before any pilot.

Price snapshot: $3 per million input and $15 per million output tokens. Reserving every configured token at the higher output price bounds the two-cell pilot at **$4.50** and the 20-cell sample at **$45.00** cumulative estimated token cost. These are conservative configured thresholds, not provider invoices or expected spend. The journal reserves that worst-case amount before each cell; it stops before exceeding the threshold. The pilot and full sample require separate paid decisions. Provider failure or interruption is retained without silent retry.

## Prepared live commands — neither executed

Run from the repository root with `OPENROUTER_API_KEY` and `OFFSECGYM_DATABASE_URL` already set. These commands are recorded for a later, separately approved collection step.

Pilot:

```bash
PYTHONPATH=src .venv/bin/python -m offsecgym.research.m65_witness_execute \
  --manifest experiments/manifests/m651-witness-recovery-pilot-v1.json \
  --journal .offsecgym/diagnostics/m651-witness-pilot/journal.jsonl \
  --state-dir .offsecgym/m651-witness-pilot/range-state \
  --max-estimated-usd 4.5
```

Full sample, only after the pilot's feasibility gate and separate cost decision:

```bash
PYTHONPATH=src .venv/bin/python -m offsecgym.research.m65_witness_execute \
  --manifest experiments/manifests/m651-witness-recovery-v1.json \
  --journal .offsecgym/diagnostics/m651-witness-sample/journal.jsonl \
  --state-dir .offsecgym/m651-witness-sample/range-state \
  --max-estimated-usd 45
```

The exact selection and sample manifests are immutable once live sample collection begins. A non-sample pilot may justify only an explicitly documented infrastructure-feasibility amendment before then; do not tune the prompt from success or failure. A recovered finding demonstrates extra-inference reportability from frozen actions. Equal-total-compute comparison is a later protocol.
