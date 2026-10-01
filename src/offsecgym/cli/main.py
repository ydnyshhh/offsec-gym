"""OffSecGym command-line skeleton."""

from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path
from uuid import UUID

import typer
import yaml
from pydantic import ValidationError
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import create_async_engine

from offsecgym import __version__
from offsecgym.config import Settings
from offsecgym.experiment import (
    MonolithicExperimentRunner,
    ScriptedExperimentRunner,
    WorkerExperimentRunner,
)
from offsecgym.gateway.compose import ComposeActionGateway
from offsecgym.orchestration_metrics import orchestration_metrics
from offsecgym.providers import OpenAIResponsesProvider, OpenRouterResponsesProvider
from offsecgym.runtime.compose import ComposeRangeRuntime, DockerCommandError
from offsecgym.runtime.oracle import StateOracleStore
from offsecgym.schemas.domain import ValidationContext
from offsecgym.schemas.events import (
    FindingSubmitted,
    FindingValidated,
    ModelCallCompleted,
    RangeStarted,
)
from offsecgym.schemas.specs import ExperimentSpec, RangeSpec
from offsecgym.storage.event_store import PostgresEventStore
from offsecgym.validation import CloneReplayVerifier, DeterministicValidator
from offsecgym.validation.ontology import normalize_legacy_m5v1
from offsecgym.worldview import EventWorldState, WorldStateIntegrityError
from offsecgym.worldview.ledger import EntityLedger

app = typer.Typer(help="Synthetic-range research platform")
range_app = typer.Typer(help="Build and manage isolated synthetic ranges")
spec_app = typer.Typer(help="Versioned input specifications")
experiment_app = typer.Typer(help="Run deterministic synthetic-range experiments")
app.add_typer(range_app, name="range")
app.add_typer(spec_app, name="spec")
app.add_typer(experiment_app, name="experiment")


@app.command()
def version() -> None:
    """Print package version."""

    typer.echo(__version__)


@spec_app.command("validate")
def validate_spec(
    path: Path, kind: str = typer.Option("range", help="range or experiment")
) -> None:
    """Validate a YAML specification without starting services."""

    if kind not in {"range", "experiment"}:
        typer.echo("kind must be 'range' or 'experiment'", err=True)
        raise typer.Exit(2)
    try:
        spec = _load_spec(path, RangeSpec if kind == "range" else ExperimentSpec)
    except (OSError, yaml.YAMLError, ValidationError, ValueError) as exc:
        typer.echo(f"invalid {kind} specification: {exc}", err=True)
        raise typer.Exit(2) from exc
    typer.echo(f"valid {kind} specification (schema_version={spec.schema_version})")


def _load_spec(path: Path, schema: type[RangeSpec] | type[ExperimentSpec]):
    raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    return schema.model_validate(raw)


def _runtime() -> ComposeRangeRuntime:
    return ComposeRangeRuntime(Settings().state_dir)


def _fail(exc: Exception) -> None:
    typer.echo(f"range error: {exc}", err=True)
    raise typer.Exit(2) from exc


@experiment_app.command("run")
def experiment_run(
    path: Path,
    paired: bool = typer.Option(False, help="Also run the fully patched sibling"),
    repetitions: int = typer.Option(1, min=1, max=20, help="Diagnostic repetitions per variant"),
) -> None:
    """Run a scripted, monolithic, or worker SaaS experiment."""
    try:
        spec = _load_spec(path, ExperimentSpec)
        if (
            spec.orchestrator
            not in {
                "scripted",
                "monolithic",
                "ephemeral_workers",
                "matched_sequential_workers",
                "matched_parallel_workers",
            }
            or spec.validation != "deterministic"
        ):
            raise ValueError("only scripted, monolithic, or worker runs are supported")
        if spec.orchestrator == "monolithic" and spec.memory not in {"transcript", "structured"}:
            raise ValueError("the monolithic baseline requires memory=transcript or structured")
        if spec.orchestrator == "monolithic" and spec.surface_visibility != "known_routes":
            raise ValueError("the monolithic baseline requires surface_visibility=known_routes")
        if paired and (spec.range.patched or spec.range.patched_properties):
            raise ValueError("--paired requires an unpatched base range")
        settings = Settings()
        if settings.database_url is None:
            raise ValueError("OFFSECGYM_DATABASE_URL is required for experiment events")
        if not settings.database_url.startswith("postgresql+asyncpg://"):
            raise ValueError("OFFSECGYM_DATABASE_URL must use postgresql+asyncpg")
        api_key = None
        if spec.orchestrator in {
            "monolithic",
            "ephemeral_workers",
            "matched_sequential_workers",
            "matched_parallel_workers",
        }:
            if spec.model is None or spec.model.provider not in {"openai", "openrouter"}:
                raise ValueError("the monolithic runner supports provider=openai or openrouter")
            if spec.model.name.startswith("REPLACE_"):
                raise ValueError("set model.name to an available API model before running")
            key_name = (
                "OPENROUTER_API_KEY" if spec.model.provider == "openrouter" else "OPENAI_API_KEY"
            )
            api_key = os.getenv(key_name)
            if spec.model.provider == "openai":
                api_key = api_key or os.getenv("OFFSECGYM_OPENAI_API_KEY")
            if not api_key:
                raise ValueError(f"{key_name} is required for {spec.model.provider} experiments")

        async def execute() -> list[dict[str, object]]:
            engine = create_async_engine(settings.database_url)
            try:
                events = PostgresEventStore(engine)
                runtime = ComposeRangeRuntime(settings.state_dir)
                if spec.orchestrator == "scripted":
                    runner = ScriptedExperimentRunner(runtime, events)
                else:
                    runner_type = (
                        WorkerExperimentRunner
                        if spec.orchestrator
                        in {
                            "ephemeral_workers",
                            "matched_sequential_workers",
                            "matched_parallel_workers",
                        }
                        else MonolithicExperimentRunner
                    )
                    runner = runner_type(
                        runtime,
                        events,
                        (
                            OpenRouterResponsesProvider(api_key or "")
                            if spec.model is not None and spec.model.provider == "openrouter"
                            else OpenAIResponsesProvider(api_key or "")
                        ),
                    )
                specs = [spec]
                if paired:
                    specs.append(
                        spec.model_copy(
                            update={"range": spec.range.model_copy(update={"patched": True})}
                        )
                    )
                reports = []
                for repetition in range(1, repetitions + 1):
                    for item in specs:
                        outcome = await runner.run(item)
                        typer.echo(
                            f"repetition={repetition} run_id={outcome.run_id} "
                            f"status={outcome.evaluation.status}",
                            err=True,
                        )
                        trace = await events.read_run(outcome.run_id)
                        calls = [event for event in trace if isinstance(event, ModelCallCompleted)]
                        reports.append(
                            {
                                "run_id": str(outcome.run_id),
                                "build_id": str(outcome.build_id) if outcome.build_id else None,
                                "repetition": repetition,
                                "variant": (
                                    "patched"
                                    if item.range.patched
                                    else "selective"
                                    if item.range.patched_properties
                                    else "vulnerable"
                                ),
                                "evaluation": outcome.evaluation.model_dump(mode="json"),
                                "model_usage": {
                                    "calls": len(calls),
                                    "input_tokens": sum(call.input_tokens for call in calls),
                                    "output_tokens": sum(call.output_tokens for call in calls),
                                    "estimated_cost_usd": sum(
                                        call.estimated_cost_usd or 0 for call in calls
                                    )
                                    if any(call.estimated_cost_usd is not None for call in calls)
                                    else None,
                                },
                                "orchestration": orchestration_metrics(trace).model_dump(
                                    mode="json"
                                ),
                                "failure_reason": outcome.failure_reason,
                            }
                        )
                return reports
            finally:
                await engine.dispose()

        runs = asyncio.run(execute())
    except (
        OSError,
        yaml.YAMLError,
        ValidationError,
        ValueError,
        DockerCommandError,
        SQLAlchemyError,
    ) as exc:
        typer.echo(f"experiment error: {exc}", err=True)
        raise typer.Exit(2) from exc
    typer.echo(json.dumps({"runs": runs}, indent=2))
    if any(run["evaluation"]["status"] != "completed" for run in runs):
        raise typer.Exit(1)


@experiment_app.command("trace")
def experiment_trace(run_id: UUID) -> None:
    """Print one persisted, credential-free experiment event stream for diagnosis."""
    try:
        settings = Settings()
        if settings.database_url is None:
            raise ValueError("OFFSECGYM_DATABASE_URL is required for experiment events")
        if not settings.database_url.startswith("postgresql+asyncpg://"):
            raise ValueError("OFFSECGYM_DATABASE_URL must use postgresql+asyncpg")

        async def load_trace() -> list[dict[str, object]]:
            engine = create_async_engine(settings.database_url)
            try:
                events = await PostgresEventStore(engine).read_run(run_id)
                if not events:
                    raise ValueError(f"no events found for run {run_id}")
                return [item.model_dump(mode="json") for item in events]
            finally:
                await engine.dispose()

        trace = asyncio.run(load_trace())
    except (OSError, ValueError, SQLAlchemyError) as exc:
        typer.echo(f"experiment error: {exc}", err=True)
        raise typer.Exit(2) from exc
    typer.echo(json.dumps({"run_id": str(run_id), "events": trace}, indent=2))


@experiment_app.command("worldview")
def experiment_worldview(
    run_id: UUID,
    predicate: str | None = typer.Option(None, help="Exact fact predicate"),
    kind: str | None = typer.Option(None, help="Fact kind, such as observation or hypothesis"),
    limit: int = typer.Option(50, min=1, max=100),
) -> None:
    """Inspect reconstructed shared facts and coverage for one run."""
    try:
        settings = Settings()
        if settings.database_url is None or not settings.database_url.startswith(
            "postgresql+asyncpg://"
        ):
            raise ValueError("OFFSECGYM_DATABASE_URL must use postgresql+asyncpg")
        if kind is not None and kind not in {
            "observation",
            "hypothesis",
            "relationship",
            "finding",
            "open_question",
        }:
            raise ValueError("kind must be a supported world fact kind")

        async def load_worldview() -> dict[str, object]:
            engine = create_async_engine(settings.database_url)
            try:
                events = PostgresEventStore(engine)
                if not await events.read_run(run_id):
                    raise ValueError(f"no events found for run {run_id}")
                state = EventWorldState(events)
                facts = await state.query(run_id, predicate, kind=kind)
                coverage = await state.coverage(run_id)
                return {
                    "run_id": str(run_id),
                    "facts": [fact.model_dump(mode="json") for fact in facts[:limit]],
                    "entities": EntityLedger(list(await state.query(run_id))).as_dict(),
                    "coverage": [claim.model_dump(mode="json") for claim in coverage],
                }
            finally:
                await engine.dispose()

        result = asyncio.run(load_worldview())
    except (OSError, ValueError, WorldStateIntegrityError, SQLAlchemyError) as exc:
        typer.echo(f"experiment error: {exc}", err=True)
        raise typer.Exit(2) from exc
    typer.echo(json.dumps(result, indent=2))


@experiment_app.command("revalidate")
def experiment_revalidate(
    run_id: UUID,
    legacy_m5v1: bool = typer.Option(
        False, help="Apply documented exact M5-v1 category aliases before validation"
    ),
) -> None:
    """Recheck saved findings without changing the original run or its evaluation."""
    try:
        settings = Settings()
        if settings.database_url is None or not settings.database_url.startswith(
            "postgresql+asyncpg://"
        ):
            raise ValueError("OFFSECGYM_DATABASE_URL must use postgresql+asyncpg")

        async def revalidate() -> list[dict[str, object]]:
            engine = create_async_engine(settings.database_url)
            try:
                events = PostgresEventStore(engine)
                trace = await events.read_run(run_id)
                started = next(
                    (
                        event
                        for event in trace
                        if isinstance(event, RangeStarted) and event.schema_version == "2"
                    ),
                    None,
                )
                if (
                    started is None
                    or started.build_id is None
                    or started.range_instance_id is None
                    or started.range_generation is None
                ):
                    raise ValueError("run has no versioned range context")
                findings = [event.finding for event in trace if isinstance(event, FindingSubmitted)]
                if not findings:
                    raise ValueError("run has no submitted findings")
                previous = {
                    event.result.finding_id: event.result
                    for event in trace
                    if isinstance(event, FindingValidated)
                }
                context = ValidationContext(
                    run_id=run_id,
                    build_id=started.build_id,
                    range_instance_id=started.range_instance_id,
                    range_generation=started.range_generation,
                )
                runtime = ComposeRangeRuntime(settings.state_dir)
                gateway = ComposeActionGateway(runtime, events)
                validator = DeterministicValidator(
                    runtime.state,
                    events,
                    StateOracleStore(runtime.state),
                    replay=CloneReplayVerifier(runtime, gateway, events),
                )
                output = []
                for original in findings:
                    finding, changes = (
                        normalize_legacy_m5v1(original) if legacy_m5v1 else (original, {})
                    )
                    result = await validator.validate(finding, context)
                    old = previous.get(original.finding_id)
                    output.append(
                        {
                            "finding_id": str(original.finding_id),
                            "normalization": changes,
                            "previous_status": old.status if old else None,
                            "previous_reasons": list(old.reason_codes) if old else [],
                            "new_status": result.status,
                            "new_reasons": list(result.reason_codes),
                            "matched_root_cause_id": (
                                str(result.matched_root_cause_id)
                                if result.matched_root_cause_id
                                else None
                            ),
                        }
                    )
                return output
            finally:
                await engine.dispose()

        results = asyncio.run(revalidate())
    except (OSError, ValueError, SQLAlchemyError, DockerCommandError) as exc:
        typer.echo(f"experiment error: {exc}", err=True)
        raise typer.Exit(2) from exc
    typer.echo(
        json.dumps(
            {
                "run_id": str(run_id),
                "normalization_version": "legacy_m5v1" if legacy_m5v1 else None,
                "results": results,
            },
            indent=2,
        )
    )


@range_app.command("build")
def range_build(
    path: Path, seed: int | None = typer.Option(None, min=0, help="Override the spec seed")
) -> None:
    """Compile a deterministic range bundle from a YAML spec."""

    try:
        spec = _load_spec(path, RangeSpec)
        if seed is not None:
            spec = RangeSpec.model_validate({**spec.model_dump(mode="python"), "seed": seed})
        build_id = asyncio.run(_runtime().build(spec))
    except (OSError, yaml.YAMLError, ValidationError, ValueError) as exc:
        _fail(exc)
    typer.echo(f"build_id={build_id}")


@range_app.command("start")
def range_start(
    source: str, seed: int | None = typer.Option(None, min=0, help="Override a spec seed")
) -> None:
    """Start a new range from a spec, or resume an instance ID."""

    try:
        path = Path(source)
        if path.is_file():
            spec = _load_spec(path, RangeSpec)
            if seed is not None:
                spec = RangeSpec.model_validate({**spec.model_dump(mode="python"), "seed": seed})
            runtime = _runtime()
            build_id = asyncio.run(runtime.build(spec))
            instance_id = asyncio.run(runtime.create_instance(build_id))
        else:
            if seed is not None:
                raise ValueError("--seed applies only when starting from a spec file")
            instance_id = UUID(source)
            runtime = _runtime()
        status = asyncio.run(runtime.start_instance(instance_id))
    except (
        OSError,
        yaml.YAMLError,
        ValidationError,
        ValueError,
        DockerCommandError,
        TimeoutError,
    ) as exc:
        _fail(exc)
    typer.echo(
        f"instance_id={status.instance_id} generation={status.generation} state={status.state}"
    )


@range_app.command("create")
def range_create(build_id: str) -> None:
    """Create a stopped instance from an existing build ID."""

    try:
        instance_id = asyncio.run(_runtime().create_instance(UUID(build_id)))
    except (OSError, ValueError) as exc:
        _fail(exc)
    typer.echo(f"instance_id={instance_id} generation=0 state=stopped")


@range_app.command("inspect-build")
def range_inspect_build(build_id: str) -> None:
    """Verify and print a build manifest."""

    try:
        manifest = _runtime().state.verify_build_integrity(UUID(build_id))
    except (OSError, ValueError) as exc:
        _fail(exc)
    typer.echo(manifest.model_dump_json(indent=2))


@range_app.command("status")
def range_status(instance_id: str) -> None:
    """Inspect a range instance."""

    try:
        status = asyncio.run(_runtime().instance_status(UUID(instance_id)))
    except (OSError, ValueError, DockerCommandError, TimeoutError) as exc:
        _fail(exc)
    typer.echo(
        f"instance_id={status.instance_id} generation={status.generation} state={status.state}"
    )


@range_app.command("metadata")
def range_metadata(instance_id: str) -> None:
    """Print a range instance metadata snapshot."""

    try:
        metadata = asyncio.run(_runtime().snapshot_metadata(UUID(instance_id)))
    except (OSError, ValueError, DockerCommandError, TimeoutError) as exc:
        _fail(exc)
    typer.echo(metadata.model_dump_json(indent=2))


@range_app.command("stop")
def range_stop(instance_id: str) -> None:
    """Stop a range while retaining its containers."""

    try:
        status = asyncio.run(_runtime().stop_instance(UUID(instance_id)))
    except (OSError, ValueError, DockerCommandError, TimeoutError) as exc:
        _fail(exc)
    typer.echo(
        f"instance_id={status.instance_id} generation={status.generation} state={status.state}"
    )


@range_app.command("reset")
def range_reset(instance_id: str) -> None:
    """Recreate an instance with the same build and fresh target state."""

    try:
        status = asyncio.run(_runtime().reset_instance(UUID(instance_id)))
    except (OSError, ValueError, DockerCommandError, TimeoutError) as exc:
        _fail(exc)
    typer.echo(
        f"instance_id={status.instance_id} generation={status.generation} state={status.state}"
    )


@range_app.command("destroy")
def range_destroy(instance_id: str) -> None:
    """Remove only this instance's containers, network, and volumes."""

    try:
        status = asyncio.run(_runtime().destroy_instance(UUID(instance_id)))
    except (OSError, ValueError, DockerCommandError, TimeoutError) as exc:
        _fail(exc)
    typer.echo(
        f"instance_id={status.instance_id} generation={status.generation} state={status.state}"
    )


if __name__ == "__main__":
    app()
