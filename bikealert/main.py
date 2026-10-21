"""
One check: runs every few minutes on a schedule.

1. Exit quietly unless we are inside a pre-shift check window.
2. Find the nearest available e-bike and estimate the walk to it.
3. Notify when bikes become scarce, and again when they come back.
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

# Keep remembered alert states for this long.
STATE_RETENTION = timedelta(days=2)


def walk_meters(cfg: Config, straight_line_meters: float) -> float:
    return straight_line_meters * cfg.detour_factor


def walk_minutes(cfg: Config, straight_line_meters: float) -> float:
    return walk_meters(cfg, straight_line_meters) / cfg.walk_meters_per_minute


def decide(previous_status: str | None, short: bool) -> str | None:
    """
    Which notification to send, if any.

    Alerting on every check would buzz your phone every five minutes, so
    only changes are announced: once when bikes become scarce, and once if
    they come back before you leave.
    """

    if short and previous_status != "short":
        return "short"

    if not short and previous_status == "short":
        return "recovered"

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
        short = True
    else:
        bike, straight = found
        minutes = walk_minutes(cfg, straight)
        meters = walk_meters(cfg, straight)
        source = "street" if bike.source == "street" else "station"
        short = minutes > cfg.threshold_minutes

    threshold_straight_line = (
        cfg.threshold_minutes * cfg.walk_meters_per_minute / cfg.detour_factor
    )
    nearby = bikes.count_within(
        available, cfg.home_lat, cfg.home_lon, threshold_straight_line
    )

    state_path = data_dir / "state.json"
    state = load_state(state_path)

    kind = decide(state.get(shift_key), short)

    # A manual test run always sends something, so you can confirm it works.
    if cfg.force and window is None:
        kind = "test"

    if kind:
        send_notification(cfg, kind, found, minutes, nearby, window)

    state[shift_key] = "short" if short else "ok"
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
            "status": "short" if short else "ok",
            "notified": kind or "",
        },
    )

    summary = "no e-bikes found" if minutes is None else f"nearest {minutes:.1f} min walk"
    print(f"Status: {'short' if short else 'ok'} ({summary}). Notification: {kind or 'none'}.")

    return kind or "checked"


def send_notification(cfg, kind, found, minutes, nearby, window) -> None:
    click = ""

    if found is not None:
        bike, _ = found
        click = notify.walking_directions_url(bike.lat, bike.lon)

    leave_note = ""

    if window is not None:
        leave_note = f" You planned to leave at {window.leave_at:%-I:%M %p}."

    if minutes is None:
        distance_text = "No e-bikes are available anywhere nearby."
    else:
        distance_text = f"Nearest e-bike is about a {minutes:.0f} min walk away."

    if kind == "short":
        notify.send(
            cfg.ntfy_server,
            cfg.ntfy_topic,
            f"{distance_text}{leave_note} Consider heading out early.",
            title="Biketown: bikes are far today",
            priority="high",
            tags="bike,warning",
            click_url=click,
        )

    elif kind == "recovered":
        notify.send(
            cfg.ntfy_server,
            cfg.ntfy_topic,
            f"{distance_text} {nearby} within your usual range.",
            title="Biketown: bikes are back",
            tags="bike,white_check_mark",
            click_url=click,
        )

    elif kind == "test":
        notify.send(
            cfg.ntfy_server,
            cfg.ntfy_topic,
            f"{distance_text} {nearby} e-bike(s) within {cfg.threshold_minutes:g} min.",
            title="Biketown alert: test check",
            tags="bike",
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
