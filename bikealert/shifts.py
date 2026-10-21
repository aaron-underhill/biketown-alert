"""
Work shift schedule and the check window before each shift.

Shifts are written one per line (commas or semicolons also work), in either
of two forms:

    Mon 07:00           every Monday at 7:00 AM
    2026-10-24 15:00    one time only

Times are 24-hour Portland time. For each shift:

    leave time    = shift start - commute minutes
    window start  = leave time  - lead minutes

Checks only run between window start and leave time.
"""

import re
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta

from bikealert.config import TIMEZONE


WEEKDAYS = ["mon", "tue", "wed", "thu", "fri", "sat", "sun"]

WEEKLY_PATTERN = re.compile(r"^([a-z]{3})[a-z]*\s+(\d{1,2}):(\d{2})$")
ONE_TIME_PATTERN = re.compile(r"^(\d{4})-(\d{2})-(\d{2})\s+(\d{1,2}):(\d{2})$")


@dataclass(frozen=True)
class Window:
    shift_start: datetime
    leave_at: datetime
    opens_at: datetime


def parse_shifts(text: str) -> tuple[list[tuple[int, time]], list[datetime]]:
    """Split the schedule into weekly (weekday, time) pairs and one-time datetimes."""

    weekly = []
    one_time = []

    for raw in re.split(r"[\n,;]+", text):
        entry = raw.strip().lower()

        if not entry or entry.startswith("#"):
            continue

        match = WEEKLY_PATTERN.match(entry)

        if match and match.group(1) in WEEKDAYS:
            weekday = WEEKDAYS.index(match.group(1))
            weekly.append((weekday, time(int(match.group(2)), int(match.group(3)))))
            continue

        match = ONE_TIME_PATTERN.match(entry)

        if match:
            year, month, day, hour, minute = (int(part) for part in match.groups())
            one_time.append(datetime(year, month, day, hour, minute, tzinfo=TIMEZONE))
            continue

        raise ValueError(
            f"Could not read shift {raw.strip()!r}. "
            "Use 'Mon 07:00' or '2026-10-24 15:00'."
        )

    return weekly, one_time


def shift_starts_near(now: datetime, text: str) -> list[datetime]:
    """Every shift start from yesterday through tomorrow, so windows that
    cross midnight are still found."""

    weekly, one_time = parse_shifts(text)

    starts = list(one_time)
    today = now.date()

    for offset in (-1, 0, 1):
        day: date = today + timedelta(days=offset)

        for weekday, start_time in weekly:
            if day.weekday() == weekday:
                starts.append(datetime.combine(day, start_time, tzinfo=TIMEZONE))

    return starts


def active_window(
    now: datetime, text: str, commute_minutes: int, lead_minutes: int
) -> Window | None:
    """The check window that `now` falls inside, if any."""

    active = []

    for start in shift_starts_near(now, text):
        leave_at = start - timedelta(minutes=commute_minutes)
        opens_at = leave_at - timedelta(minutes=lead_minutes)

        if opens_at <= now <= leave_at:
            active.append(Window(start, leave_at, opens_at))

    if not active:
        return None

    # If two windows overlap, care about the shift you leave for first.
    return min(active, key=lambda window: window.leave_at)
