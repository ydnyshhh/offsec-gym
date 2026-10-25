# R0 model-run stage analysis

The offline analyzer derives S0 from a retained `RangeStarted` event. It can derive S5 and S6 only when a canonical scored evaluation identifies the expected root and a matching same-run validated finding cites paired action/evidence events. The expected root must be supplied from a trusted private binding; it is never inferred from a model claim.

S1–S4 remain `unknown` in this implementation. Event-only exports lack independently audited public response fields and decisive postcondition artifacts needed to establish those stages. This is a measurement boundary, not an agent failure. The private passive-audit records can later supply those source-bound observations. No deterministic reference-stage flags are copied into model results.
