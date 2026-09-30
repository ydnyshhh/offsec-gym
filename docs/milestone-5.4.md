# Milestone 5.4: persistent checked state and bounded retrieval carryover

[M5.3](diagnostics/kimi-k3-openrouter-m53.md) separated three behaviors:
checked identity mappings disappeared from automatic context after the recent
entity set rotated; explicit worldview results accumulated beside fresh
automatic context; and at least one unchanged document reread occurred while
its UUID, body excerpt, and evidence were already visible. M5.4 addresses the
first two without adding more document-body representation or requiring the
model to invoke `get_entity`.

## Implementation boundary

- Up to 16 checked identities have a section outside the five-object recency
  set. Each line carries the exact identity UUID, observed role and workspace
  (or `unknown`), HTTP status, action ID, and evidence ID. Only controller
  facts from complete, verified responses supply role/workspace values.
- A separate index retains the latest action/evidence pair for up to 32 unique
  request fingerprints. The fingerprint includes method, path, identity, and
  canonical JSON body. Automatic context displays the most recent entries
  within a 2,600-character section, including exact path/identity/action/
  evidence and a 16-character response-SHA prefix. Full hashes remain in the
  gateway events and evidence artifacts. Identity checks are pinned separately.
- `query_worldview` selects at most eight facts and uses a smaller rendering
  budget. All `query_worldview` and `get_entity` outputs from one model turn
  share a hard 2,400-character serialized-output budget. The previous
  automatic context is omitted from the carried exchange because a fresh one
  is generated on every turn. The combined fresh automatic context and carried
  worldview results have a 10,000-character enforced ceiling. The automatic
  context retains its 7,500-character ceiling.
- `get_entity` remains available; no extra prompt pressure is added. The
  synthetic range, validator, finding tools, document extractor, model,
  reasoning setting, seeds, budgets, and surface visibility are unchanged.

## Predeclared three-run smoke gates

Run **only three structured vulnerable repetitions** using
`experiments/configs/kimi-k3-structured-m54.yaml`. It differs from M5.3's
config only by experiment name. Retain the saved M5.2 transcript diagnostics
as controls; do not run a new transcript arm or 10+10 comparison.

1. **No identity rediscovery caused by eviction.** For every repeated
   `/api/me` request, inspect the *immediately preceding model request*. If
   it lacks the earlier identity → role/workspace → action/evidence mapping,
   count an identity-eviction failure. A repeat despite that mapping being
   visible is reported as policy redundancy, not memory loss.
2. **No fabricated/truncated typed IDs** in action paths, tool ID arguments,
   or submitted finding assets. Compare finding assets and citations with
   gateway artifacts, and list malformed tool-call rejections separately.
3. **No cross-object evidence associations** in submitted findings. The
   object targeted by a cited action must match the claimed asset; identity
   and workspace-list context actions are allowed as context. Inspect
   before/action/after workflow citations against the same object.
4. **At most one memory-eviction-driven unchanged exact repeat across the
   smoke batch.** An exact repeat has identical method, path, identity, and
   canonical JSON body; unchanged means the response SHA-256 also matches.
   Classify every unchanged repeat from the actual pre-action model input:
   prior state/action visibly sufficient → policy-side; prior state/action
   absent → eviction; a legitimate intervening state check → verification.
   Report each class per HTTP action, without making total repeats a freeze
   gate.
5. **Bounded retrieval carryover.** Each model request must contain no more
   than 2,400 serialized characters of carried `query_worldview`/`get_entity`
   outputs; fresh automatic context plus those outputs must be at most
   10,000 characters. Report the measured values from persisted requests.
6. **Late full model input below 12,000 tokens** in every clean long run,
   using persisted model-call usage. Also report each run's peak.
7. **At least one validated finding in at least two clean runs.** A provider
   failure is kept distinct from agent or memory failure. Fewer than two
   clean runs cannot clear this gate.

If memory-eviction errors disappear, full input stays bounded, and the other
gates clear, freeze M5 even if the model still repeats requests whose prior
state is visibly available. That residual is a policy/orchestration research
question for M6. If a gate fails, preserve and annotate these three traces
before considering any further change.

## Targeted rendering correction after the initial smoke

The [initial batch](diagnostics/kimi-k3-openrouter-m54-initial.md) found that
the 32-entry action index displayed only about ten full-length lines. Older
action/evidence pairs were stored but absent from the model request. The
corrected renderer labels the pinned identities `i0`…`iN`, references those
aliases in compact checked-action lines, and increases that section's cap to
4,500 characters while reducing the recent-detail section to 1,000
characters. It keeps the 7,500-character automatic and 10,000-character
combined memory ceilings. A unit test verifies at least 24 checked actions
remain visible with eight identities. The gates above remain unchanged for
a fresh three-run verification.

The [corrected verification](diagnostics/kimi-k3-openrouter-m54.md) passed
identity retention, typed-ID integrity, retrieval carryover, late input, and
validated-finding gates. It still had three unchanged workspace-list rereads
without the prior exact request visible, and one finding cited a clearly
labeled second invoice as corroboration, which misses the literal same-asset
gate. The gates remain failed in the permanent diagnostic record. M5 is frozen
at code commit `0f27533` as the accepted comparison baseline: bounded history
can evict old checked actions, and the evidence contract lacks explicit
`primary` versus `corroborating` roles. Further Kimi tuning for the three
rereads is outside the baseline.
