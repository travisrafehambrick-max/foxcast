"""foxcast doctor: check that everything a run needs is in place, before any run."""

import re
import shutil
import sys
from collections.abc import Sequence
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Final

import httpx
from pydantic import ValidationError

from foxcast.config import Settings, load_settings

SUPPORTED_PYTHON: Final = ((3, 12), (3, 13))
MIN_FREE_BYTES: Final = 5 * 1024**3
SEC_PROBE_URL: Final = "https://www.sec.gov/files/company_tickers.json"
SEC_TIMEOUT_SECONDS: Final = 10.0
EMAIL_PATTERN: Final = re.compile(r"[^@\s]+@[^@\s]+\.[^@\s]+")


class Status(Enum):
    PASS = "pass"  # noqa: S105 (a status name, not a password)
    WARN = "warn"
    FAIL = "fail"


@dataclass(frozen=True)
class CheckResult:
    name: str
    status: Status
    detail: str


def check_python(version: Sequence[int]) -> CheckResult:
    major_minor = (version[0], version[1])
    shown = ".".join(str(part) for part in version[:3])
    if major_minor in SUPPORTED_PYTHON:
        return CheckResult("python", Status.PASS, f"Python {shown}")
    return CheckResult("python", Status.FAIL, f"Python {shown}; foxcast supports 3.12 and 3.13")


def check_config(path: Path) -> CheckResult:
    if not path.exists():
        return CheckResult("config", Status.WARN, f"no config at {path}; run `foxcast init`")
    try:
        load_settings(path)
    except (ValueError, ValidationError) as exc:
        return CheckResult("config", Status.FAIL, f"{path}: {exc}")
    return CheckResult("config", Status.PASS, str(path))


def check_sec_contact(settings: Settings) -> CheckResult:
    contact = settings.sec_contact
    if not contact:
        return CheckResult(
            "sec contact", Status.FAIL, "set FOXCAST_SEC_CONTACT to 'Your Name you@example.com'"
        )
    if not EMAIL_PATTERN.search(contact):
        return CheckResult(
            "sec contact", Status.FAIL, "FOXCAST_SEC_CONTACT must include an email address"
        )
    return CheckResult("sec contact", Status.PASS, "set")


def check_fred_key(settings: Settings) -> CheckResult:
    if settings.fred_api_key is None:
        return CheckResult(
            "fred key", Status.WARN, "FOXCAST_FRED_API_KEY not set; macro data unavailable"
        )
    return CheckResult("fred key", Status.PASS, "set")


def check_disk_space(path: Path, *, minimum_bytes: int = MIN_FREE_BYTES) -> CheckResult:
    existing = path
    while not existing.exists() and existing != existing.parent:
        existing = existing.parent
    free = shutil.disk_usage(existing).free
    free_gb = free / 1024**3
    if free < minimum_bytes:
        need_gb = minimum_bytes / 1024**3
        return CheckResult(
            "disk space", Status.FAIL, f"{free_gb:.1f} GB free; need {need_gb:.1f} GB"
        )
    return CheckResult("disk space", Status.PASS, f"{free_gb:.1f} GB free at {existing}")


def check_sec_reachable(settings: Settings, client: httpx.Client) -> CheckResult:
    if not settings.sec_contact:
        return CheckResult("sec reachable", Status.FAIL, "skipped: no SEC contact set")
    try:
        response = client.head(
            SEC_PROBE_URL,
            headers={"User-Agent": settings.sec_contact},
            timeout=SEC_TIMEOUT_SECONDS,
        )
    except httpx.HTTPError as exc:
        return CheckResult("sec reachable", Status.FAIL, f"could not reach SEC: {exc}")
    if response.status_code >= 400:
        return CheckResult("sec reachable", Status.FAIL, f"SEC answered {response.status_code}")
    return CheckResult("sec reachable", Status.PASS, f"SEC answered {response.status_code}")


def run_checks(config_path: Path, client: httpx.Client) -> list[CheckResult]:
    config_result = check_config(config_path)
    try:
        settings = load_settings(config_path)
    except (ValueError, ValidationError):
        settings = Settings()
    return [
        check_python(tuple(sys.version_info[:3])),
        config_result,
        check_sec_contact(settings),
        check_fred_key(settings),
        check_disk_space(settings.data_dir),
        check_sec_reachable(settings, client),
    ]
