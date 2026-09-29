from pathlib import Path

from typer.testing import CliRunner

from offsecgym.cli.main import app

runner = CliRunner()


def test_validate_hello_spec() -> None:
    path = Path(__file__).parents[2] / "ranges" / "hello" / "hello-range.yaml"
    result = runner.invoke(app, ["spec", "validate", str(path)])
    assert result.exit_code == 0, result.output
    assert "valid range specification" in result.output


def test_range_commands_report_milestone_boundary() -> None:
    result = runner.invoke(app, ["range", "start", "ranges/hello/hello-range.yaml"])
    assert result.exit_code == 2
    assert "Milestone 1" in result.output
