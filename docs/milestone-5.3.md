# Milestone 5.3: working set and detail retention

M5.2 recovered role, workspace, object, and citation state, but its final
structured runs still made 16 exact repeated requests in 116 actions. Ten
returned unchanged responses. M5.3 is a bounded follow-up to retain the
details and action/evidence pairings that those traces showed were missing.
The [M5.2 diagnostic](diagnostics/kimi-k3-openrouter-m52.md) remains unchanged.

## State changes

The route-aware response extractor now retains at most 512 characters of the
`body` field from document and ticket details as `body_excerpt`. If a body is
longer, `body_truncated=true` makes the limit explicit. A UUID appearing after
the literal word `invoice` in those synthetic body fields becomes a typed
`mentions_invoice` relation. This says the response text mentioned the ID; it
does not assert invoice ownership or hidden ground truth. Extraction still
requires a complete HTTP 200 response, a matching object ID, and a verified
response hash. No ticket requester relation is invented because the range's
ticket schema has no requester field.

The monolithic structured agent keeps a transient active set of the five most
recently acted-on entities, with at most four action/evidence pairs per entity.
The automatic context groups each action under its exact target entity and
retains before/after response facts even when the current worldview has
superseded an earlier status. Non-200 responses can appear as gateway action
history with their HTTP status and evidence pair; they produce no observed
response facts. Working-set rendering is capped at 3,500 characters and 18
facts within the overall 7,500-character context bound.

The structured tool `get_entity(entity_type, entity_id)` performs an exact UUID
lookup. It returns the entity's current controller-observed attributes and
bounded evidence groups, each with its own action ID, evidence ID, fields, and
historical status. It does not use free-text matching or model-authored claims.
Older facts remain in the event-backed ledger after they leave the active set.

## Predeclared three-run gates

Run **only three structured vulnerable runs** using
`experiments/configs/kimi-k3-structured-m53.yaml`. It differs from the M5.2
structured config only by name; the model, high reasoning setting, pinned
Moonshot AI upstream, seed, range, visibility, and budgets stay fixed. The
existing transcript diagnostic is the control; no transcript rerun or 10+10
expansion is planned here.

1. **Unchanged exact repeats below 5%:** same method, path, identity, and JSON
   request body as a prior action in the run, with the same response SHA-256.
   Count all completed HTTP actions in the denominator. Report state-changing
   exact rereads separately.
2. **Zero repeated `/api/me` calls** for the same identity within a run.
3. **Zero fabricated or truncated UUIDs** in submitted finding assets or tool
   arguments. Record rejected malformed tool calls separately.
4. **Zero cross-object evidence associations:** every finding evidence action
   that targets an object must target the finding's claimed asset, except
   clearly labeled context actions such as `/api/me` or workspace listings.
   Manually inspect transition before/action/after references.
5. **At least one validated finding in at least two of three runs.**
6. **Late input context below roughly 12,000 tokens** for each long run,
   measured from persisted model-call usage.

If all gates clear, freeze M5 and move to M6 coordinator and ephemeral workers,
using stable monolithic systems as controls. If any gate fails, preserve the
traces and document the remaining failure before changing the system again.

The [three-run diagnostic](diagnostics/kimi-k3-openrouter-m53.md) missed the
unchanged-repeat, `/api/me` repeat, and late-input gates. M5 remains open; the
traces and provider-limited first run are preserved.
