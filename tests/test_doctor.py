"""foxcast doctor: every check has a pass and a fail path."""

from pathlib import Path

import httpx
import pytest

from foxcast.config import load_settings
from foxcast.doctor import (
    Status,
    check_config,
    check_disk_space,
    check_fred_key,
    check_python,
    check_sec_contact,
    check_sec_reachable,
    run_checks,
)


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in ("FOXCAST_SEC_CONTACT", "FOXCAST_FRED_API_KEY", "FOXCAST_DATA_DIR"):
        monkeypatch.delenv(name, raising=False)


def client_returning(status_code: int) -> httpx.Client:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.headers["User-Agent"] == "Jane Doe jane@example.com"
        return httpx.Response(status_code)

    return httpx.Client(transport=httpx.MockTransport(handler))


def failing_client() -> httpx.Client:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("no route", request=request)

    return httpx.Client(transport=httpx.MockTransport(handler))


@pytest.mark.unit
def test_python_check_passes_on_supported_and_fails_on_unsupported() -> None:
    assert check_python((3, 13, 1)).status is Status.PASS
    assert check_python((3, 12, 0)).status is Status.PASS
    assert check_python((3, 11, 9)).status is Status.FAIL
    assert check_python((3, 14, 0)).status is Status.FAIL


@pytest.mark.unit
def test_config_check(tmp_path: Path) -> None:
    assert check_config(tmp_path / "missing.toml").status is Status.WARN
    good = tmp_path / "good.toml"
    good.write_text("watchlist_size = 5\n")
    assert check_config(good).status is Status.PASS
    bad = tmp_path / "bad.toml"
    bad.write_text("watchlist_size = [\n")
    assert check_config(bad).status is Status.FAIL


@pytest.mark.unit
def test_sec_contact_check(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    assert check_sec_contact(load_settings(tmp_path / "x.toml")).status is Status.FAIL
    monkeypatch.setenv("FOXCAST_SEC_CONTACT", "no-email-here")
    assert check_sec_contact(load_settings(tmp_path / "x.toml")).status is Status.FAIL
    monkeypatch.setenv("FOXCAST_SEC_CONTACT", "Jane Doe jane@example.com")
    assert check_sec_contact(load_settings(tmp_path / "x.toml")).status is Status.PASS


@pytest.mark.unit
def test_fred_key_check_warns_when_missing(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    assert check_fred_key(load_settings(tmp_path / "x.toml")).status is Status.WARN
    monkeypatch.setenv("FOXCAST_FRED_API_KEY", "abc")
    result = check_fred_key(load_settings(tmp_path / "x.toml"))
    assert result.status is Status.PASS
    assert "abc" not in result.detail


@pytest.mark.unit
def test_disk_space_check(tmp_path: Path) -> None:
    assert check_disk_space(tmp_path, minimum_bytes=1).status is Status.PASS
    assert check_disk_space(tmp_path, minimum_bytes=10**18).status is Status.FAIL
    assert check_disk_space(tmp_path / "not" / "yet", minimum_bytes=1).status is Status.PASS


@pytest.mark.unit
def test_sec_reachable_check(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("FOXCAST_SEC_CONTACT", "Jane Doe jane@example.com")
    settings = load_settings(tmp_path / "x.toml")
    assert check_sec_reachable(settings, client_returning(200)).status is Status.PASS
    assert check_sec_reachable(settings, client_returning(403)).status is Status.FAIL
    assert check_sec_reachable(settings, failing_client()).status is Status.FAIL


@pytest.mark.unit
def test_sec_reachable_is_skipped_without_a_contact(tmp_path: Path) -> None:
    settings = load_settings(tmp_path / "x.toml")
    assert check_sec_reachable(settings, client_returning(200)).status is Status.FAIL


@pytest.mark.unit
def test_run_checks_returns_every_check(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("FOXCAST_SEC_CONTACT", "Jane Doe jane@example.com")
    config = tmp_path / "foxcast.toml"
    config.write_text(f'data_dir = "{tmp_path.as_posix()}"\n')
    results = run_checks(config, client_returning(200))
    names = [r.name for r in results]
    assert names == ["python", "config", "sec contact", "fred key", "disk space", "sec reachable"]
