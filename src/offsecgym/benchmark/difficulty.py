"""Engineering covariates, not calibrated difficulty scores."""

from __future__ import annotations

from typing import Literal

from pydantic import Field, model_validator

from offsecgym.schemas.common import StrictModel

FactorId = Literal["D1", "D2", "D3", "D4", "D5", "D6", "D7"]
QualificationStatus = Literal["measured", "unknown", "not_implemented"]
FACTOR_NAMES: dict[str, str] = {
    "D1": "exploration complexity",
    "D2": "object-grounding complexity",
    "D3": "authorization complexity",
    "D4": "temporal complexity",
    "D5": "service complexity",
    "D6": "ambiguity and decoys",
    "D7": "evidence burden",
}
DESIGN_LEVELS = {
    "L1-existing": "focused",
    "L2-proposed": "compositional",
    "L3-proposed": "long-horizon",
}


class DifficultyFactor(StrictModel):
    factor_id: FactorId
    factor_version: Literal["1"] = "1"
    level: Literal["unknown", "existing", "proposed"] = "unknown"
    measured_features: dict[str, int | float | str] = Field(default_factory=dict)
    measurement_method: str | None = None
    qualification_status: QualificationStatus = "unknown"

    @model_validator(mode="after")
    def honest_measurement(self) -> DifficultyFactor:
        if self.qualification_status == "measured" and (
            not self.measured_features or not self.measurement_method
        ):
            raise ValueError("measured factor requires features and method")
        if self.qualification_status != "measured" and self.measured_features:
            raise ValueError("unqualified factor cannot report measured features")
        if self.qualification_status == "not_implemented" and self.level != "proposed":
            raise ValueError("unsupported factor must have proposed level")
        return self


class DifficultyFactors(StrictModel):
    axes: tuple[DifficultyFactor, ...] = Field(min_length=7, max_length=7)
    design_level: Literal["L1-existing", "L2-proposed", "L3-proposed"]
    empirically_calibrated: Literal[False] = False

    @model_validator(mode="after")
    def all_axes_once(self) -> DifficultyFactors:
        if {axis.factor_id for axis in self.axes} != set(FACTOR_NAMES):
            raise ValueError("all seven distinct difficulty axes are required")
        if self.design_level != "L1-existing" and any(
            axis.qualification_status != "not_implemented" for axis in self.axes
        ):
            raise ValueError("L2/L3 variations are not implemented")
        return self


def existing_factors(fixture_entities: int, identities: int, services: int) -> DifficultyFactors:
    measured = {
        "D1": ("fixture_entities", fixture_entities),
        "D3": ("configured_identities", identities),
        "D5": ("configured_components", services),
    }
    axes = []
    for factor_id in FACTOR_NAMES:
        if factor_id in measured:
            key, value = measured[factor_id]
            axes.append(
                DifficultyFactor(
                    factor_id=factor_id,
                    level="existing",
                    measured_features={key: value},
                    measurement_method="deterministic fixture/spec count",
                    qualification_status="measured",
                )
            )
        else:
            axes.append(DifficultyFactor(factor_id=factor_id))
    return DifficultyFactors(axes=tuple(axes), design_level="L1-existing")
