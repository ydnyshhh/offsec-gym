# M6.6 confirmatory v1: stopped after cell 34

The approved 280-cell collection stopped at the first failed paired-stage audit.
This is a retained infrastructure/protocol failure, not a result-based exclusion.
No cell has been retried or replaced, and no further paid model call is authorized
under this frozen execution after the gate failure.

## Frozen provenance and stop point

| Item | Value |
| --- | --- |
| Manifest SHA-256 | `d0f9f516d93008407196165296c5e8ce7d0c4a7fbcc2aed02c14eec3150f61f4` |
| Runtime source commit | `950bdb746e0d8ae9d68a324f58e5b763c7ddbc1d` |
| Protocol commit | `c8125d713ae863a548caddd4a7153e8c280bac5e` |
| Execution guard commit | `435aa3806ca70006f7ac2cfdd9491b6d17c89a54` |
| Private approval receipt SHA-256 | `7d13dfae52fd42d7c85423ad5b47eb4549a7c99e9b68e1ca8eeaa5779a269a07` |
| Journal SHA-256 at stop | `f5b3d4f71b96b97a01056e85df5dc65342f93e67efb546004a2f36cc60bd53de` |
| Completed cells | 34/280, in frozen order, without retry |
| Estimated model-token cost | $15.072354 / $504 approved cap |
| Completed pair receipts | 16; pair 17 has two completed cells but no receipt |

Cells 33 and 34 form Range B vulnerable seed `230173`, control then witness.
Cell 33 (`e073c77bcd21a9ba`) ended `budget_exhausted`, score valid.
Cell 34 (`f5f2775074350644`, run
`919036d3-d1b9-46cb-8c44-5c0b1e52585c`) ended `agent_failed` with
`failure_reason=ValueError`, score valid. Its trace contains one
`WitnessHypothesisStarted`. The collector wrote the cell-34 trace and journal
record, then stopped when the pinned offline stage extractor failed. The
cell-34 stage file and pair-17 receipt were never written.

## Reconciliation

- All 34 journaled source run IDs exist in the dedicated PostgreSQL store.
  Their contiguous event sequences, unique event IDs, and saved trace hashes
  match the authoritative streams. The 34 source traces contain 35,597 events.
  PostgreSQL also contains four separate validator replay runs (38 runs and
  35,673 events in total).
- No journaled run retains an active controller worker, action, coverage lease,
  model reservation, or admission hold. Cells 33 and 34 bind the same build and
  fixture. All 34 journaled scores are marked valid; their terminal statuses
  include ordinary `budget_exhausted` and `agent_failed` outcomes.
- The selected endpoint receipts through pair 17 match Moonshot AI
  `moonshotai/kimi-k3-20260715` at the frozen $3/M input and $15/M output
  prices. The dedicated database remained healthy at the stop.

## Failure mechanism

The pinned `research_ops/m66_confirmatory_stages.py` reproduces the failure
read-only for cell 34, before it writes output:

```text
ValueError: trusted identity response is malformed
```

For a witness hypothesis, the extractor calls `build_reporter_bundle` to
reconstruct witness status. The frozen source runtime also calls that bundle
from `EventWitnessLedger` witness projection. The bundle treats a successful `/api/me`
response as a SaaS identity with singular `role` and optional `workspace_id`.
The authenticated Range B `/api/me` evidence for cell 34 instead has `roles`
and `organization_id`; its identity ID matches the request. The bundle's
`me["role"]` access therefore raises. The trace records only the online
exception class, so the identical online exception message is an inference;
the offline reproduction establishes the schema mismatch directly.

## Consequence

This is a frozen runtime/protocol compatibility defect in the Range B witness
path. The collected 34-cell prefix is retained for audit, but it is neither
the planned 280-cell sample nor a confirmatory treatment estimate. Continuing
would require a reviewed, versioned runtime/protocol amendment and new
manifest/approval provenance. The current collector remains stopped, and the
completed cells must not be replayed or replaced.
