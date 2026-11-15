"""Commute times via postcodes.io (geocoding) and the TfL Journey Planner.

"Door-to-door" minutes are measured from the time you *want* to leave, so waiting for
an infrequent train counts. Querying several departure times across a window and taking
the median and worst case exposes routes that look fast but run twice an hour.
"""
from __future__ import annotations

import os
import re
import statistics
import time
from dataclasses import dataclass
from datetime import date, datetime, timedelta

import requests

POSTCODES_URL = "https://api.postcodes.io/postcodes/{}"
TFL_URL = "https://api.tfl.gov.uk/Journey/JourneyResults/{}/to/{}"
TFL_MODES = "national-rail,overground,elizabeth-line,tube,dlr,tram,walking"
LATLON = re.compile(r"^\s*(-?\d+(?:\.\d+)?)\s*,\s*(-?\d+(?:\.\d+)?)\s*$")


class CommuteError(RuntimeError):
    pass


def make_session(cache_path: str = ".cache/http", days: int = 7) -> requests.Session:
    """A requests session that caches responses on disk (falls back to a plain session)."""
    try:
        import requests_cache
        os.makedirs(os.path.dirname(cache_path) or ".", exist_ok=True)
        return requests_cache.CachedSession(cache_path, backend="sqlite",
                                            expire_after=timedelta(days=days),
                                            allowable_codes=(200,))
    except ImportError:
        return requests.Session()


def tfl_key() -> str | None:
    return os.environ.get("TFL_APP_KEY") or None


# ---------------------------------------------------------------- geocoding
def geocode(query: str, session: requests.Session) -> tuple[float, float, str]:
    """'lat,lon' or a full UK postcode -> (lat, lon, label)."""
    m = LATLON.match(query)
    if m:
        return float(m[1]), float(m[2]), query.strip()
    pc = re.sub(r"\s+", "", query).upper()
    r = session.get(POSTCODES_URL.format(pc), timeout=10)
    if r.status_code == 404:
        raise CommuteError(f"Postcode not found: {query!r} (use a full postcode or 'lat,lon')")
    r.raise_for_status()
    res = r.json()["result"]
    return res["latitude"], res["longitude"], res["postcode"]


# ---------------------------------------------------------------- journeys
@dataclass
class Journey:
    depart_wanted: datetime
    start: datetime
    arrive: datetime
    duration: int            # minutes on the move (TfL's figure)
    door_to_door: int        # minutes from depart_wanted to arrival, including waiting
    changes: int
    walk_minutes: int
    route: str               # e.g. "Walk 6 → Thameslink → Walk 4"


def _leg_label(leg: dict) -> str:
    mode = leg["mode"]["id"]
    if mode == "walking":
        return f"Walk {leg['duration']}"
    opts = leg.get("routeOptions") or []
    name = opts[0].get("name") if opts else ""
    return name or leg["mode"].get("name", mode)


def parse_journeys(payload: dict, depart_wanted: datetime) -> list[Journey]:
    out = []
    for j in payload.get("journeys", []):
        legs = j.get("legs", [])
        transit = [l for l in legs if l["mode"]["id"] != "walking"]
        start = datetime.fromisoformat(j["startDateTime"])
        arrive = datetime.fromisoformat(j["arrivalDateTime"])
        out.append(Journey(
            depart_wanted=depart_wanted, start=start, arrive=arrive,
            duration=int(j["duration"]),
            door_to_door=max(int((arrive - depart_wanted).total_seconds() // 60), int(j["duration"])),
            changes=max(len(transit) - 1, 0),
            walk_minutes=sum(int(l["duration"]) for l in legs if l["mode"]["id"] == "walking"),
            route=" → ".join(_leg_label(l) for l in legs),
        ))
    return out


_last_call = [0.0]


def plan(origin: tuple[float, float], dest: tuple[float, float], when: datetime,
         session: requests.Session, app_key: str | None = None,
         min_interval: float = 0.15) -> list[Journey]:
    """All journeys TfL suggests for one departure time."""
    params = {"date": when.strftime("%Y%m%d"), "time": when.strftime("%H%M"),
              "timeIs": "Departing", "mode": TFL_MODES, "journeyPreference": "LeastTime"}
    if app_key:
        params["app_key"] = app_key
    url = TFL_URL.format(f"{origin[0]:.5f},{origin[1]:.5f}", f"{dest[0]:.5f},{dest[1]:.5f}")
    wait = min_interval - (time.monotonic() - _last_call[0])
    if wait > 0:
        time.sleep(wait)
    # Errors from requests include the full URL, and the URL contains app_key. Never let
    # them reach the UI: replace them with messages that don't mention the URL.
    try:
        r = session.get(url, params=params, timeout=20)
    except requests.RequestException as e:
        raise CommuteError(f"Couldn't reach TfL ({type(e).__name__}). Try again in a minute.") from None
    if not getattr(r, "from_cache", False):
        _last_call[0] = time.monotonic()
    if r.status_code == 429:
        raise CommuteError("TfL rate limit hit. Add a TFL_APP_KEY or try again in a minute.")
    if r.status_code == 300:   # TfL disambiguation: shouldn't happen with lat,lon
        raise CommuteError("TfL could not resolve one of the locations.")
    if r.status_code >= 400:   # not raise_for_status(): its message contains the URL
        raise CommuteError(f"TfL returned an error ({r.status_code}). Try again later.")
    try:
        return parse_journeys(r.json(), when)
    except ValueError:
        raise CommuteError("TfL sent a response that couldn't be read.") from None


def departure_times(day: date, start: str = "07:30", window_min: int = 30,
                    step_min: int = 10) -> list[datetime]:
    t0 = datetime.combine(day, datetime.strptime(start, "%H:%M").time())
    return [t0 + timedelta(minutes=m) for m in range(0, window_min + 1, step_min)]


def next_weekday(today: date | None = None, weekday: int = 1) -> date:
    """Next given weekday (default Tuesday) strictly after today, so timetables are live."""
    today = today or date.today()
    days = (weekday - today.weekday()) % 7 or 7
    return today + timedelta(days=days)


def robust_commute(origin, dest, times: list[datetime], session, app_key=None) -> dict:
    """Best journey for each departure time -> fastest / median / worst door-to-door."""
    best = []
    for t in times:
        js = plan(origin, dest, t, session, app_key)
        if js:
            best.append(min(js, key=lambda j: j.door_to_door))
    if not best:
        raise CommuteError("TfL returned no journeys for this pair.")
    d2d = [j.door_to_door for j in best]
    typical = sorted(best, key=lambda j: j.door_to_door)[len(best) // 2]
    return {
        "fastest_min": min(j.duration for j in best),
        "median_min": statistics.median(d2d),
        "worst_min": max(d2d),
        "changes": typical.changes,
        "walk_min": typical.walk_minutes,
        "route": typical.route,
        "samples": len(best),
    }


def weekly_summary(rows: list[dict]) -> list[dict]:
    """rows: dicts with person, days, median_min -> per-person hours/week (both directions)."""
    people: dict[str, dict] = {}
    for r in rows:
        p = people.setdefault(r["person"], {"person": r["person"], "weekly_min": 0.0, "offices": 0})
        if r.get("median_min") is not None and r.get("days", 0) > 0:
            p["weekly_min"] += 2 * r["days"] * r["median_min"]
            p["offices"] += 1
    return list(people.values())
