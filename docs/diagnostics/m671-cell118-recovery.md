# M6.7.1 cell 118 stop and bounded continuation

## Authoritative stop

The consumed cell 116 continuation started cells 117 and 118, then exited. Cell 117 ended `provider_failed` and remains trace-auditable but unscored. Cell 118 ended `environment_failed` during range setup with zero provider model calls. Its cause is retained only as `DockerCommandError`; the sanitized receipt does not establish a more specific cause. The journal has no `cell_completed` row for 118. Cells 119–128 remain unstarted.

The new read-only verifier requires the exact stopped journal SHA-256 `5a9952abf49f9ab21bbe39d9628de17da0c691c6632a0bca6f5242a30594c032`, private log SHA-256 `695fcd254d9ee74308a48e745f320def11e20f50420094502300491fc3221e9c`, 63-file path/content digest `f75533a8bb8d1fe477b4bdf0c42bb388658909fab01fe42ea1942fee9ce7006f`, and sanitized failure receipt SHA-256 `240da4700057ea328004cef3dbe6a9a3f54c633d5143d9391d1f22ea296f87e7`. It replays all 233 source runs and 189,225 events, including the exact two-event cell 118 terminal trace, and admits only the existing cell 109 model reservation. The conservative cumulative cost is $61.469619 against the frozen $230.40 stop.

## Recovery boundary

The new one-pass collector selects only frozen cells 119–128, in pair order 60–64. It never retries or replaces cells 109, 113, 116, 117, or 118, and it never settles the cell 109 reservation. It copies the 58 prior pair receipts and closes pair 59 from the retained cell 117 and 118 run IDs, with no source or provider call. Its fresh create-once output root is `cell118-continuation`; all earlier roots, journals, logs, and pair receipts remain immutable.

The prior cell 116 approval is consumed. This collector remains closed until a distinct mode-0600, create-once `cell118-paid-approval.json` is bound to the new exact scope and its SHA-256 is pinned in reviewed code. The receipt's `approved_cell_ids` lists the frozen manifest for compatibility with the unchanged protocol guard; `paid_cell_ids` and the operational range restrict actual paid work to 119–128. The collector checks both on every cell. It also requires the reviewed operational merge commit as an ancestor, the fixed source/protocol/manifest, a fresh public endpoint preflight, the pinned Docker `default` context, authoritative run inventory, and the original cumulative cap before each new cell.

An isolated synthetic range with seed 900001 started healthy and was destroyed with zero provider calls. Its separate qualification receipt SHA-256 is `8d086e9bdbd3a7e66c7c15e4e19efa1d36b51ee44ac435ef5f86bb974ec1e131`. This qualifies the current Docker path; it does not establish the historical cause of cell 118's failure or guarantee future range starts.

The collector stops on any unexpected terminal status or integrity drift and writes only sanitized failure evidence. No additional paid launch or started-cell retry is implied by this recovery.

## CI validation window

The same full Docker-backed `pytest -q` gate passed for PR #67 in about 56–58 minutes. Both exact-head PR #68 jobs reached the existing 60-minute Test-step deadline without an assertion failure; their last completed progress markers were 8% and 16%. The Test-step deadline is now 75 minutes and its containing job deadline is 80 minutes. The workflow triggers, check name, permissions, and test command are unchanged. This gives the observed workload room to finish while retaining a bounded failure deadline; PR #68 still requires new exact-head CI success before merge.
