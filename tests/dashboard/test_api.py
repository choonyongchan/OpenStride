"""The FastAPI app over a synced data directory (fake scraper): dashboard JSON and the Strava-shaped endpoints."""
from datetime import date

import pytest
from fastapi.testclient import TestClient

from dashboard import stats
from dashboard.api import create_app
from dashboard.scheduler import Scheduler
from dashboard.sync import run_sync

from .conftest import FakeScraper, activity, board_entry

AUTH = {"Authorization": "Bearer secret"}


@pytest.fixture
def client(settings):
    feed = [activity(1, 10, km=5.0), activity(2, 11, "Bob Bogus", "Ride"), activity(3, 12, "Alice Anon", km=12.0,
                                                                                    start="2026-09-16T00:00:00Z")]
    run_sync(settings, scraper_factory=FakeScraper(feed=feed, boards={"this": [board_entry(10, 8.0)], "last": []}))
    sched = Scheduler(settings, sync=lambda s, scan, setup: {"ok": True})
    return TestClient(create_app(settings, scheduler=sched, start_scheduler=False)), sched


def test_club_and_static_page(client):
    c, _ = client
    club = c.get("/api/club").json()
    assert club["name"] == "Test Club" and club["id"] == 1 and club["sync"]["last_error"] is None
    page = c.get("/")
    assert page.status_code == 200 and "Unofficial" in page.text


def test_report_is_keyed_by_athlete_so_namesakes_stay_apart(client):
    c, _ = client
    r = c.get("/api/report").json()
    board = {row["athlete_id"]: row for row in r["leaderboard"]}
    # athlete 12 shares Alice's display name in the feed but is a different person (not on the roster)
    assert board["10"]["km"] == 8.0 and board["12"]["km"] == 12.0
    assert board["12"]["name"] == "Alice Anon" and board["10"]["name"] == "Alice Anon"
    assert board["11"]["acts"] == 0                     # a member with no counted activity still listed
    assert r["member_count"] == 2 and r["awards"]["distance"]["athlete_id"] == "12"
    assert c.get("/api/report?date=2026-09-01").json()["total_km"] == 0


def test_dates_and_trend(client):
    c, _ = client
    dates = c.get("/api/dates").json()
    assert dates == sorted(dates)
    trend = c.get("/api/trend").json()
    assert trend[-1]["km"] == 20.0 and trend[-1]["active"] == 2


def test_strava_shaped_endpoints_need_the_token(client):
    c, _ = client
    assert c.get("/api/v3/clubs/1/activities").status_code == 401
    assert c.get("/api/v3/clubs/1/activities", headers={"Authorization": "Bearer nope"}).status_code == 401
    assert c.get("/api/v3/clubs/2/activities", headers=AUTH).status_code == 404


def test_club_activities_newest_first_and_paged(client):
    c, _ = client
    acts = c.get("/api/v3/clubs/1/activities", headers=AUTH).json()
    assert [a["id"] for a in acts] == [3, 2, 1]
    first = acts[-1]
    assert first["athlete"]["firstname"] == "Alice" and first["distance"] == 5000.0 and first["sport_type"] == "Run"
    assert set(first) >= {"athlete", "name", "distance", "moving_time", "elapsed_time", "total_elevation_gain",
                          "type", "sport_type"}
    assert [a["id"] for a in c.get("/api/v3/clubs/1/activities?page=2&per_page=2", headers=AUTH).json()] == [1]
    assert c.get("/api/v3/clubs/1/activities?per_page=500", headers=AUTH).status_code == 422


def test_club_members_and_admins(client):
    c, _ = client
    members = c.get("/api/v3/clubs/1/members", headers=AUTH).json()
    assert [(m["id"], m["admin"], m["member"]) for m in members] == [(10, True, "accepted"), (11, False, "accepted")]
    admins = c.get("/api/v3/clubs/1/admins", headers=AUTH).json()
    assert [(a["id"], a["firstname"], a["lastname"]) for a in admins] == [(10, "Alice", "Anon")]


def test_token_disabled_means_503(settings):
    from dataclasses import replace
    c = TestClient(create_app(replace(settings, api_token=""), start_scheduler=False))
    assert c.get("/api/v3/clubs/1/members").status_code == 503


def test_manual_sync_request(client):
    c, sched = client
    assert c.post("/api/sync").status_code == 401
    assert c.post("/api/sync?scan=true", headers=AUTH).status_code == 202
    assert sched._requested == {"scan": True, "setup": False}


def test_scheduler_records_status_and_scans_once_a_day(settings, monkeypatch):
    calls = []
    sched = Scheduler(settings, sync=lambda s, scan, setup: calls.append(scan) or {"n": 1})
    status = sched.run_once(scan=True)
    assert status["last_success"] and status["last_error"] == "" and status["last_scan_date"]
    from datetime import datetime
    late = datetime(2026, 10, 1, 23, 50, tzinfo=settings.tz)
    sched.store.update_status(last_scan_date="2026-10-01")
    assert not sched.scan_due(late)
    assert sched.scan_due(late.replace(day=2))
    assert not sched.scan_due(late.replace(day=2, hour=10))


def test_scheduler_keeps_errors(settings):
    from openstride import SessionExpired

    def boom(s, scan, setup):
        raise SessionExpired("log in again")
    status = Scheduler(settings, sync=boom).run_once()
    assert status["last_error"] == "SessionExpired: log in again" and status["running"] is False


def test_member_on():
    m = {"ingest_at": "2026-09-10T00:00:00+00:00", "left_at": "2026-09-20T00:00:00+00:00"}
    assert stats.member_on(m, "2026-09-15") and not stats.member_on(m, "2026-09-20")
    assert not stats.member_on(m, "2026-09-09")
    assert date.fromisoformat("2026-09-15")
