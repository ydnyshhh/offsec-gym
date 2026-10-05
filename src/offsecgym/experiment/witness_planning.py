"""Opt-in M6.6 witness-ledger policy with the frozen monolithic control otherwise intact."""

from __future__ import annotations

import hashlib
import json

from offsecgym.experiment.bootstrapped_monolithic import BootstrappedMonolithicExperimentRunner
from offsecgym.experiment.scripted import BoundFindingSink
from offsecgym.schemas.specs import ExperimentSpec
from offsecgym.solver.monolithic import MonolithicSaasAgent


class WitnessPlanningExperimentRunner(BootstrappedMonolithicExperimentRunner):
    def _experiment_hash(self, spec: ExperimentSpec) -> str:
        record = {
            "spec": spec.model_dump(mode="json"),
            "policy": "m66-generic-temporal-witness-v1",
        }
        return hashlib.sha256(
            json.dumps(record, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest()

    def _agent(self, findings_store: BoundFindingSink, spec: ExperimentSpec):
        if spec.model is None:
            raise ValueError("witness planning requires a model")
        return MonolithicSaasAgent(
            self.provider,
            spec.model,
            findings_store,
            self.events,
            self.runtime.state.root,
            memory="structured",
            bootstrap_context=True,
            witness_planning=True,
        )
