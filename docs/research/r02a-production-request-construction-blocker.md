# R0.2a production request-construction audit: blocked before request capture

Source reviewed: draft PR #82, `96111f3952d637cd2721622817299ff51f3df84c`.
Declared protocol: `r0-a1-model-pilot-v2`, canonical SHA-256
`7cbd46177d8aa4cd1a5de74b4d1b7e02d5e8cfa19928487656674877ddafa45b`.
The frozen development matrix is seeds 42–44, each with vulnerable and patched
variants. Its model list is empty. This audit made zero provider, gateway, or
Docker calls and did not execute an agent.

## Production call chain

1. `ScriptedExperimentRunner._run_one` in
   `src/offsecgym/experiment/scripted.py` builds and starts the range, then
   calls `snapshot_metadata(instance_id)` and
   `agent_visible_context(metadata, visibility_policy="white_box_accounts")`.
   The latter projects identity IDs from the runtime metadata in
   `src/offsecgym/schemas/domain.py`.
2. The same controller constructs `AgentTask(task_id=uuid4(),
   goal="Test the synthetic range's documented security boundaries", ...)`
   and passes it to `agent.run`. Neither the frozen A1 task ID nor its scoped
   public objective is selected here.
3. `MonolithicExperimentRunner._agent` in
   `src/offsecgym/experiment/monolithic.py` selects transcript memory from the
   experiment spec and constructs `MonolithicSecurityAgent`.
4. `MonolithicSecurityAgent.run` in `src/offsecgym/solver/monolithic.py` calls
   the production `build_context` and `model_tools`. `build_context` puts
   `range_surface("saas").objective` in system instructions and `task.goal` in
   the first user item. It renders the runtime identity IDs and public routes.
   With transcript memory, later model outputs and tool results are appended to
   the input history. The initial request has no prior transcript.
5. The first provider payload is assembled inside `run` by
   `self.provider.prepare_request(self.model, instructions, request_input,
   definitions, max_output_tokens)`. The pure adapter implementations are
   `OpenAIResponsesProvider.prepare_request` and
   `OpenRouterResponsesProvider.prepare_request`. The latter can add an
   upstream-provider pin. `request_wire_bytes` in
   `src/offsecgym/providers/openai.py` serializes that payload; `complete`
   dispatches it. The controller writes a request artifact before dispatch.

This traces the actual production functions. No separate prompt renderer or
synthetic provider request was substituted for them.

## Declared versus constructed context

| Field | Frozen v2 declaration | Production construction at this head | Finding |
| --- | --- | --- | --- |
| Task ID | Six deterministic `t0-…` IDs from A1 scoped manifests | Fresh `uuid4()` per run | Mismatch |
| Public objective | A1 scoped document-access objective | Generic synthetic-range goal in first user item | Mismatch |
| System objective | Scoped A1 contract | Generic SaaS objective from `range_surface("saas")` | Generic objective contamination |
| Memory | `transcript` | Monolithic runner accepts and selects `transcript` | Configuration compatible; dispatch unverified |
| Identity IDs | `white_box_accounts` policy | Projected from `snapshot_metadata` after range startup | Policy identified; exact roster unavailable offline |
| Routes | `known_routes` and frozen route digest | `range_surface("saas").routes` in system instructions | Source path identified; runtime dispatch unverified |
| Tools | Frozen digest of `model_tools(family="saas", structured=False)` | `model_tools` called inside agent `run` | Source path identified; actual request digest unavailable |
| Budget | Seven frozen ceilings | Spec budget passed to task; first-turn output limit computed inside `run` | Static inputs known; final payload unavailable |
| Source pins | Frozen benchmark source-pin digest | Manifest/spec validated offline; controller does not bind the A1 task | No scoped source binding at controller boundary |
| Extra context | No bootstrap or structured-memory context declared | Default monolithic agent disables these; worker packet and experiment reminder paths are conditional | Initial default identified; actual dispatch unverified |

The offline `dry_run` validates the v2 protocol and six task manifests against
the current source pins. Its A1 admission report SHA-256 is
`7076ad6f80c48fbb7c66b0ac2b2d7846884dee1f0e12ae70db80e13bb0584c16`.
It reports `generic_controller_objective` and zero provider calls.

## Evidence boundary and stop condition

| Claim | Status | Basis |
| --- | --- | --- |
| `detached_context_parity` | Fails scoped system context | Existing detached audit calls production `build_context` with synthetic inputs and detects the generic SaaS objective. |
| `production_request_builder_parity` | Not evaluated for an actual A1 cell | The controller does not select A1, and the final first-turn payload depends on runtime metadata, a model configuration, and code inside `agent.run`. |
| `runtime_dispatched_context_parity` | Not evaluated | No model run or retained dispatched request was used. |

The final production request cannot be captured for the six frozen cells under
the permitted side-effect-free scope. `snapshot_metadata` requires a started
range; the v2 protocol has no configured model; and the request-construction
call occurs inside `MonolithicSecurityAgent.run`. Calling it would instantiate
the runtime path this task excludes. A fabricated identity roster or model
would yield a detached example, not the production request for a frozen cell.
Therefore no rendered-instruction, model-visible-context, identity-projection,
or final provider-request digest is claimed here. Route and tool digests in
the declared protocol remain declaration checks, not dispatched-request proof.

The requested negative cases for unexpected runtime identities, routes,
bootstrap additions, and provider-request drift cannot be qualified against
an actual production request until that request can be captured independently.
The protocol validator already rejects modified objectives, route/tool/source
pins, and incompatible memory modes; these checks do not establish controller
parity. `research_ops/benchmark_r0_a1_pilot.py --execute-models` still errors
before loading the protocol. R0.2 remains incomplete.
