"""Replay mutable security claims in a fresh, isolated synthetic instance."""

from __future__ import annotations

import json
from uuid import UUID, uuid4

from offsecgym.interfaces import ActionGateway
from offsecgym.runtime.compose import ComposeRangeRuntime, DockerCommandError
from offsecgym.runtime.manifests import BuildIntegrityError
from offsecgym.schemas.actions import ActionRequest, ActionResult
from offsecgym.schemas.domain import ExperimentContext, ValidationContext
from offsecgym.schemas.ground_truth import GroundTruthProperty, StateTransitionRequirement
from offsecgym.schemas.specs import Budget
from offsecgym.validation.deterministic import ReplayOutcome, _action_route


class CloneReplayVerifier:
    def __init__(self, runtime: ComposeRangeRuntime, gateway: ActionGateway) -> None:
        self.runtime = runtime
        self.gateway = gateway

    async def verify_transition(
        self, context: ValidationContext, prop: GroundTruthProperty, identity_id: UUID
    ) -> ReplayOutcome:
        instance_id: UUID | None = None
        outcome = ReplayOutcome("inconclusive")
        try:
            method, action_path = _action_route(prop, prop.object.object_id)
            requirement = next(
                item
                for item in prop.proof_requirements
                if isinstance(item, StateTransitionRequirement)
            )
            if method != "POST":
                raise ValueError("state-transition replay requires a POST action")
            read_path = action_path.rsplit("/", 1)[0]
            instance_id = await self.runtime.create_instance(context.build_id)
            status = await self.runtime.start_instance(instance_id)
            replay_context = ExperimentContext(
                run_id=uuid4(),
                range_instance_id=instance_id,
                range_generation=status.generation,
                budget=Budget(max_actions=3, max_http_requests=3),
                allowed_identity_ids=(identity_id,),
            )

            async def call(
                method: str, path: str, body: dict[str, str] | None = None
            ) -> ActionResult:
                return await self.gateway.execute(
                    ActionRequest(
                        run_id=replay_context.run_id,
                        kind="http_request",
                        destination="saas",
                        method=method,
                        path=path,
                        identity_id=identity_id,
                        json_body=body,
                    ),
                    replay_context,
                )

            before = await call("GET", read_path)
            changed = await call(method, action_path, {"reason": "validation replay"})
            after = await call("GET", read_path)
            results = (before, changed, after)
            if any(item.status in {"failed", "unknown"} for item in results):
                outcome = ReplayOutcome("inconclusive")
            elif any(item.status != "completed" for item in results):
                outcome = ReplayOutcome("rejected")
            else:
                bodies = tuple(_json_body(item) for item in results)
                correct = (
                    all(item.http_status == 200 for item in results)
                    and all(body is not None for body in bodies)
                    and all(body.get("id") == str(prop.object.object_id) for body in bodies)
                    and bodies[0].get("status") == requirement.from_state
                    and bodies[1].get("status") == requirement.to_state
                    and bodies[2].get("status") == requirement.to_state
                )
                outcome = ReplayOutcome(
                    "validated" if correct else "rejected",
                    tuple(item.evidence_id for item in results if item.evidence_id is not None),
                )
        except (BuildIntegrityError, DockerCommandError, OSError, TimeoutError, ValueError):
            outcome = ReplayOutcome("inconclusive")
        finally:
            if instance_id is not None:
                try:
                    await self.runtime.destroy_instance(instance_id)
                except (DockerCommandError, OSError, TimeoutError, ValueError):
                    outcome = ReplayOutcome("inconclusive")
        return outcome


def _json_body(result: ActionResult) -> dict[str, object] | None:
    try:
        value = json.loads(result.body_text or "")
    except (ValueError, TypeError):
        return None
    return value if isinstance(value, dict) else None
