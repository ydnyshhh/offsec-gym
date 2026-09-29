"""OffSecGym command-line skeleton."""

from __future__ import annotations

import asyncio
from pathlib import Path
from uuid import UUID

import typer
import yaml
from pydantic import ValidationError

from offsecgym import __version__
from offsecgym.config import Settings
from offsecgym.runtime.compose import ComposeRangeRuntime, DockerCommandError
from offsecgym.schemas.specs import ExperimentSpec, RangeSpec

app = typer.Typer(help="Synthetic-range research platform")
range_app = typer.Typer(help="Build and manage isolated synthetic ranges")
spec_app = typer.Typer(help="Versioned input specifications")
app.add_typer(range_app, name="range")
app.add_typer(spec_app, name="spec")


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
    """Start a new range from a spec/build ID, or resume an instance ID."""

    try:
        path = Path(source)
        if path.is_file():
            spec = _load_spec(path, RangeSpec)
            if seed is not None:
                spec = RangeSpec.model_validate({**spec.model_dump(mode="python"), "seed": seed})
            build_id = asyncio.run(_runtime().build(spec))
            source_id = build_id
        else:
            if seed is not None:
                raise ValueError("--seed applies only when starting from a spec file")
            source_id = UUID(source)
        status = asyncio.run(_runtime().start(source_id))
    except (
        OSError,
        yaml.YAMLError,
        ValidationError,
        ValueError,
        DockerCommandError,
        TimeoutError,
    ) as exc:
        _fail(exc)
    typer.echo(f"range_id={status.range_id} state={status.state}")


@range_app.command("status")
def range_status(range_id: str) -> None:
    """Inspect a build or range instance."""

    try:
        status = asyncio.run(_runtime().status(UUID(range_id)))
    except (OSError, ValueError, DockerCommandError, TimeoutError) as exc:
        _fail(exc)
    typer.echo(f"range_id={status.range_id} state={status.state}")


@range_app.command("metadata")
def range_metadata(range_id: str) -> None:
    """Print a range instance metadata snapshot."""

    try:
        metadata = asyncio.run(_runtime().snapshot_metadata(UUID(range_id)))
    except (OSError, ValueError, DockerCommandError, TimeoutError) as exc:
        _fail(exc)
    typer.echo(metadata.model_dump_json(indent=2))


@range_app.command("stop")
def range_stop(range_id: str) -> None:
    """Stop a range while retaining its containers."""

    try:
        status = asyncio.run(_runtime().stop(UUID(range_id)))
    except (OSError, ValueError, DockerCommandError, TimeoutError) as exc:
        _fail(exc)
    typer.echo(f"range_id={status.range_id} state={status.state}")


@range_app.command("reset")
def range_reset(range_id: str) -> None:
    """Recreate an instance with the same build and fresh target state."""

    try:
        status = asyncio.run(_runtime().reset(UUID(range_id)))
    except (OSError, ValueError, DockerCommandError, TimeoutError) as exc:
        _fail(exc)
    typer.echo(f"range_id={status.range_id} state={status.state}")


@range_app.command("destroy")
def range_destroy(range_id: str) -> None:
    """Remove only this instance's containers, network, and volumes."""

    try:
        status = asyncio.run(_runtime().destroy(UUID(range_id)))
    except (OSError, ValueError, DockerCommandError, TimeoutError) as exc:
        _fail(exc)
    typer.echo(f"range_id={status.range_id} state={status.state}")


if __name__ == "__main__":
    app()
