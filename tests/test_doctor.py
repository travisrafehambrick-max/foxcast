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
    pass_313 = check_python((3, 13, 1, 0, 0))
    assert pass_313.status is Status.PASS
    assert pass_313.detail == "Python 3.13.1"
    assert check_python((3, 12, 0)).status is Status.PASS
    fail_311 = check_python((3, 11, 9))
    assert fail_311.status is Status.FAIL
    assert "3.11.9" in fail_311.detail and "3.12 and 3.13" in fail_311.detail
    assert check_python((3, 14, 0)).status is Status.FAIL


@pytest.mark.unit
def test_config_check(tmp_path: Path) -> None:
    missing = check_config(tmp_path / "missing.toml")
    assert missing.status is Status.WARN
    assert "foxcast init" in missing.detail

    good = tmp_path / "good.toml"
    good.write_text("watchlist_size = 5\n", encoding="utf-8")
    passed = check_config(good)
    assert passed.status is Status.PASS
    assert str(good) in passed.detail

    bad_toml = tmp_path / "bad.toml"
    bad_toml.write_text("watchlist_size = [\n", encoding="utf-8")
    assert check_config(bad_toml).status is Status.FAIL

    out_of_range = tmp_path / "range.toml"
    out_of_range.write_text("watchlist_size = 501\n", encoding="utf-8")
    failed_range = check_config(out_of_range)
    assert failed_range.status is Status.FAIL
    assert "watchlist_size" in failed_range.detail

    leaked = tmp_path / "leaked.toml"
    leaked.write_text('fred_api_key = "SUPER_SECRET_KEY"\n', encoding="utf-8")
    failed_leak = check_config(leaked)
    assert failed_leak.status is Status.FAIL
    assert "fred_api_key" in failed_leak.detail
    assert "SUPER_SECRET_KEY" not in failed_leak.detail


@pytest.mark.unit
@pytest.mark.parametrize(
    "bad_contact",
    [
        "",
        "no-email-here",
        "Jane Doe @example.com",
        "Jane Doe jane@",
        "Jane Doe jane@localhost",
    ],
)
def test_sec_contact_rejects_missing_or_malformed_emails(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, bad_contact: str
) -> None:
    if bad_contact:
        monkeypatch.setenv("FOXCAST_SEC_CONTACT", bad_contact)
    result = check_sec_contact(load_settings(tmp_path / "x.toml"))
    assert result.status is Status.FAIL


@pytest.mark.unit
def test_sec_contact_accepts_name_and_email(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("FOXCAST_SEC_CONTACT", "Jane Doe jane@example.com")
    result = check_sec_contact(load_settings(tmp_path / "x.toml"))
    assert result.status is Status.PASS
    assert result.detail == "set"


@pytest.mark.unit
def test_fred_key_check_warns_when_missing(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    assert check_fred_key(load_settings(tmp_path / "x.toml")).status is Status.WARN
    monkeypatch.setenv("FOXCAST_FRED_API_KEY", "abc")
    result = check_fred_key(load_settings(tmp_path / "x.toml"))
    assert result.status is Status.PASS
    assert "abc" not in result.detail


@pytest.mark.unit
def test_disk_space_check_and_exact_threshold(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from collections import namedtuple

    Usage = namedtuple("Usage", ["total", "used", "free"])
    threshold = 5 * 1024**3
    monkeypatch.setattr(
        "foxcast.doctor.shutil.disk_usage", lambda p: Usage(threshold * 2, 0, threshold)
    )
    exact = check_disk_space(tmp_path / "nested" / "uncreated", minimum_bytes=threshold)
    assert exact.status is Status.PASS
    assert f"5.0 GB free at {tmp_path}" in exact.detail

    monkeypatch.setattr(
        "foxcast.doctor.shutil.disk_usage", lambda p: Usage(threshold * 2, 0, threshold - 1024**3)
    )
    short = check_disk_space(tmp_path, minimum_bytes=threshold)
    assert short.status is Status.FAIL
    assert "4.0 GB free; need 5.0 GB" in short.detail


@pytest.mark.unit
def test_sec_reachable_uses_head_with_user_agent_and_checks_status(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("FOXCAST_SEC_CONTACT", "Jane Doe jane@example.com")
    settings = load_settings(tmp_path / "x.toml")
    methods_seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        methods_seen.append(request.method)
        assert request.headers["User-Agent"] == "Jane Doe jane@example.com"
        return httpx.Response(204)

    ok = check_sec_reachable(settings, httpx.Client(transport=httpx.MockTransport(handler)))
    assert ok.status is Status.PASS
    assert methods_seen == ["HEAD"]
    assert "204" in ok.detail

    failed_status = check_sec_reachable(settings, client_returning(403))
    assert failed_status.status is Status.FAIL
    assert "403" in failed_status.detail

    failed_net = check_sec_reachable(settings, failing_client())
    assert failed_net.status is Status.FAIL
    assert "could not reach SEC" in failed_net.detail


@pytest.mark.unit
def test_sec_reachable_is_skipped_without_a_contact(tmp_path: Path) -> None:
    settings = load_settings(tmp_path / "x.toml")
    result = check_sec_reachable(settings, client_returning(200))
    assert result.status is Status.FAIL
    assert "no SEC contact" in result.detail


@pytest.mark.unit
def test_run_checks_returns_every_check(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("FOXCAST_SEC_CONTACT", "Jane Doe jane@example.com")
    config = tmp_path / "foxcast.toml"
    config.write_text(f'data_dir = "{tmp_path.as_posix()}"\n', encoding="utf-8")
    results = run_checks(config, client_returning(200))
    names = [r.name for r in results]
    assert names == ["python", "config", "sec contact", "fred key", "disk space", "sec reachable"]
    statuses = {r.name: r.status for r in results}
    assert statuses["config"] is Status.PASS
    assert statuses["sec contact"] is Status.PASS
    assert statuses["fred key"] is Status.WARN
    assert statuses["sec reachable"] is Status.PASS


@pytest.mark.unit
def test_run_checks_continues_on_broken_config_with_safe_defaults(tmp_path: Path) -> None:
    broken = tmp_path / "broken.toml"
    broken.write_text("watchlist_size = [\n", encoding="utf-8")
    results = run_checks(broken, client_returning(200))
    statuses = {r.name: r.status for r in results}
    assert statuses["config"] is Status.FAIL
    assert statuses["sec contact"] is Status.FAIL
    assert statuses["fred key"] is Status.WARN
    assert statuses["sec reachable"] is Status.FAIL
