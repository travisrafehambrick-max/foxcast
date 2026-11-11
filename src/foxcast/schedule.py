"""`foxcast schedule`: set up the daily job with the system's own scheduler (change V).

By default this only describes what it would do. Registering the job changes the machine,
so it happens only with ``--apply``. The time is New York time, because the snapshot only
runs while NYSE is open; it is converted to this machine's local time for the scheduler.
"""

import re
import shlex
import shutil
import subprocess
import sys
from datetime import date, datetime, time
from pathlib import Path
from typing import Final
from zoneinfo import ZoneInfo

TASK_NAME: Final = "foxcast daily snapshot"
WEEKDAYS: Final = "MON,TUE,WED,THU,FRI"
TIME_PATTERN: Final = re.compile(r"^([01]\d|2[0-3]):([0-5]\d)$")
NEW_YORK: Final = ZoneInfo("America/New_York")
CRON_TAG: Final = "# foxcast daily"


def parse_time(text: str) -> time:
    match = TIME_PATTERN.fullmatch(text)
    if not match:
        raise ValueError(f"time must be HH:MM (24-hour), got {text!r}")
    return time(int(match[1]), int(match[2]))


def new_york_to_local(ny_time: time, on: date | None = None) -> time:
    """Convert a New York wall-clock time to this machine's local wall-clock time."""
    day = on or date.today()
    moment = datetime.combine(day, ny_time, tzinfo=NEW_YORK)
    return moment.astimezone().time().replace(second=0, microsecond=0)


def daily_job_command(project: Path) -> list[str]:
    """What the scheduler runs: the snapshot, then the numbers-only forecasts."""
    uv = shutil.which("uv") or "uv"
    return [uv, "run", "--project", str(project), "foxcast", "daily"]


def windows_schtasks(project: Path, local_time: time) -> list[str]:
    job = subprocess.list2cmdline(daily_job_command(project))
    return [
        "schtasks", "/Create", "/TN", TASK_NAME, "/TR", job,
        "/SC", "WEEKLY", "/D", WEEKDAYS, "/ST", local_time.strftime("%H:%M"), "/F",
    ]  # fmt: skip


def cron_line(project: Path, local_time: time) -> str:
    job = shlex.join(daily_job_command(project))
    return f"{local_time.minute} {local_time.hour} * * 1-5 {job} {CRON_TAG}"


def describe(project: Path, ny_time: str, platform: str = sys.platform) -> str:
    local = new_york_to_local(parse_time(ny_time))
    if platform.startswith("win"):
        return subprocess.list2cmdline(windows_schtasks(project, local))
    return f"add to crontab: {cron_line(project, local)}"


def _current_crontab() -> str:
    listing = subprocess.run(["crontab", "-l"], capture_output=True, text=True, check=False)  # noqa: S607
    if listing.returncode == 0:
        return listing.stdout
    if "no crontab" in listing.stderr.lower():
        return ""
    # Any other failure: stop rather than risk replacing the user's crontab with one line.
    raise RuntimeError(f"could not read the current crontab: {listing.stderr.strip()}")


def apply(project: Path, ny_time: str, platform: str = sys.platform) -> str:
    local = new_york_to_local(parse_time(ny_time))
    if platform.startswith("win"):
        subprocess.run(windows_schtasks(project, local), check=True)  # noqa: S603
        return f"registered '{TASK_NAME}' (/F replaces any earlier copy)"
    existing = _current_crontab()
    kept = [line for line in existing.splitlines() if not line.endswith(CRON_TAG)]
    updated = "\n".join([*kept, cron_line(project, local)]) + "\n"
    subprocess.run(["crontab", "-"], input=updated, text=True, check=True)  # noqa: S607
    return "crontab updated (any earlier foxcast line replaced)"
