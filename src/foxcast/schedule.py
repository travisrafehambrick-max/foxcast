"""`foxcast schedule`: set up the daily job with the system's own scheduler (change V).

By default this only prints the command. Registering it changes the machine, so it runs
only with ``--apply``.
"""

import shlex
import sys
from pathlib import Path
from typing import Final

TASK_NAME: Final = "foxcast daily snapshot"
WEEKDAYS: Final = "MON,TUE,WED,THU,FRI"


def daily_job_command(project: Path) -> list[str]:
    """What the scheduler runs: the snapshot, then the numbers-only forecasts."""
    return ["uv", "run", "--project", str(project), "foxcast", "daily"]


def windows_schtasks(project: Path, start_time: str) -> list[str]:
    job = " ".join(f'"{part}"' if " " in part else part for part in daily_job_command(project))
    return [
        "schtasks",
        "/Create",
        "/TN",
        TASK_NAME,
        "/TR",
        job,
        "/SC",
        "WEEKLY",
        "/D",
        WEEKDAYS,
        "/ST",
        start_time,
        "/F",
    ]


def cron_line(project: Path, start_time: str) -> str:
    hour, minute = start_time.split(":")
    job = shlex.join(daily_job_command(project))
    return f"{int(minute)} {int(hour)} * * 1-5 {job}"


def scheduler_command(project: Path, start_time: str, platform: str = sys.platform) -> list[str]:
    if platform.startswith("win"):
        return windows_schtasks(project, start_time)
    # macOS and Linux: add a crontab line (launchd plist support can follow).
    return [
        "sh",
        "-c",
        f'(crontab -l 2>/dev/null; echo "{cron_line(project, start_time)}") | crontab -',
    ]
