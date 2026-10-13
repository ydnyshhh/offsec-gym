# M6.6.1 excluded feasibility: cell-14 provider-health stop

The approved continuation stopped after assigned cell 14. Cells 13 and 14 are
retained as `provider_failed/provider_unavailable`, with invalid scores and
unreconstructable resource totals. The frozen health rule pauses after two
consecutive transport failures. Cell 15 was never started. Cells 1 and 4
remain infrastructure missing; cells 2, 3, and 5–12 remain score valid.

The original and first-continuation journals remain unchanged. The second
continuation journal is SHA-256
`d2988dbe83364cb89a46fec1b5812d90dd2f01f35379760d5aa556b9fb98fdba`;
its stopped collector log is SHA-256
`ebd86476e579f931d945d052c00c8915a5d8f5e2d05527e084f3f699db6b6c97`.
Six pair receipts exist. Pair 7 has a selected endpoint preflight but lacks a
receipt because the health gate fired after cell 14.

Estimated cumulative model-token cost is $4.202310. Conservative accounting
charges the full $1.80 cell reservation for each resource-unauditable provider
failure, yielding $7.646460. Both are below the frozen $172.80 cumulative cap.
The authoritative database has exactly 26 referenced runs and 18,626 events.
Read-only frozen replay verified the recorded source and setup runs, event and
trace identities, scores, stage extraction, no extra run, and no active
reservation; it reached the expected missing pair-7 receipt. The new
`m661_cell14_provider_stop.py` admission binds those checks to the exact
stopped bytes and exits before any model call or artifact write.

A proposed operational recovery may close pair 7 solely from these retained
events and begin one new provider-health epoch at cell 15. It must retain both
provider failures and both infrastructure-missing assignments, preserve all
frozen assignments and their order, and never retry or replace a cell. This
stop document and the read-only audit authorize **zero** paid cells. A
separate, exact-scope human decision is required before any merge, approval
receipt, or paid continuation.
