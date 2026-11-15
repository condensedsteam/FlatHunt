import json
from datetime import date, datetime
from pathlib import Path

from flatcalc import commute as cm

FIXTURE = json.loads((Path(__file__).parent / "fixtures" / "tfl_journey.json").read_text())


class FakeResponse:
    def __init__(self, payload, status=200):
        self._p, self.status_code, self.from_cache = payload, status, False

    def json(self):
        return self._p

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(self.status_code)


class FakeSession:
    def __init__(self):
        self.calls = []

    def get(self, url, params=None, timeout=None):
        self.calls.append((url, params))
        if "postcodes.io" in url:
            return FakeResponse({"result": {"latitude": 51.46, "longitude": -0.06, "postcode": "SE15 3EE"}})
        return FakeResponse(FIXTURE)


def test_parse_counts_changes_walking_and_waiting():
    js = cm.parse_journeys(FIXTURE, datetime(2026, 11, 17, 7, 30))
    fast, slow = js
    assert fast.changes == 0 and fast.walk_minutes == 16 and fast.door_to_door == 35
    assert slow.changes == 1 and slow.route == "Walk 6 → Windrush → Elizabeth line → Walk 7"


def test_geocode_latlon_and_postcode():
    assert cm.geocode("51.5, -0.1", FakeSession())[:2] == (51.5, -0.1)
    assert cm.geocode("se15 3ee", FakeSession())[2] == "SE15 3EE"


def test_robust_commute_samples_window():
    sess = FakeSession()
    times = cm.departure_times(date(2026, 11, 17), "07:30", 30, 10)
    out = cm.robust_commute((51.46, -0.06), (51.51, -0.09), times, sess, "key")
    assert out["samples"] == 4 and len(sess.calls) == 4
    assert sess.calls[0][1]["app_key"] == "key"
    # same fixture each time: wanting to leave later means less waiting, never negative
    assert out["fastest_min"] == 31 and out["worst_min"] == 35


def test_errors_never_expose_app_key():
    import pytest
    import requests

    class BadStatus(FakeSession):
        def get(self, url, params=None, timeout=None):
            return FakeResponse({}, status=500)

    class Unreachable(FakeSession):
        def get(self, url, params=None, timeout=None):
            raise requests.ConnectionError(f"Max retries exceeded with url: {url}?app_key={params['app_key']}")

    for sess in (BadStatus(), Unreachable()):
        with pytest.raises(cm.CommuteError) as err:
            cm.plan((51.46, -0.06), (51.51, -0.09), datetime(2026, 11, 17, 7, 30), sess, "SECRET123")
        assert "SECRET123" not in str(err.value) and "app_key" not in str(err.value)


def test_weekly_summary_ignores_zero_day_offices():
    rows = [{"person": "Me", "days": 3, "median_min": 30},
            {"person": "Me", "days": 0, "median_min": 20},
            {"person": "Partner", "days": 2, "median_min": 40}]
    w = {r["person"]: r["weekly_min"] for r in cm.weekly_summary(rows)}
    assert w == {"Me": 180, "Partner": 160}


def test_next_weekday_is_future_tuesday():
    d = cm.next_weekday(date(2026, 11, 12))
    assert d == date(2026, 11, 17) and d.weekday() == 1
