# M6.6 Range B preflight

This is an engineering and no-model qualification record. It is not a model
result or a confirmatory M6.6 protocol. No paid model call was made.

## Model runner and intervention boundary

`MonolithicSecurityAgent` now selects an agent-visible `RangeSurface` from the
range family. SaaS keeps its existing route list and finding tools. Enterprise
change control exposes its own route list, entity lookup types, gateway
destination, and one typed workflow-finding tool. The enterprise tool maps
each supported transition to the exact independent-validator family and
expectation. It never exposes fixture anchors or hidden property IDs.

The bootstrapped monolithic control and opt-in witness policy runner accept
both supported families. `WitnessPlanningPairRunner` executes both arms from
one experiment spec on separate instances of the same build and records their
order. Enterprise control and witness prompts and tool surfaces are identical
except for the bounded witness reminder and `start_witness`/`get_witness`
tools. SaaS's earlier model protocol remains importable for frozen-run replay.

## Range semantics

B2's change-detail response now renders `allowed_actions`; calculating that
field populates the isolated approval-decision cache. After a role revocation,
the vulnerable approval endpoint can reuse the stale decision. The patched
endpoint checks the canonical current role. This gives cache priming a normal
product reason while preserving the same hidden proof requirement.

B3 jobs retain the authorized queue-time decision and change revision. The
vulnerable worker trusts the snapshot. The B3 patch compares current change
status and revision with the queued snapshot and refuses a cancelled change.
It does not also recheck the operator's current role. This keeps the B3 patch
aligned with its scored cancellation root. The semantic compiler version is
`enterprise-change-control-v1/compiler-2`, so its build and pair identities
cannot be confused with the original Range B implementation.

## Qualification gates

The no-model compiler sweep checks 100 deterministic seeds, three hidden
root anchors, role separation, role availability, fixture structure, build
integrity, and distinct pair identities. The real-Compose scripted sweep
checks selected seeds in five variants each: vulnerable 3/3 roots, fully
patched 0/3, and each single-root patch 2/3, all score valid with no false
findings. The fake-provider M6.6 pair must use the exact pair runner on both
vulnerable and patched builds, reach the enterprise gateway in both arms,
and reject an evidence-cited but unsupported finding.

All gates passed. The [versioned summary](m66-range-b-qualification.json)
binds the source bundle and the [per-seed details](m66-range-b-qualification-details.json)
by SHA-256. The 100 compiler seeds were 0–99. Real Compose covered seeds
0–8 and 42, with 50 total scripted cells:

| Variant | Cells | Distinct validated roots in every cell | False findings |
| --- | ---: | ---: | ---: |
| Vulnerable | 10 | 3 | 0 |
| Fully patched | 10 | 0 | 0 |
| B1 patched | 10 | 2 | 0 |
| B2 patched | 10 | 2 | 0 |
| B3 patched | 10 | 2 | 0 |

All 50 were score valid and had zero false negatives relative to their
configured active roots. The fake-provider pair passed on both vulnerable and
fully patched builds. In each arm it made an enterprise gateway read, submitted
an evidence-cited but unsupported finding, and the independent validator
rejected it. The B2 regression observed `approve` in the vulnerable rendered
actions even after role revocation. The B3 regression confirmed the selective
patch still executes an uncancelled queued job after operator-role revocation.

The qualification was run on the final working-tree source before these
changes were committed. Its content hash binds all Range B runtime templates,
compiler, scripted solver, validator, and qualification code. Git commit
identity is not a substitute for that content hash in this precommit run.

The deterministic worker advances its logical clock after each gateway HTTP
request. A witness policy can therefore affect the time between queueing and
cancellation by making extra reads. A paid M6.6 protocol must predeclare
per-arm HTTP requests and logical ticks between queue and cancel, in addition
to complete witnesses, validated roots, false findings, tokens, and cost.

## Pilot boundary

An excluded live pilot remains unrun. It needs one frozen Range A seed, one
Range B seed, the same model and selected endpoint, the same control/witness
compute and action limits, explicit reporter allowance, a cost ceiling, a
price check, and separate user approval for paid calls. A later sample must
also freeze trace-stage extraction and arm order before collection.
