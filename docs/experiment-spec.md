# Experiment specification v1

`ExperimentSpec` binds a range to `orchestrator`, `memory`, `validation`, `Budget`, optional
model, and an experiment seed. A model is required for every non-scripted orchestrator.
The [scripted example](../experiments/configs/scripted-hello.yaml) is valid but cannot run
until the runtime and scripted solver milestones are implemented.

Before a run, resolve the spec into an immutable manifest with hashes of the spec, code,
range artifacts, prompts, tools, validator, and images. Budget limits are shared by all
workers of one run. Reserve resource headroom before dispatch and reconcile actual usage
after completion. Hard limits stop further work; a run ends with an explicit status.

An experiment matrix expands configurations into individual manifests. Each run is stored
and evaluated, including infrastructure failures. Paired vulnerable/patched runs use the
same scenario seed and replication block.
