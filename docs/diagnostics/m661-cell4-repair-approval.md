# M6.6.1 cell-4 cached-build repair approval

The user approved **repair only** after reviewing CI-green PR #40. That PR
merged as `464b946cfd11c416165a8c9ae17a6e228efdcf75` and preserves the
read-only stop audit and mode-only repair code.

A private create-only receipt was written with mode `0600`. Its SHA-256 is
`e307bb70e175ef8adec708a28d4db76d84b6dc01412d13308729c128ad51bebd`.
The parser verified the receipt against the frozen manifest and assignment,
stopped continuation journal and log, cell-4 source run, cached build identity,
and original $172.80 cumulative cost limit. The receipt says
`approved_for_paid_calls=false` and `paid_cells_authorized=0`. Its contents are
not committed.

This pin permits only a create-once, byte-preserving file-mode change from
`0600` to `0644` for the 16 files in the exact cached patched build. Cell 1
and cell 4 remain assigned infrastructure-missing outcomes. The original
journals and PostgreSQL events remain authoritative. A separate decision,
approval, continuation implementation, and postcheck are required before any
frozen cell 5–96 can make a model call. No retry or replacement is authorized.
