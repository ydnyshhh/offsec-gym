# M6.7.3: separate prospective experiment proposals

Status: **design and offline qualification only**. None of these protocols is frozen, authorized for paid execution, or a modification of M6.7.1. All historical cells and reservations stay as recorded. A later protocol PR must pin source, compiler, model endpoint, prompt/tool schemas, seeds, order, pricing, budget, stop rules, and exact code hashes before any live run.

## Shared design and admission rules

Fresh seeds come from a precommitted hash of a future public protocol identifier and fixed seed range, before any outcome inspection. Exclude every historical M6.7.1 seed and any seed selected for a known positive outcome. Pair vulnerable and patched variants on the same fresh seed, checkpoint, and arm; interleave arms with a frozen schedule. Every primary outcome uses **all assigned cells**. An unstarted cell, provider failure, environment failure, or unauditable trace is explicit missingness, never scored as failure or success. No retry or replacement of a started cell. Use a small balanced feasibility cohort; effect sizes remain descriptive until a separately powered protocol is reviewed.

The future controller must preserve synthetic-range gateway boundaries, record model-visible request/response hashes, tool-call IDs, source trace and checkpoint hashes, decision opportunities, provider usage, and endpoint health. Preflight requires a no-model public-information path, no hidden anchor in any model-visible request, and exact source/protocol pins. Stop on provider drift, unauditable source events, unexpected reservation state, cap pressure, or contaminated randomization. A later paid approval must specify a conservative cumulative estimated token cap; the ceiling is `assigned cells × per-cell input/output token ceilings × pinned prices`, plus retained reservations and prior study charges when sharing the cap. These proposals do not reserve funds.

For scale only, applying M6.7.1's **$1.80 per-cell ceiling** to the proposed sizes yields E1 $57.60, E2A $86.40, E2B $57.60, and E3 $115.20. Applying that deliberately generous per-call ceiling to E4's 56 proposed requests yields $100.80. These figures are independent planning ceilings, not a combined budget or paid authorization. A future protocol must recalculate from then-current pinned model pricing, input/output token ceilings, and any cumulative prior charges before seeking approval.

## E1 — Argument representation and experiment activation

- **Question and alternatives:** Does a typed UUID boundary suppress model-authored experiment activation? A tolerant parser could raise acceptance while public object grounding and subsequent action remain unchanged.
- **Intervention:** Control uses the existing UUID-valued `target_objects`. Treatment stores opaque model-authored strings; a structured `{kind, value}` arm is a separate later contrast, not silently pooled. It records a plan without resolving hidden IDs or auto-filling a target. All other tools, model, task, checkpoint, budgets, and reasoning settings are identical. The tool schema/token overhead is measured.
- **Cohort:** Exploratory 16 fresh seed × checkpoint/variant pairs, one control and one treatment assignment per pair, **32 cells**. Balance B2/B3 and vulnerable/patched across pairs. Freeze seed and schedule before run.
- **Primary:** `experiment_state_created / all assigned B-enabled cells`. Secondary: proposals, parse acceptance among proposals, reference grounded in model-visible public data, unique reference, later retrieval/update, planned step attempted, relevant action, final report, token overhead. Uptake is a model-originated proposal; activation requires an authoritative state event.
- **Observability/missingness:** A parser acceptance is never a correct-object or security success. An unresolved opaque reference remains opaque. Require verified request, response, rejection/state event, and later retrieval provenance. Missing outcomes are separate.
- **Go/no-go:** Offline schema replay must accept synthetic wrapped references but reject invented references as grounded; no private resolver and no hidden root labels in treatment. Advance only if activated plans and later use are measurable without contaminating task information. Provider, cap, no-retry, and source hash rules above apply independently.

## E2A — Deadline slack only

- **Question and alternatives:** Are B3 discovery and cancellation constrained by two HTTP opportunities? More slack may improve discovery/action without changing the agent's planning ability.
- **Intervention:** Three future versioned clock variants with 2, 4, or 8 HTTP completions before due. Public information, routes, checkpoint state, model, task, and all non-clock tool behavior stay identical. The future range compiler must explicitly pin `queued_tick`, `due_tick`, request/completion tick increments, and worker progression; frozen B3 is never edited.
- **Cohort:** Exploratory 8 fresh seeds × 3 clock arms × 2 variants = **48 cells**, paired by seed and variant and scheduled in balanced order.
- **Primary:** Cancellation committed before due among all assigned cells. Secondary: first action category, target-related discovery, candidate count, cancel proposal/request/completion, job result, downstream environment effect, full verified transition, HTTP/token overhead. Uptake is use of added opportunities for task-related observations/actions.
- **Observability/missingness:** Require each clock event and model-originated request in the source trace. An oracle-supplied target path is only physical reachability. A public-only scripted discovery/action path must qualify before live execution. Missing source/clock evidence is unknown.
- **Go/no-go:** A new deadline variant must preserve the same public data and gateway rules and pass synthetic clock tests at boundary ticks. Do not combine with queue summaries. Apply shared stop, cap, pin, and no-retry rules.

## E2B — Public information only

- **Question and alternatives:** Can a generic public queue representation make candidate objects identifiable without consuming the whole deadline? Improved candidate selection may occur without improving action choice.
- **Intervention:** Control retains existing public routes. Treatment exposes a generic queue list through an ordinary public read/snapshot available to all roots with the same authorization and pagination rules. It contains public IDs, status, and relationships only; no hidden target flag, correct cancellation action, root label, or oracle field. Clock remains at the separately frozen two-opportunity level in both arms.
- **Cohort:** Exploratory 8 fresh seeds × 2 information arms × 2 variants = **32 cells**, paired by seed and variant.
- **Primary:** Correct candidate identified **and** cancellation committed before due among all assigned cells. Secondary: number of public candidates, model-selected candidate, HTTP discovery calls, cancellation request/completion, downstream effect, full transition, tokens. Uptake is actual use of the supplied public queue data, verified in the model-visible request/action chain.
- **Observability/missingness:** Public snapshot content and authorization must be archived verbatim and hash verified. When multiple candidates remain equivalent, classify ambiguity; never choose using a private anchor. A public-only policy must qualify the discovery/action path for every planned task stratum.
- **Go/no-go:** Negative controls with two indistinguishable jobs and absent/late listing must remain ambiguous; public listing must not leak oracle labels. Keep this treatment separate from E2A. Apply shared stop, cap, pin, and no-retry rules.

## E3 — Model-authored evidence closure

- **Question and alternatives:** After a relevant mutation, does generic post-action verification support increase a trusted observation chain? A reminder may cause extra unrelated reads without collecting the discriminating observation.
- **Intervention:** Control leaves post-action behavior unchanged. Treatment gives only a generic reminder after a completed state-changing HTTP action to specify a falsifiable postcondition and choose an observation. It does not name approval, cancellation, target, identity, root, expected answer, or endpoint. A structured postcondition tool would be a **separate** future arm, not bundled here. Model, routes, state, and budgets otherwise match.
- **Cohort:** Exploratory 8 fresh seeds × 2 reminder arms × 2 checkpoints × 2 variants = **64 cells**; pair within seed/checkpoint/variant. This is a proposal, not a powered sample.
- **Primary:** Relevant action **and** required later observation among all assigned cells. Secondary: action completion, correct object/identity read, before-terminal timing, frozen verified transition, witness/report, extra reads, tokens. Uptake is the model's explicit postcondition or chosen read. `P(observation | action)` is descriptive only because action is post-treatment.
- **Observability/missingness:** Only model-originated read requests with verified response count. Reminder delivery is not observation. Wrong identity/object, cutoff, blocked or insufficient read receive separate codes; missing evidence remains unknown. The frozen M6.7.1 outcomes remain unchanged.
- **Go/no-go:** Synthetic controls must show no automatic target read, no hidden root text, no credit for unrelated reads, and no repeated unchanged-state credit. Apply shared stop, cap, pin, and no-retry rules.

## E4 — Controlled context sensitivity diagnostic

- **Question and alternatives:** Does exact output repetition persist when an archived synthetic model request receives an explicitly relevant context change? Repetition could reflect provider/model behavior, generic policy, or poor contextual salience; the design cannot isolate hidden server state.
- **Intervention:** A separately approved capped provider diagnostic would compare exact serialized replay, transport metadata only, irrelevant observation, exact same-object status, authorization, deadline, and shorter context with latest relevant observation. Each condition changes one factor. M6.7.3 only generates static detached requests; no provider call is made.
- **Cohort:** Future exploratory 8 fresh synthetic base requests × 7 conditions = **56 calls** maximum, independently capped and randomized before responses. Historical positive cases are not seeds. These are request-level units, not M6.7.1 cells.
- **Primary:** Exact repeated output on all assigned request pairs. Secondary: tool-batch identity, selected action change, acknowledgment of relevant observation, invalid-call rate, usage and latency. Uptake is a visible response/action difference tied to the changed fact; no output suppression or deduplication.
- **Observability/missingness:** Archive exact wire request/response, resolved model revision, endpoint, token usage, and transport metadata. Provider failure is missing, not a different policy. Context relevance is specified prospectively by an exact object/route relation, not by hindsight.
- **Go/no-go:** Static perturbation tests must show exactly one intended field changes and archived requests remain immutable. A later provider study requires fresh endpoint health/price checks, explicit paid approval, and a cost cap; never infer caching from repetition alone.

## Causal separation

E1 manipulates parsing; E2A the clock; E2B public information; E3 post-action evidence selection; E4 request context. None is a combined "better agent" treatment. Each is a distinct future protocol and each must report assignment, uptake, missingness, and primary outcome separately.
