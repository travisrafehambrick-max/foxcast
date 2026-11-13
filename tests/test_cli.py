"""The command line: init, doctor, ingest, dossier and check-claims."""

from datetime import date
from pathlib import Path

import pytest
from typer.testing import CliRunner

from foxcast.cli import app as cli_app
from foxcast.doctor import CheckResult, Status
from foxcast.sec_ingest import IngestReport

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


@pytest.mark.unit
def test_verify_ledger_reports_ok_and_broken(tmp_path: Path) -> None:
    from foxcast.ledger.chain import Ledger

    ledger_path = tmp_path / "ledger.jsonl"
    Ledger(ledger_path).append(kind="forecast", request_id="r1", payload={"x": 1})
    result = runner.invoke(cli_app.app, ["verify-ledger", "--ledger", str(ledger_path)])
    assert result.exit_code == 0, result.output
    assert "1 record" in result.output

    ledger_path.write_text(ledger_path.read_text().replace('"x":1', '"x":2'))
    result = runner.invoke(cli_app.app, ["verify-ledger", "--ledger", str(ledger_path)])
    assert result.exit_code == 1
    assert "changed" in result.output


@pytest.mark.unit
def test_on_demand_run_exists_and_there_is_no_scheduler() -> None:
    commands = {command.name for command in cli_app.app.registered_commands}
    assert "run" in commands
    assert "schedule" not in commands
    assert "daily" not in commands


@pytest.mark.unit
def test_ingest_command_is_registered() -> None:
    names = {
        command.name or (command.callback.__name__ if command.callback else "")
        for command in cli_app.app.registered_commands
    }
    assert "ingest" in names


@pytest.mark.unit
def test_ingest_refuses_missing_sec_contact(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config = tmp_path / "foxcast.toml"
    config.write_text(f'data_dir = "{(tmp_path / "data").as_posix()}"\n')
    monkeypatch.delenv("FOXCAST_SEC_CONTACT", raising=False)
    result = runner.invoke(
        cli_app.app, ["ingest", "GOOGL", "--as-of", "2026-11-11", "--config", str(config)]
    )
    assert result.exit_code == 1
    assert "FOXCAST_SEC_CONTACT" in result.output


@pytest.mark.unit
def test_ingest_calls_library_with_cik(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Ingest opens SourceArchive.outside_git(settings.archive_dir), not data/sources."""
    from foxcast.store import archive as archive_module

    config = tmp_path / "foxcast.toml"
    data = tmp_path / "data"
    archive_dir = tmp_path / "archive"
    config.write_text(f'data_dir = "{data.as_posix()}"\narchive_dir = "{archive_dir.as_posix()}"\n')
    monkeypatch.setenv("FOXCAST_SEC_CONTACT", "Jane Doe jane@example.com")
    # pytest basetemp sits inside this repo; stand in for a folder outside git.
    monkeypatch.setattr(archive_module, "git_work_tree_of", lambda _: None)

    calls: list[tuple[int, date]] = []
    archives: list[object] = []

    def fake_ingest(
        cik: int,
        *,
        as_of: date,
        client: object,
        contact: str,
        store: object,
        archive: object,
        **_: object,
    ) -> IngestReport:
        calls.append((cik, as_of))
        archives.append(archive)
        assert contact == "Jane Doe jane@example.com"
        assert store is not None and archive is not None
        return IngestReport(
            cik=cik,
            as_of=as_of,
            index_complete=True,
            documents=("10-K 1",),
            passages=3,
            text_facts=2,
            numbers=4,
            unavailable=["XBRL NetIncomeLoss: 404"],
        )

    monkeypatch.setattr("foxcast.sec_ingest.ingest_company", fake_ingest)
    result = runner.invoke(
        cli_app.app,
        [
            "ingest",
            "GOOGL",
            "--as-of",
            "2026-11-11",
            "--cik",
            "1652044",
            "--config",
            str(config),
        ],
    )
    assert result.exit_code == 0, result.output
    assert calls == [(1652044, date(2026, 11, 11))]
    assert "1652044" in result.output
    assert "3 passages" in result.output
    assert "unavailable: XBRL NetIncomeLoss: 404" in result.output
    assert (data / "facts.duckdb").exists()
    assert archives and archives[0]._directory == archive_dir
    assert not (data / "sources").exists()


@pytest.mark.unit
def test_ingest_refuses_archive_inside_a_git_data_repo(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Ingest must fail when archive_dir is under a git work tree (e.g. data/sources)."""
    config = tmp_path / "foxcast.toml"
    data = tmp_path / "data"
    data.mkdir()
    (data / ".git").mkdir()
    config.write_text(
        f'data_dir = "{data.as_posix()}"\narchive_dir = "{(data / "sources").as_posix()}"\n'
    )
    monkeypatch.setenv("FOXCAST_SEC_CONTACT", "Jane Doe jane@example.com")
    result = runner.invoke(
        cli_app.app,
        ["ingest", "GOOGL", "--as-of", "2026-11-11", "--cik", "1", "--config", str(config)],
    )
    assert result.exit_code == 1
    assert "git" in result.output.lower()


@pytest.mark.unit
def test_dossier_command_is_registered() -> None:
    names = {
        command.name or (command.callback.__name__ if command.callback else "")
        for command in cli_app.app.registered_commands
    }
    assert "dossier" in names


@pytest.mark.unit
def test_dossier_refuses_missing_fact_store(tmp_path: Path) -> None:
    config = tmp_path / "foxcast.toml"
    data = tmp_path / "data"
    archive_dir = tmp_path / "archive"
    config.write_text(f'data_dir = "{data.as_posix()}"\narchive_dir = "{archive_dir.as_posix()}"\n')
    result = runner.invoke(
        cli_app.app,
        ["dossier", "GOOGL", "--as-of", "2026-11-12", "--cik", "1652044", "--config", str(config)],
    )
    assert result.exit_code == 1
    assert "fact store" in result.output.lower()


@pytest.mark.unit
def test_dossier_writes_markdown_and_json(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from foxcast.store import archive as archive_module
    from foxcast.store.pit import FactStore

    config = tmp_path / "foxcast.toml"
    data = tmp_path / "data"
    archive_dir = tmp_path / "archive"
    out = tmp_path / "out"
    config.write_text(f'data_dir = "{data.as_posix()}"\narchive_dir = "{archive_dir.as_posix()}"\n')
    data.mkdir()
    FactStore(data / "facts.duckdb")  # empty record still yields 19 sections
    monkeypatch.setattr(archive_module, "git_work_tree_of", lambda _: None)

    result = runner.invoke(
        cli_app.app,
        [
            "dossier",
            "GOOGL",
            "--as-of",
            "2026-11-12",
            "--cik",
            "1652044",
            "--company",
            "Alphabet Inc.",
            "--out",
            str(out),
            "--config",
            str(config),
        ],
    )
    assert result.exit_code == 0, result.output
    assert "unavailable" in result.output
    assert (out / "GOOGL-2026-11-12.md").exists()
    assert (out / "GOOGL-2026-11-12.json").exists()
    markdown = (out / "GOOGL-2026-11-12.md").read_text(encoding="utf-8")
    # Markdown escapes the period in "Inc."
    assert "Alphabet Inc\\." in markdown
    assert '"company": "Alphabet Inc."' in (out / "GOOGL-2026-11-12.json").read_text(
        encoding="utf-8"
    )


@pytest.mark.unit
def test_dossier_refuses_archive_inside_a_git_work_tree(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from foxcast.store.pit import FactStore

    config = tmp_path / "foxcast.toml"
    data = tmp_path / "data"
    data.mkdir()
    (data / ".git").mkdir()
    FactStore(data / "facts.duckdb")
    config.write_text(
        f'data_dir = "{data.as_posix()}"\narchive_dir = "{(data / "sources").as_posix()}"\n'
    )
    monkeypatch.setenv("FOXCAST_SEC_CONTACT", "Jane Doe jane@example.com")
    result = runner.invoke(
        cli_app.app,
        ["dossier", "GOOGL", "--as-of", "2026-11-12", "--cik", "1", "--config", str(config)],
    )
    assert result.exit_code == 1
    assert "git" in result.output.lower()


@pytest.mark.unit
def test_check_claims_command_is_registered() -> None:
    names = {
        command.name or (command.callback.__name__ if command.callback else "")
        for command in cli_app.app.registered_commands
    }
    assert "check-claims" in names


@pytest.mark.unit
def test_check_claims_refuses_missing_fact_store(tmp_path: Path) -> None:
    config = tmp_path / "foxcast.toml"
    data = tmp_path / "data"
    archive_dir = tmp_path / "archive"
    claims = tmp_path / "claims.json"
    claims.write_text("[]\n", encoding="utf-8")
    config.write_text(f'data_dir = "{data.as_posix()}"\narchive_dir = "{archive_dir.as_posix()}"\n')
    result = runner.invoke(
        cli_app.app,
        [
            "check-claims",
            "GOOGL",
            "--as-of",
            "2026-11-12",
            "--cik",
            "1652044",
            "--claims",
            str(claims),
            "--config",
            str(config),
        ],
    )
    assert result.exit_code == 1
    assert "fact store" in result.output.lower()


@pytest.mark.unit
def test_check_claims_empty_file_reports_zero(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from foxcast.store import archive as archive_module
    from foxcast.store.pit import FactStore

    config = tmp_path / "foxcast.toml"
    data = tmp_path / "data"
    archive_dir = tmp_path / "archive"
    claims = tmp_path / "claims.json"
    out = tmp_path / "report.json"
    claims.write_text("[]\n", encoding="utf-8")
    config.write_text(f'data_dir = "{data.as_posix()}"\narchive_dir = "{archive_dir.as_posix()}"\n')
    data.mkdir()
    FactStore(data / "facts.duckdb")
    monkeypatch.setattr(archive_module, "git_work_tree_of", lambda _: None)

    result = runner.invoke(
        cli_app.app,
        [
            "check-claims",
            "GOOGL",
            "--as-of",
            "2026-11-12",
            "--cik",
            "1652044",
            "--claims",
            str(claims),
            "--out",
            str(out),
            "--config",
            str(config),
        ],
    )
    assert result.exit_code == 0, result.output
    assert "0 verified" in result.output
    assert "0 withheld" in result.output
    assert out.exists()
    report = out.read_text(encoding="utf-8")
    assert '"counted": 0' in report
    assert '"verified": 0' in report


@pytest.mark.unit
def test_check_claims_refuses_bad_claims_json(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from foxcast.store import archive as archive_module
    from foxcast.store.pit import FactStore

    config = tmp_path / "foxcast.toml"
    data = tmp_path / "data"
    archive_dir = tmp_path / "archive"
    claims = tmp_path / "claims.json"
    claims.write_text('{"nope": true}\n', encoding="utf-8")
    config.write_text(f'data_dir = "{data.as_posix()}"\narchive_dir = "{archive_dir.as_posix()}"\n')
    data.mkdir()
    FactStore(data / "facts.duckdb")
    monkeypatch.setattr(archive_module, "git_work_tree_of", lambda _: None)

    result = runner.invoke(
        cli_app.app,
        [
            "check-claims",
            "GOOGL",
            "--as-of",
            "2026-11-12",
            "--cik",
            "1652044",
            "--claims",
            str(claims),
            "--config",
            str(config),
        ],
    )
    assert result.exit_code == 1
    assert "claims" in result.output.lower()


@pytest.mark.unit
def test_check_claims_object_form_keeps_subject_cik(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """``{claims, companies}`` is accepted; CLI subject ticker CIK wins over a colliding key."""
    import json

    from foxcast.claims.checker import WORDS_CHECK_VERSION, DossierCheck
    from foxcast.store import archive as archive_module
    from foxcast.store.pit import FactStore

    config = tmp_path / "foxcast.toml"
    data = tmp_path / "data"
    archive_dir = tmp_path / "archive"
    claims = tmp_path / "claims.json"
    claims.write_text(
        json.dumps(
            {
                "claims": [],
                "companies": {"GOOGL": 999, "Alphabet": 1652044},
            }
        ),
        encoding="utf-8",
    )
    config.write_text(f'data_dir = "{data.as_posix()}"\narchive_dir = "{archive_dir.as_posix()}"\n')
    data.mkdir()
    FactStore(data / "facts.duckdb")
    monkeypatch.setattr(archive_module, "git_work_tree_of", lambda _: None)

    captured: dict[str, object] = {}

    def fake_check(reviewed: object, context: object) -> DossierCheck:
        captured["companies"] = dict(context.companies)  # type: ignore[attr-defined]
        return DossierCheck((), (), 0, 0, WORDS_CHECK_VERSION)

    monkeypatch.setattr("foxcast.claims.checker.check_dossier", fake_check)

    result = runner.invoke(
        cli_app.app,
        [
            "check-claims",
            "GOOGL",
            "--as-of",
            "2026-11-12",
            "--cik",
            "1652044",
            "--company",
            "Alphabet Inc.",
            "--claims",
            str(claims),
            "--config",
            str(config),
        ],
    )
    assert result.exit_code == 0, result.output
    companies = captured["companies"]
    assert isinstance(companies, dict)
    assert companies["GOOGL"] == 1652044
    assert companies["Alphabet"] == 1652044
    assert companies["Alphabet Inc."] == 1652044


@pytest.mark.unit
def test_check_claims_withholds_and_exits_one(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A claim that cites a missing fact ID is withheld (dossier check path)."""
    import json

    from foxcast.store import archive as archive_module
    from foxcast.store.pit import FactStore

    config = tmp_path / "foxcast.toml"
    data = tmp_path / "data"
    archive_dir = tmp_path / "archive"
    claims = tmp_path / "claims.json"
    missing = "f_" + ("0" * 16)
    claims.write_text(
        json.dumps(
            [
                {
                    "section": "financial_health",
                    "type": "fact",
                    "text": f"Revenue was {{{missing}}}.",
                    "cites": [missing],
                }
            ]
        ),
        encoding="utf-8",
    )
    config.write_text(f'data_dir = "{data.as_posix()}"\narchive_dir = "{archive_dir.as_posix()}"\n')
    data.mkdir()
    FactStore(data / "facts.duckdb")
    monkeypatch.setattr(archive_module, "git_work_tree_of", lambda _: None)

    result = runner.invoke(
        cli_app.app,
        [
            "check-claims",
            "GOOGL",
            "--as-of",
            "2026-11-12",
            "--cik",
            "1652044",
            "--company",
            "Alphabet",
            "--claims",
            str(claims),
            "--config",
            str(config),
        ],
    )
    assert result.exit_code == 1, result.output
    assert "0 verified" in result.output
    assert "1 withheld" in result.output
    assert "withheld" in result.output


@pytest.mark.unit
def test_check_claims_refuses_archive_inside_a_git_work_tree(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from foxcast.store.pit import FactStore

    config = tmp_path / "foxcast.toml"
    data = tmp_path / "data"
    claims = tmp_path / "claims.json"
    claims.write_text("[]\n", encoding="utf-8")
    data.mkdir()
    (data / ".git").mkdir()
    FactStore(data / "facts.duckdb")
    config.write_text(
        f'data_dir = "{data.as_posix()}"\narchive_dir = "{(data / "sources").as_posix()}"\n'
    )
    monkeypatch.setenv("FOXCAST_SEC_CONTACT", "Jane Doe jane@example.com")
    result = runner.invoke(
        cli_app.app,
        [
            "check-claims",
            "GOOGL",
            "--as-of",
            "2026-11-12",
            "--cik",
            "1",
            "--claims",
            str(claims),
            "--config",
            str(config),
        ],
    )
    assert result.exit_code == 1
    assert "git" in result.output.lower()
