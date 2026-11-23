"""End to end in real headless Chromium against the fake site: library calls, the CLI, and a dashboard sync
served by the API."""
import json
from datetime import date

import pytest
from fastapi.testclient import TestClient

from dashboard.api import create_app
from dashboard.config import from_env
from dashboard.scheduler import Scheduler
from openstride import ClubScraper, MarkupChanged, RateLimited, SessionExpired, cli

from .conftest import CLUB

pytestmark = pytest.mark.e2e


def scraper(session):
    return ClubScraper(session)


def test_library_reads_every_club_endpoint(fake_strava):
    site, session = fake_strava
    with scraper(session) as s:
        acts = s.get_club_activities(CLUB)
        roster = s.get_club_roster(CLUB)
        admins = s.get_club_admins(CLUB)
        board = s.get_club_leaderboard(CLUB, "last")
        weeks = s.get_athlete_weeks([(10, date(2026, 9, 16)), (11, date(2026, 9, 16))])
    assert [(a.id, a.athlete_id, a.sport_type.root) for a in acts] == [(1, 10, "Run"), (2, 11, "Ride")]
    assert roster.complete and [(m.id, m.admin) for m in roster.members] == [(10, True), (11, False)]
    assert [a.id for a in admins] == [10]
    assert [(e.athlete_id, e.distance, e.moving_time) for e in board] == [(10, 12500.0, 3900)]
    w10, w11 = weeks.weeks
    assert (w10.visible, [a.id for a in w10.activities]) == (True, [110])
    assert (w11.visible, w11.activities) == (False, [])
    assert f"/clubs/{CLUB}/recent_activity" in site.requests


@pytest.mark.parametrize("status,error", [(401, SessionExpired), (429, RateLimited), (500, MarkupChanged)])
def test_http_failures_map_to_errors(fake_strava, status, error):
    site, session = fake_strava
    site.overrides[f"/clubs/{CLUB}/feed"] = (status, "nope")
    with scraper(session) as s, pytest.raises(error):
        s.get_club_activities(CLUB)


def test_login_redirect_is_session_expired(fake_strava):
    site, session = fake_strava
    site.overrides[f"/clubs/{CLUB}/recent_activity"] = (302, "")
    with scraper(session) as s, pytest.raises(SessionExpired):
        s.get_club_activities(CLUB)


def test_cli_prints_json(fake_strava, capsys):
    _, session = fake_strava
    assert cli.main(["members", str(CLUB), "--session-file", str(session)]) == 0
    assert [m["id"] for m in json.loads(capsys.readouterr().out)] == [10, 11]
    assert cli.main(["count", str(CLUB), "--session-file", str(session)]) == 0
    assert capsys.readouterr().out.strip() == "2"


def test_dashboard_sync_then_api(fake_strava, tmp_path):
    _, session = fake_strava
    s = from_env({"OPENSTRIDE_CLUB_ID": str(CLUB), "OPENSTRIDE_CHALLENGE_START": "2026-09-14",
                  "OPENSTRIDE_TIMEZONE": "Asia/Singapore", "OPENSTRIDE_DATA_DIR": str(tmp_path / "data"),
                  "OPENSTRIDE_SESSION_FILE": str(session), "OPENSTRIDE_API_TOKEN": "t"})
    sched = Scheduler(s)
    status = sched.run_once(scan=True)
    assert status["last_error"] == "", status
    assert status["summary"]["member_count"] == 2 and status["summary"]["scan_requests"] > 0

    c = TestClient(create_app(s, scheduler=sched, start_scheduler=False))
    assert c.get("/api/club").json()["sync"]["last_error"] == ""
    board = {r["athlete_id"]: r for r in c.get("/api/report").json()["leaderboard"]}
    assert set(board) == {"10", "11"}
    acts = c.get(f"/api/v3/clubs/{CLUB}/activities", headers={"Authorization": "Bearer t"}).json()
    assert {a["id"] for a in acts} >= {1, 2, 110}
