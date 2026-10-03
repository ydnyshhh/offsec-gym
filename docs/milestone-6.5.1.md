# M6.5.1: prospective witness recovery from a frozen monolithic probe

**Status:** implemented and predeclared; no M6.5.1 paid model call or pilot has run.
This is a separate protocol from the completed [M6.5 common-bootstrap control](diagnostics/m65-common-bootstrap-control-results.md). Its sample has new seeds. Completed M6.4 and M6.5 manifests, analyses, traces, scores, and published hashes remain unchanged.

## Question and interpretation

Given a closed probing trajectory and only its trusted action/evidence record, how often can a fresh read-only reporter submit a distinct validated root cause that the probing agent missed? The reporter gets extra inference compute. Recovery establishes reportability from existing evidence; this assay cannot show superiority to an equally funded integrated continuation. A null recovery result is valid.

Each paired `tenant_boundary_v2` seed has one vulnerable and one fully patched build. The main sample contains ten new seed pairs (20 cells). The separate two-cell pilot is excluded from the primary sample. A deterministic SHA-256 selection over protocol label, source commit, and counter produced the seeds before any of those builds were compiled. The committed [seed declaration](../experiments/manifests/m651-witness-seed-selection-v1.json) is the first freeze; only afterward did the planner verify paired build IDs and fixture hashes for the [full manifest](../experiments/manifests/m651-witness-recovery-v1.json). It did not inspect root outcomes or select on fixture properties. Cell order is deterministically shuffled, independent of build contents.

## Stage A: frozen probe

Use the M6.5 common-bootstrap structured-memory monolithic agent with its original prompt, tools, known-route visibility, Kimi K3 high-reasoning request, 17-action deterministic prerequisite bootstrap, validator, and 120,000 model-token / 20-call / 60-action / 60-HTTP / 8,192-output-token-per-call / 900-second limits. Its integrated findings are retained exactly as submitted. Its agent task keeps the 120k cap even though the controller declares a separate reporter allowance. Once the probe exits, the event prefix and canonical reporter packet are hashed and persisted before reporter inference. No validator result enters that packet.

## Stage B: fresh read-only reporter

The reporter starts a fresh context with a compact ordered action index and existing Stage A candidate claims. The canonical, versioned evidence packet includes source run/build/experiment and trace hashes, request/evidence IDs, gateway sequence, method/path, acting identity, visible `/api/me` role/workspace facts, bounded request body, status, response excerpt/hash, request fingerprint, entity association, and prior candidates. Requested actions that ended blocked, failed, or without response evidence appear as distinct unobserved attempts; they are never citable proof. Exact trusted response bodies are retrievable by evidence ID. The bundle is reconstructed from the source run's verified request/evidence artifacts and authoritative events; it cannot serialize hidden fixture/oracle material, variant labels, validator decisions, scores, expected root count, or model private reasoning. The exact packet hash is in `ReporterStarted` and a restricted local packet artifact.

The model tool registry contains only `get_evidence`, `get_entity`, `search_evidence`, three typed `submit_*_finding` tools, and `finish_report`. Unknown tools, including HTTP requests, produce a typed rejection. It has no gateway/runtime/reset/shell/network capability. The controller-bound finding sink rejects citations outside the frozen packet. Every retrieval and finding has a typed event with reporter/task/source attribution; model calls use reporter correlation IDs and separate token accounting. Findings use the ordinary deterministic validator and unchanged transition replay.

The reporter prompt is hashed in the manifest. It asks for directly evidenced vulnerabilities, requires same-identity ordered before/action/after evidence for transitions, warns against inventing absent fields or treating expected authorization behavior as a finding, and allows an empty report. It never names configured hidden roots or a patch state.

The reporter allowance is 30,000 model tokens, four calls, 4,096 output tokens per call, 300 seconds, 24 retrieval calls, and 12 finding submissions. This is **provisional pending the non-sample pilot**. It is charged separately from Stage A. The combined controller reservation is 150,000 tokens and 24 calls. Any pilot-driven amendment must be limited to feasibility, committed and repinned before full collection, and cannot depend on whether the pilot found a vulnerability. Do not run the full sample until that gate is resolved and its separate paid cost decision is made.

## Prospective stages and outcomes

For each of the five configured roots in each score-valid vulnerable run, the offline root-stage audit records: relevant action attempted; relevant action completed with response evidence; relevant success; complete trace proof; integrated matching submission; integrated citation proof; integrated validation; reporter matching submission; reporter citation proof; reporter validation; and incomplete or wrong cited proof. Monolithic worker `READY` and `ADMITTED` are inapplicable. `Complete trace proof` means authenticated actions satisfy the deterministic validator's response and relation requirements. It does **not** mean the model noticed them, and it does not imply a transition passed clone replay. Hidden oracle and fixture inputs exist only in this offline audit module, never in reporter packet construction.

Refund receives a dedicated record: target invoice known; relevant member refund attempted; successful unauthorized transition; same-identity paid before-read; same-identity successful refund; same-identity refunded after-read; complete `before.sequence < refund.sequence < after.sequence` witness; integrated submission and cited proof; reporter submission and cited proof; and final deterministic validation. A rejected matching citation is classified conservatively as wrong identity, temporal order, missing before/after, failed/wrong transition, unrelated invoice, or other proof failure.

The primary denominator is **recoverable missed roots**: complete Stage A trace proof with no distinct integrated validated root. The primary outcome is distinct roots newly validated from reporter submissions divided by that denominator. The analysis also reports `P(integrated validated | complete proof)`, per-root conditional recovery, and a 10,000-resample interval using seed pairs as the resampling unit. A zero denominator produces an undefined rate, not a fabricated zero.

Secondary outputs keep submissions, validated submissions, distinct roots, duplicate roots, same-root alternate witnesses, rejected findings, patched false findings by root where uniquely attributable, exact repeated Stage A HTTP attempts, probe/reporter calls and tokens, combined cost, roots per 100k reporter tokens, and unrecovered proof-capable roots separate. Patched reporter false claims are classified where evidence allows into contradicted response, expected authorization behavior, or other/unclassified. The offline analysis does not force ambiguous cases into a specific class.

## Hypotheses, failure handling, and gates

- **H1:** Static read/exposure proof-to-integrated-finding conversion exceeds refund conversion.
- **H2:** A fresh read-only reporter recovers some proof-capable roots that Stage A did not validate.
- **H3:** Refund losses occur substantially during witness construction or cited-evidence assembly.
- **H4:** Recovery can reduce proof left unused while adding duplicate or patched false claims.
- **H5:** Reporting compute changes conversion, not exploration, because it cannot dispatch an action.

These are hypotheses, not pass gates. Probe and reporter provider failures are journaled without automatic retry. An interrupted cell requires event-store reconciliation before any decision about continuation. The collector fails closed on manifest/source/config/build drift, selected endpoint drift, bootstrap mismatch, active controller reservations, reused run IDs, trace/packet hash mismatch, score replay disagreement, and the cumulative cost stop. Terminal-cause attribution distinguishes call/token/action/HTTP/wall limits, repeated tool rejections, provider failures, and unknown cases when mechanically available. It does not retrofit causes into completed historical studies.

The [preflight record](diagnostics/m651-witness-recovery-preflight.md) gives exact hashes, tests, context measurements, cost bounds, and prepared commands. Neither live command has been executed.
