# M6.6.1 cell-4 paid continuation proposal

The excluded feasibility study stopped before cell 5. Cell 1 and cell 4 have
authoritative two-event `environment_failed` runs with zero model calls and
zero estimated model-token cost. Cells 2 and 3 have completed, score-valid
source runs. The original journal and first continuation journal are immutable.
The cumulative estimated cost is $0.719841 against the original $172.80 cap.

The approved, completed repair changed only the modes of 16 cached build files
from `0600` to `0644`. Its intent and completion receipts bind the original
file hashes. The repair did not retry either missing cell or authorize paid
work. The source, protocol, manifest, model, checkpoint states, assignments,
and assignment order remain frozen.

This proposal adds an exact read-only admission for the repaired stop, a
create-only retrospective pair-2 receipt from existing events, a separate
continuation journal for assigned cells 5–96, and a full PostgreSQL postcheck.
The runner is closed while `EXPECTED_PAID_APPROVAL_SHA256` is all zeros. A
separate private paid-call receipt must bind both stopped journal hashes, both
missing run IDs, the exact cells 5–96, no retry or replacement, and the
original cumulative cap before any new model call. The wrapper does not reset
the provider-health history or charge missing cells as paid calls.

The pre-call postcheck verifies the retained pair receipts, source events,
trace and stage hashes, validator replays, selected endpoint and price,
PostgreSQL run inventory, reservations, and cumulative cost. An interrupted
new cell remains unscored and is not retried. The final postcheck retains
cells 1 and 4 as assigned missing, while reporting their independently
audited zero resource use. The resulting checkpoint curves remain descriptive
for this excluded feasibility study; the two infrastructure-missing outcomes
reduce the observed sample and are not replaced to restore balance.
