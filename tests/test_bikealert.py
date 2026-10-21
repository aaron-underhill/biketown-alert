"""
Tests use small hand-made feeds shaped like Biketown's real GBFS data, with
Pioneer Courthouse Square standing in for "home".

Run with:
    python -m unittest discover tests
"""

import csv
import json
import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from unittest import mock

from bikealert import bikes, main, shifts
from bikealert.config import TIMEZONE, Config


HOME = (45.51897, -122.67930)

# Roughly 1 meter of latitude.
METER = 1 / 111_320


def at(lat_meters_north: float) -> tuple[float, float]:
    """A point straight north of home by the given number of meters."""
    return HOME[0] + lat_meters_north * METER, HOME[1]


def free_feed(*entries: dict) -> dict:
    return {"data": {"bikes": list(entries)}}


def free_bike(meters: float, **fields) -> dict:
    lat, lon = at(meters)
    bike = {
        "bike_id": "x",
        "lat": lat,
        "lon": lon,
        "is_reserved": 0,
        "is_disabled": 0,
        "type": "electric_bike",
    }
    bike.update(fields)
    return bike


def make_config(**overrides) -> Config:
    values = dict(
        home_lat=HOME[0],
        home_lon=HOME[1],
        threshold_minutes=3,
        commute_minutes=20,
        lead_minutes=45,
        walk_meters_per_minute=80,
        detour_factor=1.3,
        ntfy_topic="test-topic",
        ntfy_server="https://ntfy.example",
        shifts="Mon 15:00",
        force=False,
    )
    values.update(overrides)
    return Config(**values)


def pdx(year, month, day, hour, minute) -> datetime:
    return datetime(year, month, day, hour, minute, tzinfo=TIMEZONE)


class DistanceTests(unittest.TestCase):
    def test_one_kilometer_north(self):
        lat, lon = at(1000)
        self.assertAlmostEqual(
            bikes.distance_meters(*HOME, lat, lon), 1000, delta=5
        )

    def test_same_point_is_zero(self):
        self.assertEqual(bikes.distance_meters(*HOME, *HOME), 0)


class FilteringTests(unittest.TestCase):
    def test_skips_reserved_disabled_and_scooters(self):
        feed = free_feed(
            free_bike(50, is_reserved=1),
            free_bike(60, is_disabled=1),
            free_bike(70, type="electric_scooter"),
            free_bike(500),
        )

        available = bikes.street_ebikes(feed)

        self.assertEqual(len(available), 1)
        found, meters = bikes.nearest(available, *HOME)
        self.assertAlmostEqual(meters, 500, delta=3)

    def test_docked_ebikes_use_station_location(self):
        lat, lon = at(120)
        info = {"data": {"stations": [
            {"station_id": "a", "name": "Near", "lat": lat, "lon": lon},
            {"station_id": "b", "name": "Closed", "lat": HOME[0], "lon": HOME[1]},
            {"station_id": "c", "name": "Empty", "lat": HOME[0], "lon": HOME[1]},
        ]}}
        status = {"data": {"stations": [
            {"station_id": "a", "num_ebikes_available": 2, "is_renting": 1, "is_installed": 1},
            {"station_id": "b", "num_ebikes_available": 4, "is_renting": 0, "is_installed": 1},
            {"station_id": "c", "num_ebikes_available": 0, "is_renting": 1, "is_installed": 1},
        ]}}

        docked = bikes.docked_ebikes(info, status)

        self.assertEqual([bike.source for bike in docked], ["Near"])

    def test_no_bikes_returns_none(self):
        self.assertIsNone(bikes.nearest([], *HOME))


class ShiftWindowTests(unittest.TestCase):
    # 2026-10-26 is a Monday.

    def test_inside_window(self):
        # Shift 15:00, leave 14:40, checks open 13:55.
        window = shifts.active_window(pdx(2026, 10, 26, 14, 10), "Mon 15:00", 20, 45)
        self.assertIsNotNone(window)
        self.assertEqual(window.leave_at, pdx(2026, 10, 26, 14, 40))

    def test_before_and_after_window(self):
        self.assertIsNone(shifts.active_window(pdx(2026, 10, 26, 13, 50), "Mon 15:00", 20, 45))
        self.assertIsNone(shifts.active_window(pdx(2026, 10, 26, 14, 45), "Mon 15:00", 20, 45))

    def test_wrong_weekday(self):
        self.assertIsNone(shifts.active_window(pdx(2026, 10, 27, 14, 10), "Mon 15:00", 20, 45))

    def test_one_time_shift(self):
        window = shifts.active_window(pdx(2026, 10, 24, 6, 30), "2026-10-24 07:00", 20, 45)
        self.assertIsNotNone(window)

    def test_window_crossing_midnight(self):
        # Tuesday 00:30 shift: leave Tuesday 00:10, checks open Monday 23:25.
        window = shifts.active_window(pdx(2026, 10, 26, 23, 40), "Tue 00:30", 20, 45)
        self.assertIsNotNone(window)
        self.assertEqual(window.shift_start, pdx(2026, 10, 27, 0, 30))

    def test_accepts_full_day_names_and_separators(self):
        weekly, one_time = shifts.parse_shifts("Monday 7:00; tue 15:00\n# note\n2026-11-01 23:00")
        self.assertEqual(len(weekly), 2)
        self.assertEqual(len(one_time), 1)

    def test_rejects_unreadable_shift(self):
        with self.assertRaises(ValueError):
            shifts.parse_shifts("sometime tomorrow")


class DecideTests(unittest.TestCase):
    def test_alerts_once_then_stays_quiet(self):
        self.assertEqual(main.decide(None, short=True), "short")
        self.assertIsNone(main.decide("short", short=True))

    def test_announces_recovery(self):
        self.assertEqual(main.decide("short", short=False), "recovered")

    def test_quiet_when_bikes_are_fine(self):
        self.assertIsNone(main.decide(None, short=False))
        self.assertIsNone(main.decide("ok", short=False))


class RunTests(unittest.TestCase):
    """The whole check, with the network and notifications replaced."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.data_dir = Path(self.tmp.name)
        self.sent = []
        patcher = mock.patch.object(
            main.notify, "send", side_effect=lambda *a, **k: self.sent.append(k["title"])
        )
        patcher.start()
        self.addCleanup(patcher.stop)
        self.addCleanup(self.tmp.cleanup)

    def check(self, bike_meters, now, **config):
        def fetch():
            return [
                bikes.Bike(*at(meters), source="street") for meters in bike_meters
            ]

        return main.run(make_config(**config), self.data_dir, now=now, fetch=fetch)

    def test_outside_window_does_nothing(self):
        result = self.check([1000], pdx(2026, 10, 26, 9, 0))
        self.assertEqual(result, "skipped")
        self.assertFalse((self.data_dir / "log.csv").exists())

    def test_far_bike_alerts_once_then_recovers(self):
        # 3 min threshold = 240 m walking = about 185 m straight line.
        self.check([400], pdx(2026, 10, 26, 14, 0))
        self.check([400], pdx(2026, 10, 26, 14, 5))
        self.check([100], pdx(2026, 10, 26, 14, 10))

        self.assertEqual(
            self.sent,
            ["Biketown: bikes are far today", "Biketown: bikes are back"],
        )

        with (self.data_dir / "log.csv").open() as file:
            rows = list(csv.DictReader(file))

        self.assertEqual([row["status"] for row in rows], ["short", "short", "ok"])
        self.assertEqual(rows[2]["ebikes_within_threshold"], "1")

    def test_close_bike_sends_nothing(self):
        self.check([100], pdx(2026, 10, 26, 14, 0))
        self.assertEqual(self.sent, [])

    def test_no_bikes_counts_as_short(self):
        self.check([], pdx(2026, 10, 26, 14, 0))
        self.assertEqual(self.sent, ["Biketown: bikes are far today"])

    def test_forced_check_outside_window_sends_test(self):
        self.check([100], pdx(2026, 10, 26, 9, 0), force=True)
        self.assertEqual(self.sent, ["Biketown alert: test check"])

    def test_new_shift_alerts_again(self):
        self.check([400], pdx(2026, 10, 26, 14, 0))
        self.check([400], pdx(2026, 11, 2, 14, 0))
        self.assertEqual(len(self.sent), 2)

    def test_log_has_no_coordinates(self):
        self.check([400], pdx(2026, 10, 26, 14, 0))
        text = (self.data_dir / "log.csv").read_text()
        self.assertNotIn(f"{HOME[0]:.3f}", text)
        state = json.loads((self.data_dir / "state.json").read_text())
        self.assertEqual(list(state.values()), ["short"])


if __name__ == "__main__":
    unittest.main()
