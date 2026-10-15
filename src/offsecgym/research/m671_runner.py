"""Thin guarded composition of the frozen checkpoint and M6.7 arm behavior."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from offsecgym.experiment.critical_checkpoint import CriticalCheckpointExperimentRunner
from offsecgym.experiment.m67 import M67Arm
from offsecgym.experiment.scripted import BoundFindingSink
from offsecgym.research.m671_protocol import digest, require_paid_approval
from offsecgym.schemas.specs import ExperimentSpec
from offsecgym.solver.monolithic import MonolithicSecurityAgent


@dataclass(frozen=True)
class ApprovedCell:
    cell_id: str
    arm_name: str
    checkpoint_id: str


def admit_cell(
    manifest_bytes: bytes,
    approval: dict[str, object],
    *,
    cell_id: str,
    started_cell_ids: set[str],
    active_reservations: int,
    cumulative_estimated_cost_usd: Decimal,
) -> ApprovedCell:
    """A future collector must supply authoritative PostgreSQL inventory."""
    import json

    require_paid_approval(
        approval,
        manifest_bytes,
        cell_id=cell_id,
        started_cell_ids=started_cell_ids,
        active_reservations=active_reservations,
        cumulative_estimated_cost_usd=cumulative_estimated_cost_usd,
    )
    manifest = json.loads(manifest_bytes)
    row = next((item for item in manifest["cells"] if item["cell_id"] == cell_id), None)
    if row is None:
        raise ValueError("approved cell absent from manifest")
    return ApprovedCell(cell_id, row["arm"], row["checkpoint_id"])


class M671CriticalFactorialRunner(CriticalCheckpointExperimentRunner):
    """Reuse the qualified checkpoint lifecycle; switch only existing M6.7 flags."""

    def __init__(
        self,
        runtime,
        events,
        provider,
        *,
        arm: M67Arm,
        admission: ApprovedCell,
    ) -> None:
        if arm.name != admission.arm_name:
            raise ValueError("arm differs from approved assignment")
        super().__init__(runtime, events, provider, admission.checkpoint_id)
        self.arm = arm
        self.admission = admission

    def _experiment_hash(self, spec: ExperimentSpec) -> str:
        return digest(["m671-run-v1", self.arm.run_hash(spec), self.admission.checkpoint_id])

    def _agent(self, findings_store: BoundFindingSink, spec: ExperimentSpec):
        if spec.model is None:
            raise ValueError("M6.7.1 requires pinned model configuration")
        return MonolithicSecurityAgent(
            self.provider,
            spec.model,
            findings_store,
            self.events,
            self.runtime.state.root,
            memory="structured",
            bootstrap_context=True,
            witness_planning=False,
            provenance_reporting=self.arm.provenance_reporting,
            experiment_commitment=self.arm.experiment_commitment,
        )
