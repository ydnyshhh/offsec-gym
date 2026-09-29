"""OffSecGym command-line skeleton."""

from __future__ import annotations

from pathlib import Path

import typer
import yaml
from pydantic import ValidationError

from offsecgym import __version__
from offsecgym.schemas.specs import ExperimentSpec, RangeSpec

app = typer.Typer(help="Synthetic-range research platform")
range_app = typer.Typer(help="Range lifecycle (available in Milestone 1)")
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
        raw = yaml.safe_load(path.read_text(encoding="utf-8"))
        schema = RangeSpec if kind == "range" else ExperimentSpec
        spec = schema.model_validate(raw)
    except (OSError, yaml.YAMLError, ValidationError, ValueError) as exc:
        typer.echo(f"invalid {kind} specification: {exc}", err=True)
        raise typer.Exit(2) from exc
    typer.echo(f"valid {kind} specification (schema_version={spec.schema_version})")


def _not_implemented() -> None:
    typer.echo("range lifecycle is scheduled for Milestone 1", err=True)
    raise typer.Exit(2)


@range_app.command("build")
def range_build(path: Path) -> None:
    """Build a range from a spec (Milestone 1)."""

    _not_implemented()


@range_app.command("start")
def range_start(path: Path) -> None:
    """Start a range from a spec (Milestone 1)."""

    _not_implemented()


@range_app.command("status")
def range_status(range_id: str) -> None:
    """Show range status (Milestone 1)."""

    _not_implemented()


@range_app.command("stop")
def range_stop(range_id: str) -> None:
    """Stop a range (Milestone 1)."""

    _not_implemented()


@range_app.command("destroy")
def range_destroy(range_id: str) -> None:
    """Destroy a range (Milestone 1)."""

    _not_implemented()


if __name__ == "__main__":
    app()
