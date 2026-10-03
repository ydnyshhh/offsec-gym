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


class BootstrapBudget(StrictModel):
    max_actions: int = Field(gt=0)
    max_http_requests: int = Field(gt=0)
    max_wall_seconds: int = Field(gt=0)
    max_model_calls: Literal[0] = 0
    max_total_tokens: Literal[0] = 0

    @model_validator(mode="after")
    def equal_dispatch_caps(self) -> BootstrapBudget:
        if self.max_actions != self.max_http_requests:
            raise ValueError("bootstrap action and HTTP caps must match")
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
    upstream_provider: str | None = Field(default=None, pattern=r"^[a-z0-9-]+$")
    input_usd_per_million_tokens: float | None = Field(default=None, gt=0, allow_inf_nan=False)
    output_usd_per_million_tokens: float | None = Field(default=None, gt=0, allow_inf_nan=False)
    input_reservation_bytes_per_token: float = Field(default=2.0, gt=0, allow_inf_nan=False)
    input_reservation_margin_tokens: int = Field(default=1024, ge=0)

    @model_validator(mode="after")
    def paired_token_prices(self) -> ModelSpec:
        if self.upstream_provider is not None and self.provider != "openrouter":
            raise ValueError("upstream_provider is supported only for OpenRouter")
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
    orchestrator: Literal[
        "scripted",
        "monolithic",
        "bootstrapped_monolithic",
        "planner_executor",
        "ephemeral_workers",
        "matched_sequential_workers",
        "matched_parallel_workers",
        "bootstrapped_sequential_workers",
        "bootstrapped_parallel_workers",
        "escrowed_sequential_workers",
        "escrowed_parallel_workers",
        "elastic_sequential_workers",
        "admitted_sequential_workers",
    ]
    memory: Literal["none", "transcript", "summary", "structured"] = "none"
    validation: Literal["deterministic", "self", "independent_model"] = "deterministic"
    surface_visibility: Literal["known_routes", "openapi", "discoverable", "black_box"] | None = (
        None
    )
    model: ModelSpec | None = None
    bootstrap_budget: BootstrapBudget | None = None

    @model_validator(mode="after")
    def require_model_for_llm(self) -> ExperimentSpec:
        if self.orchestrator != "scripted" and self.model is None:
            raise ValueError("model is required for non-scripted orchestrators")
        if (
            self.orchestrator in {"monolithic", "bootstrapped_monolithic"}
            and self.surface_visibility is None
        ):
            raise ValueError("monolithic runs require explicit surface_visibility")
        if (
            self.orchestrator in {"monolithic", "bootstrapped_monolithic"}
            and self.budget.max_model_calls is None
        ):
            raise ValueError("monolithic runs require explicit max_model_calls")
        if self.orchestrator == "bootstrapped_monolithic":
            if self.memory != "structured" or self.surface_visibility != "known_routes":
                raise ValueError("bootstrapped monolithic requires structured known_routes")
        if self.orchestrator in {
            "ephemeral_workers",
            "matched_sequential_workers",
            "matched_parallel_workers",
            "bootstrapped_sequential_workers",
            "bootstrapped_parallel_workers",
            "escrowed_sequential_workers",
            "escrowed_parallel_workers",
            "elastic_sequential_workers",
            "admitted_sequential_workers",
        }:
            if self.memory != "structured":
                raise ValueError("ephemeral workers require structured memory")
            if self.surface_visibility != "known_routes":
                raise ValueError("ephemeral workers require known_routes visibility")
            if self.budget.max_model_calls is None or self.budget.max_model_calls < 6:
                raise ValueError("ephemeral workers require at least six model calls")
            expected_concurrency = (
                6
                if self.orchestrator
                in {
                    "matched_parallel_workers",
                    "bootstrapped_parallel_workers",
                    "escrowed_parallel_workers",
                }
                else 1
            )
            if self.budget.max_workers != 6 or self.budget.max_concurrency != expected_concurrency:
                raise ValueError(
                    f"{self.orchestrator} requires max_workers=6 and "
                    f"max_concurrency={expected_concurrency}"
                )
            if self.budget.max_actions is not None and self.budget.max_actions < 6:
                raise ValueError("ephemeral workers require at least six actions")
            if self.budget.max_http_requests is not None and self.budget.max_http_requests < 6:
                raise ValueError("ephemeral workers require at least six HTTP requests")
            if self.budget.max_total_tokens is not None and self.budget.max_total_tokens < 6:
                raise ValueError("ephemeral workers require at least six total tokens")
        bootstrapped = self.orchestrator in {
            "bootstrapped_monolithic",
            "bootstrapped_sequential_workers",
            "bootstrapped_parallel_workers",
            "escrowed_sequential_workers",
            "escrowed_parallel_workers",
            "elastic_sequential_workers",
            "admitted_sequential_workers",
        }
        if bootstrapped:
            if self.bootstrap_budget is None:
                raise ValueError("bootstrapped runs require an explicit bootstrap budget")
            if self.budget.max_actions is None or self.budget.max_http_requests is None:
                raise ValueError("bootstrapped runs require explicit agent action and HTTP limits")
            if (
                self.orchestrator
                in {
                    "escrowed_sequential_workers",
                    "escrowed_parallel_workers",
                    "elastic_sequential_workers",
                    "admitted_sequential_workers",
                }
                and self.budget.max_total_tokens is None
            ):
                raise ValueError("budgeted workers require an explicit total token limit")
        elif self.bootstrap_budget is not None:
            raise ValueError("bootstrap budget is only valid for bootstrapped runs")
        if self.orchestrator == "scripted" and self.surface_visibility is not None:
            raise ValueError("surface_visibility is only supported for model orchestrators")
        if self.orchestrator in {
            "monolithic",
            "bootstrapped_monolithic",
            "ephemeral_workers",
            "matched_sequential_workers",
            "matched_parallel_workers",
            "bootstrapped_sequential_workers",
            "bootstrapped_parallel_workers",
            "escrowed_sequential_workers",
            "escrowed_parallel_workers",
            "elastic_sequential_workers",
            "admitted_sequential_workers",
        } and all(
            limit is None
            for limit in (self.budget.max_total_tokens, self.budget.max_output_tokens_per_call)
        ):
            raise ValueError("model runs require a total or per-call output token limit")
        if (
            self.orchestrator
            in {
                "monolithic",
                "bootstrapped_monolithic",
                "ephemeral_workers",
                "matched_sequential_workers",
                "matched_parallel_workers",
                "bootstrapped_sequential_workers",
                "bootstrapped_parallel_workers",
                "escrowed_sequential_workers",
                "escrowed_parallel_workers",
                "elastic_sequential_workers",
                "admitted_sequential_workers",
            }
            and self.budget.max_cost_usd is not None
            and self.model is not None
            and self.model.input_usd_per_million_tokens is None
        ):
            raise ValueError("a cost budget requires configured model token prices")
        return self
