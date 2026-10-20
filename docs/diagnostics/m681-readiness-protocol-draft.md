# M6.8.1 readiness review and prospective protocol draft

**Status: draft for independent review. No runtime treatment, live manifest, paid approval, or model run exists.** This document does not authorize collection. It applies only to the proposed M6.8.1 reference-normalization comparison; M6.7.1 and the other M6.8 studies remain separate.

## Evidence available now

| Item | State | Evidence and limit |
| --- | --- | --- |
| Controller call chain | Audited | [`m68-interface-design-audit.md`](m68-interface-design-audit.md) traces provider schema, response parsing, typed validation, experiment-state event, tool feedback, and later model request. |
| Detached grammar | Implemented offline | `m681_reference_syntax.py` SHA-256 `ec301c812be9cb21472ae0c80c287be93862376c58443b364be3e82835af7371`; it is not invoked by the controller. |
| Historical replay | Complete offline | [`m681-historical-replay.json`](m681-historical-replay.json) SHA-256 `34db85c2e7f9ce7f153ddbb2ff6035710e6f43e5114a6803102766315e7dd2dc`. Four of 18 complete historical proposals passed the fixed syntax and subsequent schema. This is not a treatment effect. |
| Runtime control/treatment comparison | Missing | No opt-in treatment is connected to the controller. The provider-facing schema has not been compared across runtime arms. |
| No-model persistence qualification | Missing | Offline parser tests do not establish an authoritative state event or later retrieval through the controller. |
| Prospective protocol and approval | Draft only | No fresh assignments, execution source pin, spending cap, or M6.8.1 approval is frozen. |

## Runtime change requiring separate review

The proposed contrast changes only whether a prespecified, lossless public reference form can pass the existing typed argument boundary. The control must preserve the original validation result. A future treatment must be opt-in and disabled by default. It must preserve the model-authored UUID exactly, reject ambiguous or unsupported syntax, and perform no lookup, target substitution, or hidden-fixture access. The provider-facing tool definition and descriptions must be byte-identical across arms in a serialized request comparison. Any difference there changes the experimental intervention and requires a revised protocol.

The reviewer should inspect the complete source diff and test evidence before a merge. The review must establish that the change cannot alter non-experiment tools, ordinary HTTP behavior, model-call reservation accounting, state transaction semantics, invalid-call limits, or frozen historical artifacts. Syntax acceptance alone is never evidence of public provenance, object resolution, or correct-target selection.

## No-model qualification evidence required

These are acceptance criteria for a separately reviewed implementation. The present draft supplies no passing runtime result.

| Case | Required observation |
| --- | --- |
| Strict control | A canonical UUID follows the original validation and state path; its persisted event and returned view agree. |
| Wrapped control | The same typed reference is rejected with the treatment disabled, preserving historical behavior and rejection accounting. |
| Wrapped treatment | The exact UUID survives the syntactic adapter and existing typed validation; an authoritative state event is present, and a later controller turn can retrieve the same experiment state. |
| Ambiguous reference | Unsupported kind, extra text, multiple identifiers, and noncanonical form fail before state creation. No inferred substitute is emitted. |
| Exposure equality | Serialized provider-facing tool schemas and descriptions match between arms; only the intended controller-side acceptance differs. |
| Isolation | A failed or terminal run cannot create a state event; experiment state remains associated with its own run and does not cross independent histories. |

Use synthetic range data and zero provider calls. Record case-level pass/fail evidence and exact source hashes. A unit test of the detached parser does not satisfy this gate. If any case fails, revise the source and repeat qualification before considering a prospective manifest.

## Proposed E1 protocol for review

**Question.** Does lossless reference syntax normalization increase all-assigned model-authored experiment activation? Parsing, public grounding, later state use, and downstream action are distinct outcomes.

**Design.** The existing proposal is 16 fresh matched blocks, each assigned once to strict control and once to treatment, for 32 excluded feasibility cells. Balance B2-S3 and B3-S2, vulnerable and patched, with four blocks per combination. Each assigned cell needs an independent range instance and model history. Fresh eligibility, assignment, and order must be fixed prospectively without selecting on prior outcomes. This is a proposed size and structure, not a frozen manifest.

**Primary outcome.** `experiment_state_created` requires an authoritative persisted state event. The denominator is every assigned cell. A cell with no model-authored proposal is non-uptake and has no activation; a provider/environment failure or unauditable trace is reported separately as missing evidence. A started cell is never retried or replaced to improve the result.

**Separate secondary stages.** Report proposal, JSON parse, assigned syntax acceptance, typed validation, controller rejection, state creation, later retrieval/update, independently verified public-source grounding, declared object-type resolution, model-authored planned action, authoritative action completion, required observation, and verified transition separately. Unknown provenance or correspondence stays `null`. Model-marked completion does not establish a verified transition. Report model-call, token, and HTTP overhead independently.

**Analysis.** Estimate paired activation differences at the matched-block level and present checkpoint/variant strata separately. Any uncertainty interval is descriptive for a small feasibility cohort. Do not pool unrelated security-success outcomes or stop based on the observed direction.

**Admission and stop gates.** Before collection, independently review and merge the runtime source; pass the no-model matrix above; pin exact execution-critical source, tool schema, model/provider, protocol, and analysis definitions; verify retained historical evidence and reservation state; calculate a conservative capped cost from current pinned pricing; and obtain a distinct exact-scope human approval. Stop on source or provider drift, corrupted evidence, unexpected accounting state, or cap pressure. The existing M6.7.1 receipts do not authorize M6.8.1.

## Decision requested from reviewers

1. Confirm or amend the proposed contrast and public kind allowlist before runtime work is merged.
2. Review the source change and no-model case evidence as a separate gate. Record any model-facing difference explicitly.
3. Review a fully pinned prospective protocol and cost calculation only after qualification. A later human decision must approve the exact paid scope before any run.

**Current decision: no-go for live M6.8.1 collection.** The runtime and no-model gates are open. This document is a review artifact and does not create or consume an approval receipt.
