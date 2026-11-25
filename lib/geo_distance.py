"""Geographic distance utilities — haversine + lightweight geocoding.

Pure stdlib. No external dependencies. All geocoding goes through the
OpenStreetMap Nominatim public endpoint by default, respecting their
usage policy (max 1 req/s, descriptive User-Agent). Results are cached
in ~/.claude/cache/geocode.json to minimise repeat lookups.

Used by the family-distance skill and /where-is command to compute
distances between arbitrary addresses or lat/lon pairs.
"""

import json
import math
import os
import re
import time
import urllib.parse
from dataclasses import dataclass
from pathlib import Path
from typing import Optional, Tuple

from lib.hal.http import get

__all__ = [
    "Coord",
    "haversine_km",
    "geocode",
    "parse_lat_lon",
    "GeocodeError",
]

EARTH_RADIUS_KM = 6371.0088
CACHE_DIR = Path.home() / ".claude" / "cache"
CACHE_FILE = CACHE_DIR / "geocode.json"
USER_AGENT = "grip-family-distance/1.0 (personal use)"
NOMINATIM_URL = "https://nominatim.openstreetmap.org/search"
NOMINATIM_RATE_LIMIT_S = 1.1


class GeocodeError(RuntimeError):
    """Raised when an address cannot be geocoded."""


@dataclass(frozen=True)
class Coord:
    """A latitude/longitude point on Earth (WGS84, decimal degrees)."""

    lat: float
    lon: float

    def __post_init__(self) -> None:
        if not (-90.0 <= self.lat <= 90.0):
            raise ValueError(f"lat out of range: {self.lat}")
        if not (-180.0 <= self.lon <= 180.0):
            raise ValueError(f"lon out of range: {self.lon}")

    def __str__(self) -> str:
        return f"{self.lat:.6f},{self.lon:.6f}"


def haversine_km(a: Coord, b: Coord) -> float:
    """Great-circle distance between two points, in kilometres.

    This is a straight-line distance, not a driving distance. For driving
    estimates use the OSRM or Google Maps APIs. For most "how far away is
    X from Y" queries the haversine is within ~30% of driving distance in
    urban areas, less accurate in mountainous or coastal terrain.
    """
    lat1 = math.radians(a.lat)
    lat2 = math.radians(b.lat)
    dlat = math.radians(b.lat - a.lat)
    dlon = math.radians(b.lon - a.lon)

    h = (
        math.sin(dlat / 2) ** 2
        + math.cos(lat1) * math.cos(lat2) * math.sin(dlon / 2) ** 2
    )
    c = 2 * math.asin(math.sqrt(h))
    return EARTH_RADIUS_KM * c


_LAT_LON_RE = re.compile(
    r"^\s*(-?\d+(?:\.\d+)?)\s*[,\s]\s*(-?\d+(?:\.\d+)?)\s*$"
)


def parse_lat_lon(text: str) -> Optional[Coord]:
    """Parse '-33.928, 18.417' or '-33.928 18.417' into a Coord.

    Returns None if the text does not look like a coordinate pair.
    Accepts comma, space, or tab separators.
    """
    m = _LAT_LON_RE.match(text)
    if not m:
        return None
    try:
        return Coord(lat=float(m.group(1)), lon=float(m.group(2)))
    except ValueError:
        return None


def _load_cache() -> dict:
    try:
        return json.loads(CACHE_FILE.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def _save_cache(cache: dict) -> None:
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    tmp = CACHE_FILE.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(cache, indent=2), encoding="utf-8")
    os.replace(tmp, CACHE_FILE)


_last_request_at = 0.0


def _nominatim_query(address: str, country_hint: Optional[str] = None) -> dict:
    """Query Nominatim respecting their rate limit. Returns first result."""
    global _last_request_at
    now = time.monotonic()
    wait = NOMINATIM_RATE_LIMIT_S - (now - _last_request_at)
    if wait > 0:
        time.sleep(wait)
    _last_request_at = time.monotonic()

    params = {
        "q": address,
        "format": "json",
        "limit": 1,
        "addressdetails": 1,
    }
    if country_hint:
        params["countrycodes"] = country_hint

    url = NOMINATIM_URL + "?" + urllib.parse.urlencode(params)
    resp = get(url, headers={"User-Agent": USER_AGENT}, timeout=15)
    results = resp.json()
    if not results:
        raise GeocodeError(f"no geocoding result for: {address!r}")
    return results[0]


def geocode(
    address: str,
    *,
    country_hint: Optional[str] = None,
    use_cache: bool = True,
) -> Coord:
    """Resolve a free-text address to a Coord.

    Args:
        address: the address string.
        country_hint: ISO 3166-1 alpha-2 country code to bias results
            (e.g. "za" for South Africa).
        use_cache: read/write the local geocode cache.

    Raises GeocodeError if no result is found.
    """
    cache_key = f"{address}|{country_hint or ''}"
    cache = _load_cache() if use_cache else {}

    if use_cache and cache_key in cache:
        hit = cache[cache_key]
        return Coord(lat=float(hit["lat"]), lon=float(hit["lon"]))

    result = _nominatim_query(address, country_hint=country_hint)
    coord = Coord(lat=float(result["lat"]), lon=float(result["lon"]))

    if use_cache:
        cache[cache_key] = {
            "lat": coord.lat,
            "lon": coord.lon,
            "display_name": result.get("display_name", ""),
            "cached_at": int(time.time()),
        }
        _save_cache(cache)

    return coord


def resolve_endpoint(text: str, country_hint: Optional[str] = None) -> Tuple[Coord, str]:
    """Resolve text to a Coord, auto-detecting coord-pair vs address.

    Returns (coord, label) where label is either "coords" or the geocoded
    display name. Callers can surface this label to the user for
    verification.
    """
    parsed = parse_lat_lon(text)
    if parsed is not None:
        return parsed, "coords"
    coord = geocode(text, country_hint=country_hint)
    cache = _load_cache()
    label = cache.get(f"{text}|{country_hint or ''}", {}).get("display_name", text)
    return coord, label
