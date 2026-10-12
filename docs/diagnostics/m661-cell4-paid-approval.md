# M6.6.1 cell-4 paid continuation approval pin

The separate private, create-only approval receipt has SHA-256
`6502419b322454debc4f4552ff0de6d8865586319b9a11c7dc3bf801b148aa0b`.
Its parser accepts only frozen assigned cells 5–96 once, with cells 1 and 4
retained as infrastructure missing, no retry or replacement, and the original
$172.80 cumulative estimated model-token cap. It binds the exact stopped
journal and log hashes, both missing source runs, frozen manifest and
assignment, source and protocol commits, the completed mode-only repair, and
the operational merge `41b77717e6f059821fb9149f90c886afed87b304`.

The receipt remains outside Git and is never copied into this repository.
This commit only pins its digest and tightens the parser. It does not create a
collector, alter any study artifact, or make a model call. The later execution
must still pass the prelaunch PostgreSQL, endpoint, price, key-presence, and
no-collector gates from the merged, clean execution checkout.
