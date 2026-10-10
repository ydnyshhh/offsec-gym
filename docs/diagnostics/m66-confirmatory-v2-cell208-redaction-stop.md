# M6.6 v2: cell 208 offline redaction stop

The confirmatory collector stopped after exactly **208 of 280** frozen cells. Cell 208
(`84757cc11cc22ebc`, patched Range B control) finished as `budget_exhausted` with a
**valid oracle score**. Cell 209 never started. The cumulative estimated model-token
cost is **$91.340037** under the unchanged $504 cap. The journal SHA-256 is
`e0e118e096b931e31fb99cf71c51e9c2140c89bd8e688717923aade8cf4452a3`.
Pair 104 has no receipt, and the cell-208 stage artifact is absent.

## Cause and integrity checks

The frozen stage extractor rejects one request artifact because its
`budget_tokens` field is stored as `"*"`. The gateway's privacy redactor
classified that field as sensitive, while the `action_requested.body_sha256`
event binds the **original** request body. The model response artifact contains
the original `http_request` tool arguments. Its verified response hash, call
identity, tool-call identity, method, path hash, identity, original body hash,
and redacted artifact all agree. No other action in the cell has this mismatch.

The PostgreSQL store has 208 journaled source runs and 16 referenced validator
replays, with no unreferenced run or active action/model reservation. Cell 208
has 1,295 contiguous authoritative events. Its trace SHA-256 is
`93d7ccf7006e76b7599e129b1823802d8f121166ad0a1b6491ccfabbe2bf1470`
and event-identity SHA-256 is
`1b881777b8ea6ef5b1c6b4887f2bc887ea772b223937a58ee3aa24d638f7ba66`.
The source run, oracle score, prior failures, and earlier study evidence are retained.

## Proposed offline amendment

`m66_cell208_redaction_offline.py` is pinned to this one cell and the frozen
protocol commit. It first reproduces the frozen extractor failure. It then
verifies the private model response artifact against its event hash and
substitutes the original request body **in memory only**, twice: once for the
proof-action ledger and once for the observed-request ledger. The frozen stage
predicates run on that verified body. The reconstructed stage bytes have SHA-256
`83141f46b1393e678d4ffb99933ceba7f20bfa1b3776727ea897f59fbcedf27e`.
The redacted artifact remains untouched.

The proposed continuation wrapper would create the stage, amendment receipt,
and pair-104 receipt only after a **separate exact approval**. It binds the
208-cell journal, original and metadata approvals, prior pair/endpoint
receipts, source run identity, cumulative cost, and selected endpoint. It
passes a full authoritative PostgreSQL postcheck before admitting cell 209.
Its approval pin is all zeros, so it cannot run now. The proposed approval
scope is only assigned cells **209–280 once**, without retry or replacement,
under the original **$504 cumulative** cap. The provider-health epoch that
began at cell 189 is preserved; there is no new reset at 209.

## Rehearsal and limits

A temporary-copy rehearsal reconstructed the stage twice with identical bytes,
closed pair 104, and passed the full 208-run PostgreSQL postcheck. The paid
collector was replaced with a forced stop. The original journal, stage
directory, and pair receipts were not written. This exception is post-start
measurement repair for one redacted field. It cannot be treated as a new
prospective protocol or as evidence for an unrun cell. The 208-cell prefix is
not a complete confirmatory sample and has no treatment estimate.
