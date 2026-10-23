# T0 stage observation contract

The common vocabulary is S0 environment initialized; S1 relevant public objects discovered; S2 required relationships established; S3 decisive observation or action completed; S4 required postcondition observed; S5 complete proof established; S6 finding independently validated. A task may have a proof graph rather than a linear sequence; a read-only task can satisfy S3/S4 through observations without mutation.

`StageResult` records `true`, `false`, `unknown`, or `not_applicable`, a rule version, reason code, and authoritative evidence reference for a true result. Model text and a bare HTTP status are not admissible evidence sources. The detached T0 vector is all `unknown` because a task definition is not a run trace. The schema does not itself verify an evidence reference; task-specific extractors must bind a source to run, generation, identity, object relationship, and canonical verifier result before declaring a stage true.

The current PR does not add a generic stage extractor or change the existing validator. This avoids treating a model statement, an arbitrary 200 response, a visible UUID, or a setup artifact as proof. Later task-specific rules must document which stages are inapplicable and which require ordered before/after evidence.
