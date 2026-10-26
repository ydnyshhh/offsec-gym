# R0.1 detached protocol correction

The frozen `r0-a1-model-pilot-v1` protocol remains unchanged. Its `memory: none` setting is unsupported by `MonolithicExperimentRunner`.

The new [v2 configuration](../../configs/research/r0-a1-pilot-v2.yaml) changes only the protocol version and memory mode to `transcript`. It retains A1, seeds 42–44, both variants, the public objective, visibility, source pins, route and tool digests, and all seven budget ceilings. The canonical parsed protocol SHA-256 is `7cbd46177d8aa4cd1a5de74b4d1b7e02d5e8cfa19928487656674877ddafa45b`. Model configuration is still empty.

The detached dry run validates all six task IDs against the A1 admission report, which remains pinned to `7076ad6f80c48fbb7c66b0ac2b2d7846884dee1f0e12ae70db80e13bb0584c16`. A synthetic render checks the public goal, identities, routes, and tool projection without a runtime, gateway, or provider. It confirms that the system instructions still include the broader SaaS range objective. The synthetic render does not establish live controller parity.

The memory-mode mismatch is resolved for v2. Scoped controller binding and full rendered model-context parity remain blocked. `--execute-models` continues to fail closed. There have been zero model runs and zero provider calls under this protocol.

Retained-run metadata requires an explicit protocol version. An offline import with a missing version fails validation, so a future v2 exporter cannot silently inherit v1 metadata.
