# M6.7.1 fresh launch approval pin

The human separately approved exactly one new collector launch for frozen
cells 87–128, once each under the original $230.40 cumulative estimated
model-token cap. Cells 85–86 remain provider failures. No retries or
replacements are authorized.

Operational recovery PR #59 merged as
`eb3535709631aedab73990d1204a83c2c371cc7a`. The consumed first
continuation approval and its failed output remain preserved. The new private
create-once receipt is `precell-paid-approval.json` (mode 0600) with SHA-256
`38af69f25668c3bcd53d50ce99f1dd189b9c153cfe81312ee612a1da9ee50fa0`.
Its contents remain private. The wrapper requires this exact hash, the
operational merge, original stopped evidence, the failed-launch evidence,
and the frozen manifest/source/protocol before any paid action.

The dedicated PostgreSQL stop was replayed read-only before pinning: 86
completed source cells, 172 runs, 143,873 events, zero active reservations.
The approval pin does not start a collector. The sole launch remains gated on
exact-head CI, a clean execution checkout, a fresh endpoint check, and an
unchanged authoritative stop.
