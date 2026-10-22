# M6.8.2 historical public-reference grounding audit

This is a read-only audit of the pinned 18 model-authored `start_experiment` proposals and their 43 target references, spanning 12 cells. The canonical identifier-free aggregate is [`m682-reference-grounding-audit.json`](m682-reference-grounding-audit.json), SHA-256 `d859370459c843d726d82831a763f99035dfd94c7152334e545067483db53779`. Qualification details are in [`m682-grounding-qualification.json`](m682-grounding-qualification.json). The admission and delivery rules are in [`m682-source-design.md`](m682-source-design.md). No model or gateway action was dispatched by this audit, no paid approval was created, and no frozen result was rescored.

## Source inventory and boundary

The exact historical M6.7.3 input SHA-256 is `34bc9b4a9348fcf3a324ea7265a6e8d53d076e5d8e96778354bee1b008093647`. The frozen parser source SHA-256 is `f9b81420771e6e8802682aa5dd6383165847f705295f3a15883fc2dae23951d5`. The M6.7.3 read-only loader reverified the stopped source boundary: 128 assigned, 118 started, 114 trace-auditable, 110 score-valid, 233 database runs, and 189225 events. Cells 119–128 remained unstarted and were excluded. The proposal cohort reproduced 18 proposals, 43 references, 27 frozen-grammar-accepted references, four fully eligible proposals, and 35 references observed by the earlier general-context rule.

Across the 12 proposal cells, the index verified 396 bootstrap GET observations, 358 model-run GET observations, and 21 GET observations in separate critical-checkpoint setup runs. These are source observations, not 775 independently delivered identifiers. Only structural, typed records in verified public responses could resolve objects. A separate-run setup observation did not count as model-visible without proof of delivery.

## Reference stages (denominator: 43 references)

| Stage | Count | Interpretation |
|---|---:|---|
| R0 model proposal | 43 | All had verified model-response provenance. |
| R1 frozen syntax accepted | 27 | The other 16 remained parser-rejected. |
| R2 general context occurrence | 35 | This measure alone cannot establish origin. |
| R3 independently grounded and delivered | 34 | A typed public-source record reached a model request before the proposal. |
| R3 no prior visible grounding established | 8 | These references were not established as visible before the proposal and had no verified public-source match. |
| R3 unknown | 1 | The identifier occurred in general context and in a separate setup-run source, but delivery from that source was not proved. |
| R4 unique typed public object | 35 | This includes one object in verified setup evidence that was not delivered. |
| R5 declared kind matches | 32 | Includes 10 parser-rejected `change:` aliases with public change-request records. |
| R5 declared kind conflicts | 0 | No conflict was established in the observed cohort. |
| R5 type correspondence unknown | 11 | Three wrappers lacked a declared kind; eight references had no typed public match. |
| R7 private target-root match | 7 | All seven also had verified public grounding; the oracle did not establish model intent. |

Exactly one reference was observed in general model-visible context without verified delivery from an independent public source. It was a real typed object in a separate setup-run response, but the audit cannot attribute the general-context occurrence to that response. Eight further references appeared only as model-authored proposals within the verified public-source and earlier-context inventory; none was established as visible before the proposal. These categories describe available evidence, not proof that the model invented an identifier.

## Table A — syntax and public grounding

| Frozen syntax | Grounded | No prior visible grounding | Unknown | Total |
|---|---:|---:|---:|---:|
| Accepted | 22 | 5 | 0 | 27 |
| Rejected | 12 | 3 | 1 | 16 |
| **Total** | **34** | **8** | **1** | **43** |

The 12 parser-rejected yet grounded references show a representation problem. The five parser-accepted references without established grounding show a separate grounding problem. Neither count is a prospective treatment effect.

## Table B — public object and declared type

| Reference class | Refs | Public source observed | Grounded and delivered | Unique public object | Declared kind matches | Conflicts | Type unknown |
|---|---:|---:|---:|---:|---:|---:|---:|
| Supported typed UUID | 27 | 22 | 22 | 22 | 22 | 0 | 5 |
| Unsupported typed UUID | 12 | 10 | 9 | 10 | 10 | 0 | 2 |
| Other UUID wrapper | 3 | 3 | 3 | 3 | 0 | 0 | 3 |
| Natural language | 1 | 0 | 0 | 0 | 0 | 0 | 1 |

Thirteen unsupported forms identify unique typed public objects: ten `change:` aliases and three other wrappers. Twelve of the thirteen were independently delivered before proposal. An unsupported alias can have a correct semantic type while remaining invalid under the frozen parser. The three other wrappers lack a declared type, so their type correspondence is unknown even though their UUIDs resolve.

## Table C — complete proposal composition (denominator: 18)

| Proposal class | Count | ≥1 grounded | All grounded | All unique public objects | All correctly typed | Any type conflict |
|---|---:|---:|---:|---:|---:|---:|
| Fully syntactically eligible | 4 | 4 | 3 | 3 | 3 | 0 |
| Mixed eligible/ineligible | 13 | 13 | 8 | 9 | 8 | 0 |
| Only ineligible | 1 | 1 | 1 | 1 | 0 | 0 |
| **Total** | **18** | **18** | **12** | **13** | **11** | **0** |

The last proposal has no declared type for its wrapper, so “all correctly typed” is unknown, not false. Across all proposals, 12 have every target publicly grounded, five have at least one target with no established prior grounding, and one has unknown grounding. Eleven have every target's declared type confirmed, seven are type-indeterminate, and none has an established type conflict. The 13 mixed arrays remain invalid as whole proposals under the frozen parser; no invalid entry was dropped to create a valid subset.

## Table D — public resolution versus isolated oracle

| Public resolution | Private target-root match | Count |
|---|---|---:|
| Unique typed public object | Yes | 7 |
| Unique typed public object | No | 28 |
| Unresolved publicly | No | 7 |
| Unknown | Unknown | 1 |

No reference matched the frozen target root while lacking public grounding. This is a descriptive comparison over model-authored proposals, not a representative accuracy estimate. The private fixture pass was run only after the public classification and did not alter R2–R6.

## Table E — public relationships and hypothesis intent

Ten of 18 proposals contained a documented foreign-key relationship among proposed objects in verified delivered public records. The remaining eight had no mechanically established relationship from the available records. All 18 model-authored hypotheses are free text without a structured predicate-to-public-edge contract, so `hypothesis_relationship_supported` remains unknown for all 18. A public relationship is stronger than keyword overlap, but cannot establish that the selected objects test the model's stated hypothesis.

## Research answers and limits

Representation mismatch and object-grounding failure are empirically distinct in this cohort: 12 rejected references were delivered from public records, while five accepted references lacked established grounding. The former includes nine delivered `change:` aliases and three delivered wrappers. The current lossless E1 normalization grammar would still reject the `change:` alias; any alias expansion would require a separately prespecified contrast. The three fully eligible, fully grounded proposals are a cleaner observational subset, but historical proposals alone do not estimate whether normalization would improve activation, persistence, later use, or validated findings.

The record cannot establish intent for any free-text hypothesis, attribute the one context-observed reference to its verified setup source, prove that an undelivered setup observation was inferred by the model, exclude unseen objects from partially paginated listings, or infer outcomes of unrun treatments. The natural-language reference lacks an unambiguous public entity. R4 object existence is distinct from R3 model delivery, and R7 hidden target correspondence cannot retroactively establish either.

No historical run, reservation, gateway artifact, checkpoint, model request, or score was modified. The ignored per-proposal file contains exact evidence pointers and private comparisons; the tracked aggregate contains no UUIDs or raw response bodies.
