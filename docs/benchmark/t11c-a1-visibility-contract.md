# T1.1c A1 model-facing visibility contract

The qualification renders the current monolithic agent's `build_context()` and `model_tools()` for the scoped A1 public objective. The frozen policy is `t11c-a1-public-visibility-v1`: `white_box_accounts` supplies allowed identity IDs, while roles are discovered through `GET /api/me`. The route list comes from the SaaS `range_surface`; bootstrap and structured memory are off. The prompt exposes neither fixture documents nor the hidden oracle.

The reference policy receives the public objective, exactly the rendered identity roster, and the rendered route inventory. It does not receive `AgentVisibleRangeContext.known_roles`, fixture files, target anchors, or a generated target UUID. `freeze_visibility()` checks the literal prompt roster, route inventory, role-discovery instruction, and availability of the model's HTTP and authorization-finding tools before asserting parity. The source-bound JSON contract is `t11c-a1-visibility-contract.json`; the full rendered context is retained only in the private qualification artifact.

This contract describes the current monolithic, unstructured, no-bootstrap harness. A later benchmark harness with different initialization must receive a new visibility contract and qualification. Account visibility, route visibility, and tool permissions are separate inputs.
