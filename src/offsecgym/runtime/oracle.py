"""Privileged oracle lookup bound to generated builds and instance generations."""

from __future__ import annotations

from uuid import UUID

from pydantic import ValidationError

from offsecgym.runtime.manifests import BuildIntegrityError, StateStore
from offsecgym.runtime.saas import PROPERTY_SLUGS, patched_properties
from offsecgym.schemas.domain import ValidationContext
from offsecgym.schemas.ground_truth import GroundTruthManifest


class InvalidGroundTruthError(BuildIntegrityError):
    """The verified hidden artifact does not satisfy the oracle contract."""


class OracleBindingError(ValueError):
    """A validation context points to another build or target generation."""


class StateOracleStore:
    def __init__(self, state: StateStore) -> None:
        self.state = state

    def _load_ground_truth(self, build_id: UUID) -> GroundTruthManifest:
        build = self.state.verify_build_integrity(build_id)
        if build.spec.family != "saas":
            raise InvalidGroundTruthError("this range family has no security oracle")
        path = self.state.root / "oracles" / build_id.hex / "ground_truth.json"
        try:
            oracle = GroundTruthManifest.model_validate_json(path.read_bytes())
        except (OSError, ValidationError) as exc:
            raise InvalidGroundTruthError("hidden ground truth is invalid") from exc
        patch_set = patched_properties(build.spec)
        variant = (
            "vulnerable"
            if not patch_set
            else "patched"
            if patch_set == PROPERTY_SLUGS
            else "selective"
        )
        if (
            oracle.build_id != build_id
            or oracle.pair_id != build.pair_id
            or oracle.scenario_id != build.spec.scenario
            or oracle.seed != build.spec.seed
            or oracle.variant != variant
        ):
            raise InvalidGroundTruthError("hidden ground truth differs from its build binding")
        return oracle

    def load_for_context(self, context: ValidationContext) -> GroundTruthManifest:
        instance = self.state.load_instance(context.range_instance_id)
        if instance.build_id != context.build_id:
            raise OracleBindingError("validation build does not match range instance")
        if instance.generation != context.range_generation:
            raise OracleBindingError("validation generation does not match range instance")
        oracle = self._load_ground_truth(context.build_id)
        if oracle.build_id != instance.build_id:
            raise OracleBindingError("ground truth build does not match range instance")
        return oracle
