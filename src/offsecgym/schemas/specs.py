"""Range and experiment input contracts."""

from __future__ import annotations

from typing import Literal

from pydantic import Field, model_validator

from offsecgym.schemas.common import StrictModel


class Budget(StrictModel):
    max_total_tokens: int | None = Field(default=None, gt=0)
    max_output_tokens_per_call: int | None = Field(default=None, ge=16)
    max_model_calls: int | None = Field(default=None, gt=0)
    max_actions: int | None = Field(default=None, gt=0)
    max_http_requests: int | None = Field(default=None, gt=0)
    max_workers: int | None = Field(default=None, gt=0)
    max_concurrency: int | None = Field(default=None, gt=0)
    max_wall_seconds: int | None = Field(default=None, gt=0)
    max_cost_usd: float | None = Field(default=None, gt=0, allow_inf_nan=False)

    @model_validator(mode="after")
    def require_limit(self) -> Budget:
        if all(value is None for value in self.model_dump().values()):
            raise ValueError("at least one budget limit is required")
        if (
            self.max_workers is not None
            and self.max_concurrency is not None
            and self.max_concurrency > self.max_workers
        ):
            raise ValueError("max_concurrency cannot exceed max_workers")
        return self


class VulnerabilitySpec(StrictModel):
    family: str = Field(min_length=1)
    component: str = Field(min_length=1)
    variant: str = Field(min_length=1)


class RangeSpec(StrictModel):
    schema_version: Literal["1"] = "1"
    family: str = Field(min_length=1)
    scenario: str = Field(min_length=1)
    seed: int = Field(ge=0)
    patched: bool = False
    patched_properties: tuple[str, ...] = ()
    topology: dict[str, bool] = Field(default_factory=dict)
    identities: dict[str, int] = Field(default_factory=dict)
    vulnerabilities: tuple[VulnerabilitySpec, ...] = ()

    @model_validator(mode="after")
    def validate_counts(self) -> RangeSpec:
        if any(count < 0 for count in self.identities.values()):
            raise ValueError("identity counts cannot be negative")
        if len(set(self.patched_properties)) != len(self.patched_properties):
            raise ValueError("patched property slugs must be unique")
        if self.patched and self.patched_properties:
            raise ValueError("patched=true cannot be combined with patched_properties")
        return self


class ModelSpec(StrictModel):
    provider: str = Field(min_length=1)
    name: str = Field(min_length=1)
    reasoning: str | None = None
    input_usd_per_million_tokens: float | None = Field(default=None, gt=0, allow_inf_nan=False)
    output_usd_per_million_tokens: float | None = Field(default=None, gt=0, allow_inf_nan=False)

    @model_validator(mode="after")
    def paired_token_prices(self) -> ModelSpec:
        if (self.input_usd_per_million_tokens is None) != (
            self.output_usd_per_million_tokens is None
        ):
            raise ValueError("both input and output token prices are required together")
        return self


class ExperimentSpec(StrictModel):
    schema_version: Literal["1"] = "1"
    name: str = Field(min_length=1)
    seed: int = Field(ge=0)
    range: RangeSpec
    budget: Budget
    orchestrator: Literal["scripted", "monolithic", "planner_executor", "ephemeral_workers"]
    memory: Literal["none", "transcript", "summary", "structured"] = "none"
    validation: Literal["deterministic", "self", "independent_model"] = "deterministic"
    surface_visibility: Literal["known_routes", "openapi", "discoverable", "black_box"] | None = (
        None
    )
    model: ModelSpec | None = None

    @model_validator(mode="after")
    def require_model_for_llm(self) -> ExperimentSpec:
        if self.orchestrator != "scripted" and self.model is None:
            raise ValueError("model is required for non-scripted orchestrators")
        if self.orchestrator == "monolithic" and self.surface_visibility is None:
            raise ValueError("monolithic runs require explicit surface_visibility")
        if self.orchestrator == "monolithic" and self.budget.max_model_calls is None:
            raise ValueError("monolithic runs require explicit max_model_calls")
        if self.orchestrator == "scripted" and self.surface_visibility is not None:
            raise ValueError("surface_visibility is only supported for model orchestrators")
        if self.orchestrator == "monolithic" and all(
            limit is None
            for limit in (self.budget.max_total_tokens, self.budget.max_output_tokens_per_call)
        ):
            raise ValueError("monolithic runs require a total or per-call output token limit")
        if (
            self.orchestrator == "monolithic"
            and self.budget.max_cost_usd is not None
            and self.model is not None
            and self.model.input_usd_per_million_tokens is None
        ):
            raise ValueError("a cost budget requires configured model token prices")
        return self
