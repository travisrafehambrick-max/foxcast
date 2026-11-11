"""The command line: init and doctor."""

from pathlib import Path

import pytest
from typer.testing import CliRunner

from foxcast.cli import app as cli_app
from foxcast.doctor import CheckResult, Status

runner = CliRunner()


@pytest.mark.unit
def test_init_writes_a_config_file(tmp_path: Path) -> None:
    config = tmp_path / "foxcast.toml"
    result = runner.invoke(
        cli_app.app, ["init", "--config", str(config), "--data-dir", str(tmp_path / "d")]
    )
    assert result.exit_code == 0, result.output
    assert config.exists()


@pytest.mark.unit
def test_init_refuses_to_overwrite(tmp_path: Path) -> None:
    config = tmp_path / "foxcast.toml"
    config.write_text("watchlist_size = 3\n")
    result = runner.invoke(cli_app.app, ["init", "--config", str(config)])
    assert result.exit_code == 1
    assert config.read_text() == "watchlist_size = 3\n"


@pytest.mark.unit
def test_doctor_exit_code_follows_failures(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    passing = [CheckResult("python", Status.PASS, "ok"), CheckResult("fred", Status.WARN, "no")]
    monkeypatch.setattr(cli_app, "run_checks", lambda config, client: passing)
    result = runner.invoke(cli_app.app, ["doctor", "--config", str(tmp_path / "c.toml")])
    assert result.exit_code == 0, result.output
    assert "python" in result.output

    failing = [*passing, CheckResult("sec reachable", Status.FAIL, "blocked")]
    monkeypatch.setattr(cli_app, "run_checks", lambda config, client: failing)
    result = runner.invoke(cli_app.app, ["doctor", "--config", str(tmp_path / "c.toml")])
    assert result.exit_code == 1
    assert "blocked" in result.output
