# R0.2b offline provenance contract

This validator checks retained exports without starting a range, agent,
gateway, or provider. All status values describe evidence available to the
offline importer. `verified` means the specified relationship is internally
checked; it does not authenticate the export or establish a model result.

## Identity chain

| Identity | Source | Retained evidence | Limit |
| --- | --- | --- | --- |
| Benchmark task ID (`t0-…`) | `BenchmarkTaskSpec` and `make_task_manifest` | `RetainedRunMetadata.task_id` | Recomputed for the frozen seed and variant; metadata alone does not bind a runtime task. |
| Runtime task UUID | `AgentTask.task_id` | Optional `ModelCallStarted.task_id` and terminal task ID | Event-internal consistency can be checked; no benchmark mapping is emitted by the current controller. |
| Run UUID | `AgentContext.run_id` | Every trace event | Events must share one run ID and have one ordered start and completion. `RunEvaluation` does not carry a run ID. |
| Model-call UUID | Created per turn | Started and terminal events | Each call must have one start and one terminal event, in order. |
| Request artifact ID | `ModelCallArtifacts.write` | `ModelCallStarted.request_artifact_id` and optional exported artifact | Event reference alone is insufficient; a retained artifact is checked by ID, run, call, kind, and recomputed payload SHA-256. |

`analyze_run` computes descriptive trace metrics and some event integrity
checks. `read_retained_run` now runs `audit_retained_provenance` before analysis
and rejects any `mismatch`. It retains the structured audit inside
`OfflineRunRecord.provenance_audit`, which is serialized into private
`pilot_runs.jsonl` and paired-cell records. The report remains unqualified.
Metadata fields such as source commit, visibility hash, tool hash, and budget
hash remain declarations unless checked against an independent source.

## Optional retained evidence

For a prefix such as `cell-42-vulnerable`, request artifacts are read from
`cell-42-vulnerable.requests/<call UUID>.json` using the existing
`ModelCallArtifacts` record shape. The pilot protocol version is distinct
from each event's schema version. An export declaring
`r0-a1-model-pilot-v2` requires every model-call start (and every model-call
completion, when present) to use schema version 2. Every start must reference
a retained request artifact whose IDs and recomputed payload SHA-256 match.
Legacy-only or mixed-schema calls, missing files, altered payloads, and
contradictory IDs block import. An export declaring the historical
`r0-a1-model-pilot-v1` can retain schema-version-1 calls without request
references, with `request_artifact_integrity = not_evaluated`. No event or
artifact is upgraded or synthesized during import.

An optional `cell-42-vulnerable.binding.json` contains exactly
`schema_version`, `benchmark_task_id`, `runtime_task_id`, and `run_id`.
`read_retained_run(..., independent_binding_sha256=...)` accepts its SHA-256
only from the caller, outside retained metadata. Without that independent pin,
the file cannot establish runtime-task linkage. The pinning authority must
obtain and authenticate this digest separately; no current production export
provides such evidence. A synthetic test demonstrates the conditional check,
not production qualification.

## Status boundaries

- `benchmark_manifest_consistency` checks the frozen seed/variant task ID.
- `protocol_event_schema_consistency` checks the declared pilot protocol
  against retained model-call schema versions. For v2, a legacy or mixed
  model-call trace is a mismatch even when its artifacts are intact.
- `model_call_run_linkage` checks event sequence, run, call, runtime UUID,
  provider, and model consistency.
- `request_artifact_integrity` checks exported request records against event
  references and recomputes payload SHA-256. It does not prove dispatch.
- `runtime_task_identity_linkage` is `not_evaluated` without an independently
  pinned binding. A contradictory pinned binding is `mismatch`.
- `scoped_task_parity` detects a generic or incorrect objective in an intact
  request artifact, but remains `not_evaluated` on a matching objective because
  that is not proof of the complete model-visible contract.
- `production_request_parity` and `runtime_dispatch_parity` remain
  `not_evaluated`; neither a retained file nor metadata proves those scopes.

The current controller still creates a fresh runtime task UUID and generic
objectives. There is no authenticated benchmark-to-runtime task-binding
receipt, no qualified full production request for the six A1 cells, and no
runtime-dispatch evidence. R0.2 remains incomplete. The frozen v2 protocol,
source pins, controller, agent, provider, gateway, and `--execute-models` block
are unchanged.
