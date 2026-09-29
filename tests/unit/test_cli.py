from pathlib import Path

from typer.testing import CliRunner

from offsecgym.cli.main import app

runner = CliRunner()


def test_validate_hello_spec() -> None:
    path = Path(__file__).parents[2] / "ranges" / "hello" / "hello-range.yaml"
    result = runner.invoke(app, ["spec", "validate", str(path)])
    assert result.exit_code == 0, result.output
    assert "valid range specification" in result.output


def test_range_build_and_inspect(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("OFFSECGYM_STATE_DIR", str(tmp_path))
    path = Path(__file__).parents[2] / "ranges" / "hello" / "hello-range.yaml"
    built = runner.invoke(app, ["range", "build", str(path)])
    assert built.exit_code == 0, built.output
    build_id = built.output.strip().split("=", 1)[1]
    inspected = runner.invoke(app, ["range", "inspect-build", build_id])
    assert inspected.exit_code == 0, inspected.output
    assert f'"build_id": "{build_id}"' in inspected.output
    status = runner.invoke(app, ["range", "status", build_id])
    assert status.exit_code == 2
    assert "unknown range instance" in status.output
    created = runner.invoke(app, ["range", "create", build_id])
    assert created.exit_code == 0, created.output
    instance_id = created.output.split()[0].split("=", 1)[1]
    assert instance_id != build_id
    assert "generation=0" in created.output


def test_saas_seed_override_changes_build_id(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("OFFSECGYM_STATE_DIR", str(tmp_path))
    path = Path(__file__).parents[2] / "examples" / "saas-range.yaml"
    default = runner.invoke(app, ["range", "build", str(path)])
    alternate = runner.invoke(app, ["range", "build", str(path), "--seed", "43"])
    assert default.exit_code == alternate.exit_code == 0
    assert default.output != alternate.output
