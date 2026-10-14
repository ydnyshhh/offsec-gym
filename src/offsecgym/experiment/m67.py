"""Prospective M6.7 factorial switches without changing frozen ExperimentSpec."""

from __future__ import annotations

import hashlib
import json

from pydantic import StrictBool

from offsecgym.experiment.monolithic import MonolithicExperimentRunner
from offsecgym.experiment.scripted import BoundFindingSink
from offsecgym.schemas.common import StrictModel
from offsecgym.schemas.specs import ExperimentSpec
from offsecgym.solver.monolithic import MonolithicSecurityAgent, model_tools


class M67Arm(StrictModel):
    provenance_reporting: StrictBool
    experiment_commitment: StrictBool

    @property
    def name(self) -> str:
        return f"A{int(self.provenance_reporting)}B{int(self.experiment_commitment)}"

    def schema_sha256(self, *, family: str, structured: bool) -> str:
        tools = model_tools(
            family=family,
            structured=structured,
            provenance_reporting=self.provenance_reporting,
            experiment_commitment=self.experiment_commitment,
        )
        return hashlib.sha256(
            json.dumps(tools, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest()


class M67MonolithicExperimentRunner(MonolithicExperimentRunner):
    """Opt-in runner; existing baseline runners and spec serialization stay intact."""

    def __init__(self, runtime, events, provider, *, arm: M67Arm) -> None:
        super().__init__(runtime, events, provider)
        self.arm = arm

    def _agent(self, findings_store: BoundFindingSink, spec: ExperimentSpec):
        if spec.model is None:
            raise ValueError("M6.7 requires a model specification")
        return MonolithicSecurityAgent(
            self.provider,
            spec.model,
            findings_store,
            self.events,
            self.runtime.state.root,
            memory=spec.memory,
            provenance_reporting=self.arm.provenance_reporting,
            experiment_commitment=self.arm.experiment_commitment,
        )
