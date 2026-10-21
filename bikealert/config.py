"""
Settings, read from environment variables.

Anything personal (home location, notification topic, work shifts) comes
from GitHub Actions secrets, so none of it is stored in the code.
"""

import os
from dataclasses import dataclass
from zoneinfo import ZoneInfo


TIMEZONE = ZoneInfo("America/Los_Angeles")

GBFS_BASE = "https://gbfs.lyft.com/gbfs/1.1/pdx/en"
FREE_BIKE_STATUS_URL = f"{GBFS_BASE}/free_bike_status.json"
STATION_INFORMATION_URL = f"{GBFS_BASE}/station_information.json"
STATION_STATUS_URL = f"{GBFS_BASE}/station_status.json"


@dataclass(frozen=True)
class Config:
    home_lat: float
    home_lon: float

    # Bikes within this many minutes' walk count as "nearby".
    threshold_minutes: float

    # Nearby e-bikes needed for "plenty". Fewer (but at least one) is "few".
    plenty_min: int

    # How long before a shift starts you leave home.
    commute_minutes: int

    # How long before leaving the checks begin.
    lead_minutes: int

    # Average walking pace in meters per minute (about 3 mph).
    walk_meters_per_minute: float

    # Streets are not straight lines, so walking distance is longer than
    # distance as the crow flies. 1.3 is a common estimate for city grids.
    detour_factor: float

    ntfy_topic: str
    ntfy_server: str

    shifts: str

    # Check right now regardless of the shift schedule (for testing).
    force: bool


def _required(name: str) -> str:
    value = os.getenv(name, "").strip()

    if not value:
        raise SystemExit(f"Missing required setting: {name}")

    return value


def _optional(name: str, default: str) -> str:
    """GitHub passes unset variables as empty strings, so treat those as unset."""

    return os.getenv(name, "").strip() or default


def load_config() -> Config:
    return Config(
        home_lat=float(_required("HOME_LAT")),
        home_lon=float(_required("HOME_LON")),
        threshold_minutes=float(_optional("THRESHOLD_MINUTES", "3")),
        plenty_min=int(_optional("PLENTY_MIN", "4")),
        commute_minutes=int(_optional("COMMUTE_MINUTES", "10")),
        lead_minutes=int(_optional("LEAD_MINUTES", "50")),
        walk_meters_per_minute=float(_optional("WALK_METERS_PER_MINUTE", "80")),
        detour_factor=float(_optional("DETOUR_FACTOR", "1.3")),
        ntfy_topic=_required("NTFY_TOPIC"),
        ntfy_server=_optional("NTFY_SERVER", "https://ntfy.sh").rstrip("/"),
        shifts=_optional("SHIFTS", ""),
        force=_optional("FORCE_CHECK", "").lower() in ("1", "true", "yes"),
    )
