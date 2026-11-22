"""The models are stravalib models (drop-in), and Client is a stravalib.Client whose club methods use the scraper."""
from datetime import datetime, timezone

import stravalib
from stravalib import model as sm
from stravalib import strava_model as ssm

from openstride import ClubActivity, ClubAdmin, ClubAthlete, Client
from openstride import _parse as P
from openstride.models import sport_and_type

from .test_parse import activity_entry


def test_club_activity_is_a_stravalib_club_activity_with_extra_fields():
    (row,) = P.feed_page([activity_entry(type_="TrailRun")])
    a = ClubActivity.from_feed(row)
    assert isinstance(a, sm.ClubActivity)
    assert isinstance(a.athlete, ssm.ClubAthlete)
    assert (a.id, a.athlete_id, a.athlete.id, a.athlete.firstname, a.athlete.lastname) == (111, 9, 9, "Sam", "Q")
    assert a.sport_type.root == "TrailRun" and a.type.root == "Run" and a.raw_type == "TrailRun"
    assert a.start_date == datetime(2026, 9, 14, 7, tzinfo=timezone.utc)
    assert (a.distance, a.moving_time, a.elapsed_time, a.total_elevation_gain) == (5000.0, 1500, 2000, 12.0)


def test_unknown_sport_keeps_the_raw_value_instead_of_failing():
    (row,) = P.feed_page([activity_entry(type_="Underwater Hockey")])
    a = ClubActivity.from_feed(row)
    assert a.sport_type is None and a.type is None and a.raw_type == "Underwater Hockey"


def test_sport_and_type():
    assert sport_and_type("Run") == ("Run", "Run")
    assert sport_and_type("GravelRide") == ("GravelRide", "Ride")
    assert sport_and_type("Padel") == ("Padel", None)       # a sport with no legacy activity type
    assert sport_and_type(None) == (None, None)


def test_roster_models():
    m = ClubAthlete.from_roster(7, "Ada Lovelace", True)
    assert isinstance(m, ssm.ClubAthlete)
    assert (m.id, m.firstname, m.lastname, m.admin, m.member, m.owner) == (7, "Ada", "Lovelace", True, "accepted", None)
    a = ClubAdmin.from_member(m)
    assert isinstance(a, sm.SummaryAthlete) and (a.id, a.name) == (7, "Ada Lovelace")


class _Scraper:
    def __init__(self):
        self.calls, self.closed = [], False

    def get_club_activities(self, club_id, limit=None):
        self.calls.append(("activities", club_id, limit))
        return ["a"]

    def get_club_members(self, club_id, limit=None):
        self.calls.append(("members", club_id, limit))
        return ["m"]

    def get_club_admins(self, club_id, limit=None):
        self.calls.append(("admins", club_id, limit))
        return ["x"]

    def close(self):
        self.closed = True


def test_client_is_stravalib_and_routes_club_methods_to_the_scraper():
    s = _Scraper()
    with Client(access_token="t", scraper=s) as c:
        assert isinstance(c, stravalib.Client)
        assert c.access_token == "t"
        assert c.get_club_activities(1, limit=5) == ["a"]
        assert c.get_club_members(1) == ["m"]
        assert c.get_club_admins(1) == ["x"]
    assert s.calls == [("activities", 1, 5), ("members", 1, None), ("admins", 1, None)]
    assert s.closed


def test_client_makes_a_scraper_lazily_without_opening_a_browser():
    c = Client(session_file="nowhere.json")
    assert c._scraper is None
    assert c.scraper._browser.session_file.name == "nowhere.json"
    c.close()
