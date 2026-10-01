# Milestone 6.1.3: worker objective contract

The [M6.1.2 diagnostic](diagnostics/kimi-k3-openrouter-m612.md) remains frozen,
including its failed objective-action and refund gates. This is the final
sequential-worker repair. It changes the execution contract, while retaining
the same six objectives, order, global and rolling budgets, packet selection,
model, provider, range, surface, and validator.

Each packet now carries a typed `WorkerTaskContract`: objective, exact route
family, target entity types, permitted methods, state-change authorization,
success condition, and one orientation turn. The worker may inspect its packet
and use retrieval tools during that turn. If it has not executed an objective
HTTP action, the next model call exposes only `http_request` and
`task_blocked(reason, missing_prerequisite)`. The harness rejects a request
outside the assigned route at that stage. A worker may continue evidence
analysis and finding submission after an objective action. `WorkerOriented`,
`WorkerObjectiveAction`, `WorkerBlocked`, and `WorkerContractViolated` make
these states visible in the event stream. Event replay requires a contracted
worker to have an outcome before finishing. An unattempted objective is a
contract failure, not successful worker completion. Only the refund worker
may use POST; its state change is authorized within this synthetic range.

The route match is deliberately strict: identity `/api/me`, document detail,
invoice detail, ticket detail, public invoice preview, or refund POST.
Preparatory list reads do not satisfy the assigned test. A blocked event is a
typed model report, not proof that its claimed prerequisite was truly absent;
the diagnostic audits that claim against the packet.

## Predeclared three-run smoke

Run three vulnerable repetitions using the unchanged
[`kimi-k3-structured-m61-workers.yaml`](../experiments/configs/kimi-k3-structured-m61-workers.yaml)
configuration, Kimi K3 high reasoning, Moonshot AI upstream, fallback off,
seed 1, range seed 42, known routes, deterministic validation, and the same
60 action / 60 HTTP / 20 model-call / 120,000 token global budget. Evaluate
these gates before seeing the traces:

1. All six workers receive at least one model call in every run.
2. No worker finishes with a retrieval-only trajectory. Each objective has a
   matching dispatched HTTP action or a typed `WorkerBlocked` with a concrete
   missing prerequisite. Contract violations are reported as failures.
3. Before its first objective action, a worker has at most one retrieval-only
   model turn. The action-required call must lead to a matching action or
   explicit block. Count preparatory HTTP separately.
4. The refund worker sends a POST in at least two of three runs. Otherwise,
   inspect and report its explicit blocked reason, including whether the
   purported missing prerequisite was actually present.
5. Document and ticket workers with targets in their handoff packets either
   read a target detail route or explicitly block. Audit any block against
   those packet targets.
6. Peak input context is at most 10,900 tokens; no controller budget, lease,
   ownership, or provenance leak occurs, and event replay matches the ledger.

Record validated root-cause recall, HTTP actions, duplication, worker overlap,
and blocked reasons without a score gate or a statistical comparison. Freeze
the sequential behavior after this batch even if score is poor, preserving
failed gates exactly. Defer parallel M6.2 until its independent concurrency
blockers are resolved.
