"""
All-day data collection for predicting bike availability.

Every run records one row describing Biketown e-bike availability around
home: how many are within a few walking distances, how far away the nearest
one is, and how many are available across the whole city. Over weeks, this
becomes the training data for a model that predicts whether a bike will be
nearby at a given day and time.

No coordinates are written, only counts and distances relative to home.

Usage:
    python -m bikealert.collect --data-dir data
"""

import argparse
import csv
import os
from datetime import datetime
from pathlib import Path

from bikealert import bikes
from bikealert.config import TIMEZONE


# Walking-time rings around home, in minutes.
RINGS = (3, 5, 10)

WALK_METERS_PER_MINUTE = 80
DETOUR_FACTOR = 1.3

COLUMNS = [
    "collected_at",
    "weekday",
    "hour",
    "minute",
    "nearest_walk_minutes",
    *[f"ebikes_within_{minutes}_min" for minutes in RINGS],
    "nearest_source",
    "city_street_ebikes",
    "city_docked_ebikes",
]


def straight_line_radius(walk_minutes: float) -> float:
    """Straight-line meters that correspond to a walking time."""

    return walk_minutes * WALK_METERS_PER_MINUTE / DETOUR_FACTOR


def build_row(now: datetime, available: list[bikes.Bike], lat: float, lon: float) -> dict:
    found = bikes.nearest(available, lat, lon)

    if found is None:
        nearest_minutes = ""
        nearest_source = ""
    else:
        bike, meters = found
        nearest_minutes = f"{meters * DETOUR_FACTOR / WALK_METERS_PER_MINUTE:.1f}"
        nearest_source = "street" if bike.source == "street" else "station"

    row = {
        "collected_at": now.isoformat(timespec="seconds"),
        "weekday": now.strftime("%a"),
        "hour": now.hour,
        "minute": now.minute,
        "nearest_walk_minutes": nearest_minutes,
        "nearest_source": nearest_source,
        "city_street_ebikes": sum(b.count for b in available if b.source == "street"),
        "city_docked_ebikes": sum(b.count for b in available if b.source != "street"),
    }

    for minutes in RINGS:
        row[f"ebikes_within_{minutes}_min"] = bikes.count_within(
            available, lat, lon, straight_line_radius(minutes)
        )

    return row


def append_row(path: Path, row: dict) -> None:
    is_new = not path.exists()

    with path.open("a", newline="", encoding="utf-8") as file:
        writer = csv.DictWriter(file, fieldnames=COLUMNS)

        if is_new:
            writer.writeheader()

        writer.writerow(row)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-dir", default="data", type=Path)
    args = parser.parse_args()

    lat = float(os.environ["HOME_LAT"])
    lon = float(os.environ["HOME_LON"])

    row = build_row(datetime.now(TIMEZONE), bikes.fetch_available_ebikes(), lat, lon)

    args.data_dir.mkdir(parents=True, exist_ok=True)
    append_row(args.data_dir / "availability.csv", row)

    print(
        f"Logged {row['weekday']} {row['hour']:02d}:{row['minute']:02d}: "
        f"{row['ebikes_within_3_min']} e-bikes within 3 min, "
        f"nearest {row['nearest_walk_minutes'] or 'none'} min."
    )


if __name__ == "__main__":
    main()
