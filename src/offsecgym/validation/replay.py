"""Replay mutable security claims in a fresh, isolated synthetic instance."""

from __future__ import annotations

import hashlib
import json
from uuid import UUID, uuid4

from offsecgym.interfaces import ActionGateway, EventStore
from offsecgym.runtime.compose import ComposeRangeRuntime, DockerCommandError
from offsecgym.runtime.manifests import BuildIntegrityError
from offsecgym.schemas.actions import ActionRequest, ActionResult
from offsecgym.schemas.domain import ExperimentContext, ReplayTraceRef, ValidationContext
from offsecgym.schemas.events import RangeStarted, RunCompleted, RunStarted
from offsecgym.schemas.ground_truth import GroundTruthProperty, StateTransitionRequirement
from offsecgym.schemas.specs import Budget
from offsecgym.validation.deterministic import ReplayOutcome, _action_route


class CloneReplayVerifier:
    def __init__(
        self, runtime: ComposeRangeRuntime, gateway: ActionGateway, events: EventStore
    ) -> None:
        self.runtime = runtime
        self.gateway = gateway
        self.events = events

    async def verify_transition(
        self,
        context: ValidationContext,
        prop: GroundTruthProperty,
        identity_id: UUID,
        asset_id: UUID,
    ) -> ReplayOutcome:
        replay_run_id = uuid4()
        instance_id: UUID | None = None
        generation: int | None = None
        evidence_ids: tuple[UUID, ...] = ()
        replay_status = "inconclusive"
        started = False
        try:
            replay_hash = hashlib.sha256(
                f"{context.run_id}:{context.build_id}:{prop.property_id}:{asset_id}".encode()
            ).hexdigest()
            await self.events.append(
                RunStarted(
                    run_id=replay_run_id,
                    actor="validator",
                    experiment_hash=replay_hash,
                )
            )
            started = True
            method, action_path = _action_route(prop, asset_id)
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
            generation = status.generation
            await self.events.append(
                RangeStarted(
                    run_id=replay_run_id,
                    actor="validator",
                    build_id=context.build_id,
                    range_instance_id=instance_id,
                    range_generation=generation,
                )
            )
            replay_context = ExperimentContext(
                run_id=replay_run_id,
                range_instance_id=instance_id,
                range_generation=generation,
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
            evidence_ids = tuple(
                item.evidence_id for item in results if item.evidence_id is not None
            )
            if any(item.status in {"failed", "unknown"} for item in results):
                replay_status = "inconclusive"
            elif any(item.status != "completed" for item in results):
                replay_status = "rejected"
            else:
                bodies = tuple(_json_body(item) for item in results)
                correct = (
                    all(item.http_status == 200 for item in results)
                    and all(body is not None for body in bodies)
                    and all(body.get("id") == str(asset_id) for body in bodies)
                    and bodies[0].get("status") == requirement.from_state
                    and bodies[1].get("status") == requirement.to_state
                    and bodies[2].get("status") == requirement.to_state
                )
                replay_status = "validated" if correct else "rejected"
        except (BuildIntegrityError, DockerCommandError, OSError, TimeoutError, ValueError):
            replay_status = "inconclusive"
        finally:
            if instance_id is not None:
                try:
                    await self.runtime.destroy_instance(instance_id)
                except (DockerCommandError, OSError, TimeoutError, ValueError):
                    replay_status = "inconclusive"
            if started:
                try:
                    await self.events.append(
                        RunCompleted(
                            run_id=replay_run_id,
                            actor="validator",
                            status="completed"
                            if replay_status in {"validated", "rejected"}
                            else "environment_failed",
                        )
                    )
                except Exception:
                    replay_status = "inconclusive"
        trace_ref = (
            ReplayTraceRef(
                replay_run_id=replay_run_id,
                range_instance_id=instance_id,
                range_generation=generation,
                evidence_ids=evidence_ids,
            )
            if instance_id is not None and generation is not None
            else None
        )
        return ReplayOutcome(replay_status, trace_ref)


def _json_body(result: ActionResult) -> dict[str, object] | None:
    try:
        value = json.loads(result.body_text or "")
    except (ValueError, TypeError):
        return None
    return value if isinstance(value, dict) else None
