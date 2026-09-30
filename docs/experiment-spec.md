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

For the monolithic baseline, `ModelSpec.provider=openai` selects the first provider
adapter. `ModelSpec.name` is an explicit model name. `max_model_calls` limits provider
round trips, and `max_total_tokens` limits observed input plus output tokens. If
`max_output_tokens_per_call` is set, it caps one provider response, including reasoning
tokens; otherwise the remaining total-token allowance is used. At least one of these
two token limits is required for monolithic runs. The controller stops before a call when
fewer than 16 total tokens remain. `surface_visibility=known_routes` explicitly labels
the initial diagnostic prompt, which discloses the API route shapes. It must be set for
monolithic runs and is omitted for scripted runs. The schema also
reserves `openapi`, `discoverable`, and `black_box`, but the current monolithic runner
rejects those modes until their prompt and discovery contracts are implemented. If
`max_cost_usd` is set, both token prices per million must be configured in `ModelSpec`.
The controller records usage after each call and stops before the next call when a ceiling
is reached. See [Milestone 4](milestone-4.md).
