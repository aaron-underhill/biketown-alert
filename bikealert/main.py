"""
One check: runs every few minutes on a schedule.

1. Exit quietly unless we are inside a pre-shift check window.
2. Count the available e-bikes within a short walk and sort the result into
   a category: none, few, or plenty.
3. Notify with the category at the first check of each window, then again
   only when the category changes.
4. Append the result to a CSV log for later analysis.

Usage:
    python -m bikealert.main --data-dir data
"""

import argparse
import csv
import json
from datetime import datetime, timedelta
from pathlib import Path

from bikealert import bikes, notify
from bikealert.config import TIMEZONE, Config, load_config
from bikealert.shifts import active_window


NONE = "none"
FEW = "few"
PLENTY = "plenty"

CATEGORIES = (NONE, FEW, PLENTY)

LOG_COLUMNS = [
    "checked_at",
    "shift_start",
    "nearest_walk_minutes",
    "nearest_walk_meters",
    "nearest_source",
    "ebikes_within_threshold",
    "status",
    "notified",
]

# Keep remembered categories for this long.
STATE_RETENTION = timedelta(days=2)


def walk_meters(cfg: Config, straight_line_meters: float) -> float:
    return straight_line_meters * cfg.detour_factor


def walk_minutes(cfg: Config, straight_line_meters: float) -> float:
    return walk_meters(cfg, straight_line_meters) / cfg.walk_meters_per_minute


def categorize(nearby: int, plenty_min: int) -> str:
    if nearby == 0:
        return NONE

    if nearby < plenty_min:
        return FEW

    return PLENTY


def decide(previous: str | None, current: str) -> str | None:
    """
    Which notification to send, if any.

    "initial" at the first check of a window, so there is always a heads-up.
    "change" whenever the category moves, in either direction.
    Nothing while the category stays the same.
    """

    if previous not in CATEGORIES:
        return "initial"

    if previous != current:
        return "change"

    return None


def load_state(path: Path) -> dict:
    if not path.exists():
        return {}

    return json.loads(path.read_text(encoding="utf-8"))


def save_state(path: Path, state: dict, now: datetime) -> None:
    cutoff = now - STATE_RETENTION

    kept = {
        key: value
        for key, value in state.items()
        if key == "manual" or datetime.fromisoformat(key) >= cutoff
    }

    path.write_text(json.dumps(kept, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def append_log(path: Path, row: dict) -> None:
    is_new = not path.exists()

    with path.open("a", newline="", encoding="utf-8") as file:
        writer = csv.DictWriter(file, fieldnames=LOG_COLUMNS)

        if is_new:
            writer.writeheader()

        writer.writerow(row)


def run(cfg: Config, data_dir: Path, now: datetime | None = None, fetch=None) -> str:
    now = now or datetime.now(TIMEZONE)
    fetch = fetch or bikes.fetch_available_ebikes

    window = active_window(now, cfg.shifts, cfg.commute_minutes, cfg.lead_minutes)

    if window is None and not cfg.force:
        print("Outside every check window. Nothing to do.")
        return "skipped"

    shift_key = "manual" if window is None else window.shift_start.isoformat()

    available = fetch()
    found = bikes.nearest(available, cfg.home_lat, cfg.home_lon)

    if found is None:
        minutes = None
        meters = None
        source = ""
    else:
        bike, straight = found
        minutes = walk_minutes(cfg, straight)
        meters = walk_meters(cfg, straight)
        source = "street" if bike.source == "street" else "station"

    radius = cfg.threshold_minutes * cfg.walk_meters_per_minute / cfg.detour_factor
    nearby = bikes.count_within(available, cfg.home_lat, cfg.home_lon, radius)

    category = categorize(nearby, cfg.plenty_min)

    state_path = data_dir / "state.json"
    state = load_state(state_path)

    kind = decide(state.get(shift_key), category)

    # A manual test run always reports, so you can confirm it works.
    if cfg.force and window is None:
        kind = "test"

    if kind:
        send_notification(cfg, kind, category, nearby, found, minutes, window)

    state[shift_key] = category
    save_state(state_path, state, now)

    append_log(
        data_dir / "log.csv",
        {
            "checked_at": now.isoformat(timespec="seconds"),
            "shift_start": "" if window is None else window.shift_start.isoformat(),
            "nearest_walk_minutes": "" if minutes is None else f"{minutes:.1f}",
            "nearest_walk_meters": "" if meters is None else f"{meters:.0f}",
            "nearest_source": source,
            "ebikes_within_threshold": nearby,
            "status": category,
            "notified": kind or "",
        },
    )

    nearest_text = "none found" if minutes is None else f"nearest {minutes:.1f} min walk"
    print(f"Category: {category} ({nearby} nearby, {nearest_text}). Notification: {kind or 'none'}.")

    return kind or "checked"


def describe(cfg: Config, category: str, nearby: int, minutes: float | None) -> tuple[str, str]:
    """Notification title and body for a category."""

    walk = f"{cfg.threshold_minutes:g} min walk"
    plural = "e-bike" if nearby == 1 else "e-bikes"

    if category == PLENTY:
        return (
            "Plenty of bikes available",
            f"{nearby} {plural} within a {walk}.",
        )

    if category == FEW:
        return (
            f"Only {nearby} bike{'s' if nearby != 1 else ''} nearby",
            f"{nearby} {plural} within a {walk}. Nearest is about "
            f"{minutes:.0f} min away.",
        )

    if minutes is None:
        detail = "No e-bikes are available anywhere."
    else:
        detail = f"Nearest e-bike is about a {minutes:.0f} min walk."

    return ("No bikes available", f"None within a {walk}. {detail} Consider leaving early.")


def send_notification(cfg, kind, category, nearby, found, minutes, window) -> None:
    title, body = describe(cfg, category, nearby, minutes)

    if kind == "change":
        title = f"Update: {title[0].lower()}{title[1:]}"

    elif kind == "test":
        title = f"Test check: {title[0].lower()}{title[1:]}"

    if window is not None and category != PLENTY:
        body += f" Planned leave time: {window.leave_at:%-I:%M %p}."

    click = ""

    if found is not None:
        bike, _ = found
        click = notify.walking_directions_url(bike.lat, bike.lon)

    # Only "none" is urgent. ntfy's "low" can hide the banner entirely, so
    # the good news stays at normal priority.
    priority = "high" if category == NONE else "default"
    tags = {NONE: "red_circle", FEW: "yellow_circle", PLENTY: "green_circle"}[category]

    notify.send(
        cfg.ntfy_server,
        cfg.ntfy_topic,
        body,
        title=title,
        priority=priority,
        tags=f"bike,{tags}",
        click_url=click,
    )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-dir", default="data", type=Path)
    args = parser.parse_args()

    args.data_dir.mkdir(parents=True, exist_ok=True)

    run(load_config(), args.data_dir)


if __name__ == "__main__":
    main()
