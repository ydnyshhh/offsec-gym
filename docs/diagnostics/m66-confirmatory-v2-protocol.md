# M6.6 confirmatory v2 protocol

**Status: protocol preparation. No v2 manifest or paid-call authorization yet.**
The approved v1 collection stopped after 34 of 280 cells when a Range B
witness encountered an incompatible `/api/me` identity shape. Those 34 cells,
including the failed cell 34, remain in the [v1 stop record](m66-confirmatory-v1-stop-after-cell-34.md).
They will not be retried, replaced, or pooled into a v2 confirmatory estimate.

## Change from v1

The qualified runtime source changes only to the reviewed Range B witness
projection fix: witness status can be reconstructed from Range B `roles` and
`organization_id` evidence without requiring the SaaS `role` and
`workspace_id` summary. Action, evidence, time-order, and tamper checks remain
required. A synthetic Range B test and read-only replay of the retained cell
34 trace establish compatibility; no paid feasibility call is used for this
qualification. The final manifest must identify the full merge commit of this
fix as `source_commit`.

The version label is `m66-confirmatory-v2`. The v2 exclusion registry includes
all 162 prior excluded seeds **and all 120 seeds assigned in v1**, whether or
not their cells ran. Thus every v2 seed is newly assigned. The registry binds
the frozen v1 manifest and stop record by SHA-256. Seed selection uses the same
SHA-256 algorithm with the v2 label; no fixture or outcome is inspected to
select seeds.

## Fixed experiment

The research question, root ontology, both ranges and patched variants,
known-routes visibility, high-reasoning Moonshot AI Kimi K3 revision, model
prompt and tools, deterministic validator, 120,000-token and 20-call cell
ceilings, bootstrap budgets, and witness intervention remain as in the
[predeclared confirmatory design](m66-confirmatory-protocol-design.md).
There are 60 fresh vulnerable seed pairs per range and 10 patched pairs per
range: 140 pairs, 280 cells in one frozen interleaved order. Control and
witness share a fixture per pair. The same predeclared terminal-bounded
extraction, all-assigned root contrast, seed-cluster bootstrap, patched false
submission count, missingness bounds, and provider-health gates apply.
Changing any of these after manifest freeze requires a new version.

The proposed cumulative estimated model-token stop is **$504**, the
conservative 280 × 120,000 × $15/M output-rate bound. It is a ceiling, not an
expected bill or an authorization. Endpoint availability and the $3/M input,
$15/M output price require a new live public metadata check at freeze time.

## Freeze and execution boundary

1. Merge the tested Range B fix after exact-head CI. Pin its full merge SHA as
   the qualified runtime source. Keep the v1 stop and old execution guard
   closed.
2. Review and merge this v2 protocol after focused tests and exact-head CI.
   Pin its full merge SHA as `protocol_commit`. The v2 protocol includes the
   new seed registry, configs, collector/audit/extractor/analyzer code and
   source file hashes, including the witness packet builder.
3. From a clean checkout at the exact protocol commit, perform the public
   endpoint/price check and create `m66-confirmatory-v2.json` **once**. Record
   its SHA-256. Its `paid_model_calls_authorized` value stays `false`; the
   collector's manifest and approval hash pins stay zero and fail closed.
4. Request a new, separate explicit approval bound to that exact manifest SHA
   and $504 stop before creating a private approval artifact or making any
   paid model call. Any execution guard change requires its own reviewed CI.

The old v1 approval names the old manifest and does not authorize v2.
