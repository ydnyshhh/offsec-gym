# Experiment specification v1

`ExperimentSpec` binds a range to `orchestrator`, `memory`, `validation`, `Budget`, optional
model, and an experiment seed. A model is required for every non-scripted orchestrator.
The [scripted SaaS example](../experiments/configs/scripted-saas.yaml) runs through the
Milestone 3 solver, validator, replay, and evaluator. The older scripted hello example
remains a valid specification but has no security properties to score.

Before a run, resolve the spec into an immutable manifest with hashes of the spec, code,
range artifacts, prompts, tools, validator, and images. Budget limits are shared by all
workers of one run. Reserve resource headroom before dispatch and reconcile actual usage
after completion. Hard limits stop further work; a run ends with an explicit status.

An experiment matrix expands configurations into individual manifests. Each run is stored
and evaluated, including infrastructure failures. Paired vulnerable/patched runs use the
same scenario seed and replication block.
