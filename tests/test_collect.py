import csv
import tempfile
import unittest
from datetime import datetime
from pathlib import Path

from bikealert import bikes, collect
from bikealert.config import TIMEZONE


HOME = (45.51897, -122.67930)
METER = 1 / 111_320


def bike_at(meters_north, source="street", count=1):
    return bikes.Bike(HOME[0] + meters_north * METER, HOME[1], source, count)


class CollectTests(unittest.TestCase):
    def setUp(self):
        self.now = datetime(2026, 10, 22, 14, 15, tzinfo=TIMEZONE)  # a Thursday

    def test_rings_count_every_bike(self):
        # 3 min ~ 185 m, 5 min ~ 308 m, 10 min ~ 615 m straight line.
        available = [
            bike_at(100),
            bike_at(150, source="Station A", count=3),
            bike_at(250),
            bike_at(500),
            bike_at(2000),
        ]

        row = collect.build_row(self.now, available, *HOME)

        self.assertEqual(row["ebikes_within_3_min"], 4)
        self.assertEqual(row["ebikes_within_5_min"], 5)
        self.assertEqual(row["ebikes_within_10_min"], 6)
        self.assertEqual(row["city_street_ebikes"], 4)
        self.assertEqual(row["city_docked_ebikes"], 3)
        self.assertEqual(row["nearest_source"], "street")
        self.assertEqual((row["weekday"], row["hour"], row["minute"]), ("Thu", 14, 15))

    def test_no_bikes(self):
        row = collect.build_row(self.now, [], *HOME)
        self.assertEqual(row["nearest_walk_minutes"], "")
        self.assertEqual(row["ebikes_within_10_min"], 0)

    def test_csv_has_header_once_and_no_coordinates(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "availability.csv"
            for _ in range(2):
                collect.append_row(path, collect.build_row(self.now, [bike_at(100)], *HOME))

            text = path.read_text()
            rows = list(csv.DictReader(text.splitlines()))

            self.assertEqual(len(rows), 2)
            self.assertEqual(list(rows[0].keys()), collect.COLUMNS)
            self.assertNotIn("45.5", text)


if __name__ == "__main__":
    unittest.main()
