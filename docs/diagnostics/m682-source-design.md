# M6.8.2 source and identification design

M6.8.2 is a detached forensic measurement of the 18 historical `start_experiment` proposals in the pinned M6.7.3 cohort. It does not change the agent controller, parser, gateway, provider, range, or historical database. PR #71 (M6.8.1.1) is an unmerged dependency branch; this draft is based on its exact head `9d9c1162d30fabc5cf046a92f3549a20282a7193`.

## Admission boundary

The existing `m673_audit.load_snapshot` verifies the frozen manifest and assignment hashes, source/protocol commits, continuation journal and log, failure receipt, trace digests, model request/response artifact digests, checkpoint receipts, retained reservation, and database inventory using a read-only PostgreSQL connection. The loader stops at cell 118; cells 119–128 remain outside this cohort. This audit then byte-compares reconstructed model-authored proposal rows with the retained private M6.7.3 input, SHA-256 `34bc9b4a9348fcf3a324ea7265a6e8d53d076e5d8e96778354bee1b008093647`. It verifies the unchanged M6.8.1 parser source SHA-256 `f9b81420771e6e8802682aa5dd6383165847f705295f3a15883fc2dae23951d5` and the 18/43/27/4/35 historical counts. Any mismatch aborts.

## Trusted public sources

The pinned enterprise source exposes public GET routes for identity, organization, project, role assignment, access request, change request, environment, job, and audit data. The audit indexes completed GET observations from three provenance classes: controller bootstrap, model-originated HTTP requests, and separate critical-checkpoint setup. It reads the event-bound `RequestArtifact` and `Evidence` files, verifies run/action/request/evidence IDs, range instance and generation, event causation and ordering, path and response SHA-256, HTTP status, and the evidence body digest. Source integrity errors abort. POST response echoes, failed-tool feedback, model-authored text, hidden fixtures, and route placeholders cannot establish R3 public grounding.

Typed objects require a successful, complete public GET response and an `id` in a documented record position. A collection may contain a valid record on a partial page; such a record can be identified, while absence from that page cannot exclude other objects. A foreign-key field is retained as a relationship but does not alone prove that the referenced object was fetched. Free-text UUIDs do not establish entity type. Audit-event records have a numeric sequence key at this source commit, so this resolver does not infer an audit-event UUID from them.

## Model-visible delivery

For model-originated GETs, a matching `function_call_output` in a verified model request must bind the originating tool-call ID, action ID, evidence ID, HTTP status, and exact delivered `body_text` prefix. The output must contain the identifier, and the source event must precede that model request and the proposal. For bootstrap GETs, the identifier must occur in the first verified model request, before any model-authored response. A critical-checkpoint setup run is verified against its receipt and range binding but never automatically counts as delivered; its event sequence is not compared with model-run sequence numbers. An observed identifier in general context remains R2 only until this R3 delivery test passes.

## Public typing, relationship, and oracle isolation

The offline resolver uses only route-specific structural record fields and documented foreign keys. Parser acceptance is independent of type correspondence. The unsupported historical `change:` prefix is treated as a semantic shorthand for the public `change_request` route noun for type measurement only; it remains parser-rejected. `user:` similarly maps to public `identity` for type measurement if encountered. Unknown wrappers have no declared type. Multiple candidate kinds remain ambiguous.

Public relationship edges are drawn only when a verified delivered record has a documented foreign key to another proposed object. Historical hypotheses are free text without a structured predicate-to-edge contract, so keyword overlap never proves hypothesis alignment. The public R0–R6 classification closes before the private fixture and checkpoint anchors are read for the separate `offline_oracle_correspondence` pass. Oracle values cannot make a reference public-grounded or model-visible.

The public aggregate contains counts, hashes, and methods only. Exact references, hypotheses, run/model/action/evidence IDs, source paths, and oracle details are in an ignored mode-0700 directory with a mode-0600 detail file.
