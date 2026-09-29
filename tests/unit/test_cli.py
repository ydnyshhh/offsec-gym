from pathlib import Path

from typer.testing import CliRunner

from offsecgym.cli.main import app

runner = CliRunner()


def test_validate_hello_spec() -> None:
    path = Path(__file__).parents[2] / "ranges" / "hello" / "hello-range.yaml"
    result = runner.invoke(app, ["spec", "validate", str(path)])
    assert result.exit_code == 0, result.output
    assert "valid range specification" in result.output


def test_range_build_and_status(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("OFFSECGYM_STATE_DIR", str(tmp_path))
    path = Path(__file__).parents[2] / "ranges" / "hello" / "hello-range.yaml"
    built = runner.invoke(app, ["range", "build", str(path)])
    assert built.exit_code == 0, built.output
    build_id = built.output.strip().split("=", 1)[1]
    status = runner.invoke(app, ["range", "status", build_id])
    assert status.exit_code == 0, status.output
    assert "state=built" in status.output
