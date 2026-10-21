"""
Reads Biketown's public GBFS feeds and finds the nearest available e-bike.

GBFS (General Bikeshare Feed Specification) is the open standard bikeshare
systems use to publish live availability. Biketown splits its bikes across
two feeds:

- free_bike_status: bikes locked anywhere on the street, each with a location
- station_status:   counts of bikes docked at each station, whose locations
                    come from station_information
"""

import json
import math
import urllib.request
from dataclasses import dataclass

from bikealert import config


EARTH_RADIUS_METERS = 6_371_000


@dataclass(frozen=True)
class Bike:
    lat: float
    lon: float

    # "street" for a free-floating bike, or the station's name.
    source: str


def fetch_json(url: str) -> dict:
    request = urllib.request.Request(
        url,
        headers={"User-Agent": "biketown-alert (personal commute notifier)"},
    )

    with urllib.request.urlopen(request, timeout=20) as response:
        return json.load(response)


def distance_meters(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Straight-line distance between two points on Earth (haversine formula)."""

    phi1 = math.radians(lat1)
    phi2 = math.radians(lat2)
    d_phi = math.radians(lat2 - lat1)
    d_lambda = math.radians(lon2 - lon1)

    a = (
        math.sin(d_phi / 2) ** 2
        + math.cos(phi1) * math.cos(phi2) * math.sin(d_lambda / 2) ** 2
    )

    return 2 * EARTH_RADIUS_METERS * math.asin(math.sqrt(a))


def street_ebikes(free_bike_status: dict) -> list[Bike]:
    """Free-floating e-bikes that are not reserved or out of service."""

    bikes = []

    for bike in free_bike_status["data"]["bikes"]:
        if bike.get("is_reserved") or bike.get("is_disabled"):
            continue

        # The feed also lists scooters. Older entries may have no type,
        # which Biketown uses for bikes.
        if bike.get("type", "electric_bike") != "electric_bike":
            continue

        bikes.append(Bike(lat=bike["lat"], lon=bike["lon"], source="street"))

    return bikes


def docked_ebikes(station_information: dict, station_status: dict) -> list[Bike]:
    """One entry per station that has at least one rentable e-bike."""

    locations = {
        station["station_id"]: station
        for station in station_information["data"]["stations"]
    }

    bikes = []

    for status in station_status["data"]["stations"]:
        if not status.get("is_renting") or not status.get("is_installed"):
            continue

        if status.get("num_ebikes_available", 0) <= 0:
            continue

        station = locations.get(status["station_id"])

        if station is None:
            continue

        bikes.append(
            Bike(lat=station["lat"], lon=station["lon"], source=station["name"])
        )

    return bikes


def nearest(bikes: list[Bike], lat: float, lon: float) -> tuple[Bike, float] | None:
    """The closest bike and its straight-line distance, or None if no bikes."""

    best = None

    for bike in bikes:
        meters = distance_meters(lat, lon, bike.lat, bike.lon)

        if best is None or meters < best[1]:
            best = (bike, meters)

    return best


def count_within(bikes: list[Bike], lat: float, lon: float, meters: float) -> int:
    return sum(
        1 for bike in bikes if distance_meters(lat, lon, bike.lat, bike.lon) <= meters
    )


def fetch_available_ebikes() -> list[Bike]:
    free = fetch_json(config.FREE_BIKE_STATUS_URL)
    station_info = fetch_json(config.STATION_INFORMATION_URL)
    station_status = fetch_json(config.STATION_STATUS_URL)

    return street_ebikes(free) + docked_ebikes(station_info, station_status)
