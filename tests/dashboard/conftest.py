from datetime import date, datetime, timezone

import pytest

from dashboard.config import from_env
from openstride import ClubActivity, ClubAthlete, ClubRoster, LeaderboardEntry


@pytest.fixture
def settings(tmp_path):
    return from_env({"OPENSTRIDE_CLUB_ID": "1", "OPENSTRIDE_CHALLENGE_START": "2026-09-14", "OPENSTRIDE_TIMEZONE": "Asia/Singapore",
                     "OPENSTRIDE_DATA_DIR": str(tmp_path), "OPENSTRIDE_CLUB_NAME": "Test Club", "OPENSTRIDE_API_TOKEN": "secret"})


def activity(aid, athlete_id, name="Alice Anon", sport="Run", km=5.0, start="2026-09-15T00:00:00Z", moving=1800,
             elapsed=1900, elev=10.0, device="Garmin"):
    return ClubActivity.from_feed({
        "id": aid, "athlete_id": athlete_id, "athlete_name": name, "sport_type": sport, "name": "Morning",
        "distance": km * 1000, "moving_time": moving, "elapsed_time": elapsed, "total_elevation_gain": elev,
        "start_date": start, "device_name": device, "is_virtual": False, "entity": "Activity"})


class FakeScraper:
    """Stands in for openstride.ClubScraper in a sync."""

    def __init__(self, roster=None, feed=None, boards=None, weeks=None):
        self.roster = roster or ClubRoster(member_count=2, members=[
            ClubAthlete.from_roster(10, "Alice Anon", True), ClubAthlete.from_roster(11, "Bob Bogus", False)])
        self.feed = feed if feed is not None else [activity(1, 10), activity(2, 11, "Bob Bogus", "Ride")]
        self.boards = boards or {"this": [], "last": []}
        self.weeks = weeks
        self.week_requests = None

    def __call__(self, *args, **kwargs):   # used as the scraper factory
        return self

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        pass

    def get_club_roster(self, club_id):
        return self.roster

    def get_club_activities(self, club_id, limit=None):
        return self.feed

    def get_club_leaderboard(self, club_id, week="this"):
        return self.boards[week]

    def get_athlete_weeks(self, jobs):
        from openstride import AthleteWeeks
        self.week_requests = list(jobs)
        return self.weeks or AthleteWeeks(weeks=[])


def board_entry(athlete_id, km, week="this", monday=date(2026, 9, 14), acts=1):
    return LeaderboardEntry(week=week, week_start=monday, rank=1, athlete_id=athlete_id, athlete_name="x",
                            distance=km * 1000, activities=acts, total_elevation_gain=5.0, moving_time=1200)


__all__ = ["activity", "FakeScraper", "board_entry", "datetime", "timezone"]
