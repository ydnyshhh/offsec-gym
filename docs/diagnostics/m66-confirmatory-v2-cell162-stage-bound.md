# M6.6 confirmatory v2: cell-162 offline stage stop

The approved continuation stopped after **162 of 280** frozen cells. The
append-only journal SHA-256 is
`02e4acbb03125bb363810980372eb5d16f1eeb324f22c2eab979d8f8a862cdbc`.
Estimated model-token cost is **$71.985765** under the existing $504 cumulative
cap. Cell 163 never started. Pair 81 has no postcheck receipt and cell 162 has
no persisted stage artifact. Neither source cell may be rerun or replaced.

Cell 162 (`6a1de650f2404e27`, run
`91c0161e-32ed-45da-bd5d-aea499625583`) ended `agent_failed` after a
`ValidationError`; its oracle score remains valid. Its 1,904 contiguous
PostgreSQL events, trace hash
`c8f48f176e628a5fab038f2a3e7c18f009e64d51c8b1e6f2b82bbdc45a38ba0f`,
and lack of active controller reservations were verified. The frozen offline
stage extractor fails while reconstructing the same reporter packet: its JSON
is **70,045 characters**, 45 over the frozen 70,000-character bound. This is
an audit failure after the source run finished, not a provider outage or a new
model result.

## Proposed narrow amendment

An isolated offline worker first reproduces the frozen validation failure. It
then raises the packet-size guard **in memory to exactly 70,045 characters**
for this one pinned trace, verifies the reconstructed size, and invokes the
unchanged frozen stage predicates. The limit is restored immediately. The
runtime source, model request, range, validator, manifest, seeds, order,
budgets, and cell-162 terminal status remain unchanged.

The operational wrapper is bound to the exact 162-cell journal and source
run. After a separately approved, exact-hash receipt, it will replay
authoritative PostgreSQL events, persist the deterministic stage output and
an explicit amendment receipt create-only, reconstruct pair 81 from the two
retained source runs, and require the full frozen postcheck before admitting
cell 163. It retains the provider-failed cells 89 and 90. The separate
approval followed review of PR #24 at
`29d1b390fe696a5a7c73716d0fd78f3ff6e5b8e6`. Its private create-only
receipt has SHA-256
`2162bdaca0f407fbb32253d1bbb41b99412c049ec5626281b1ed19e80cdf6bc6`.
It covers **assigned cells 163–280 only**, with no retry or replacement and
the **same $504 cumulative** estimated-cost cap. The wrapper binds this exact
receipt hash and still requires exact-head CI, authoritative replay, the
frozen postcheck, and a fresh selected-endpoint/price check before paid work.

## Read-only rehearsal

Two independent offline reconstructions produced identical stage bytes,
SHA-256 `5a32492d655c508f1a83b30a8f0edcce2166aaebbedc20b7ae1b82d2b3c66849`.
A temporary-directory copy of the 162-cell journal, existing receipts, and
the proposed stage/pair-81 artifacts passed the frozen PostgreSQL postcheck:
162 source cells, 160 score valid, two retained provider failures, and
$71.985765 estimated cost. **No artifact in the stopped study was written and
no model call was made.**

This is a post-start measurement amendment. Any completed analysis must label
the cell-162 stage as reconstructed under this explicit exception and report
the original agent failure and missing provider outcomes. It must not imply
that the live reporter accepted an oversized packet or that the frozen packet
bound was changed for subsequent runs.
