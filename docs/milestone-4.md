# Milestone 4: monolithic model baseline

The baseline runs one model agent through the same audited SaaS gateway, controller-bound
finding sink, independent validator, replay, and evaluator as the scripted agent. The
agent sees an allowlisted identity roster and public API route shapes. It does not see
fixtures, hidden ground truth, attack graphs, range credentials, or Docker. HTTP response
content is untrusted input. The controller executes all tool calls and binds every action
and finding to its run and instance generation.

`ModelProvider.prepare_request` creates the provider JSON body; `complete` returns the
raw response plus parsed output items and token usage. The first
adapter uses the OpenAI Responses API with strict function schemas, stateless conversation
input, and `store=false`; it carries reasoning items forward for subsequent tool turns.
The four model tools are `http_request` and typed submission tools for authorization,
field exposure, and state transition. The controller parses arguments into Pydantic
models. Malformed calls receive a tool error; three malformed calls end as a scored
`agent_failed` run. See the [official OpenAI function-calling guide](https://developers.openai.com/api/docs/guides/function-calling)
for the function-call and `function_call_output` exchange.

This baseline requires `memory=transcript`; other memory conditions are reserved for
later milestones.

Each provider request is first written to `model_calls/<run_id>/<call_id>/request.json`.
The returned response is written to `response.json` before `ModelCallCompleted` is
appended. Files are mode 0600 under private directories; artifacts include opaque
reasoning state, raw tool arguments, and the exact transcript sent on each turn.
The v2 `ModelCallStarted` and `ModelCallCompleted` events carry artifact IDs and SHA-256
digests so a reader can verify them. Event payloads contain provider/model names, usage,
and response ID but no API key or prompt. The API key is only placed in the HTTP header,
outside the saved request body. Bounded JSON bodies returned with HTTP errors are also
retained as response artifacts after API-key redaction. The CLI summarizes usage per run. Action and HTTP
budgets remain enforced by the gateway. The agent enforces model-call, observed token,
configured cost, and wall-time budgets. A single provider response can overshoot a token
or cost ceiling because final usage is known only afterward; the controller stops before
the next request. `max_output_tokens_per_call` is an explicit optional cap; without it,
the remaining total-token budget is sent as `max_output_tokens`. Fewer than 16 remaining
tokens end the run without a provider request. OpenAI counts reasoning tokens within
[`max_output_tokens`](https://developers.openai.com/api/docs/guides/reasoning), so tune
the per-call cap for the selected model. Token prices are explicit experiment inputs, not assumed market rates;
the estimate may differ from provider billing, including cache discounts.

Provider outages, 5xx responses, rate limits, and authentication failures produce
`provider_failed` with `score_valid=false`. Invalid provider requests or malformed API
responses are harness failures (`environment_failed`). Malformed model tool calls and
agent failures stay scored. Controller cancellation is `cancelled` and unscored. Submitted
findings are still validated before scoring or marking a run unscored.

## Running a diagnostic batch

Edit `experiments/configs/monolithic-saas.yaml` to name a model available to your API
account. Set the API key in the environment, not in the YAML or repository. Configure an
isolated PostgreSQL control database and apply migrations:

```sh
export OPENAI_API_KEY='...'
export OFFSECGYM_DATABASE_URL='postgresql+asyncpg://USER:PASSWORD@HOST/DB'
uv run alembic upgrade head
uv run offsecgym experiment run experiments/configs/monolithic-saas.yaml --repetitions 10
```

Use `--paired` to run vulnerable and fully patched variants per repetition. The command
supports 1–20 repetitions. For a cost ceiling, add `max_cost_usd` under `budget` and both
`input_usd_per_million_tokens` and `output_usd_per_million_tokens` under `model`, using
prices valid for the chosen model. The current model adapter does not retry requests, so
provider failures stay visible as individual diagnostic outcomes.

The first 10–20 live runs are a harness diagnostic. Inspect each run's model, action,
finding, validation, and terminal events before drawing conclusions. The fake-provider
Docker acceptance test checks the complete local tool and scoring path without an API key;
it does not replace the live diagnostic batch.

`surface_visibility=known_routes` is the current diagnostic condition. The prompt gives
the model exact route shapes and finding classes. The spec reserves `openapi`,
`discoverable`, and `black_box` as future experimental conditions; they are rejected by
this runner until implemented. This baseline does not support model capability claims
across surface-visibility conditions.

Use `offsecgym experiment trace RUN_ID` to inspect one run's persisted event stream.
