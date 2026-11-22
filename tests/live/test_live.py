"""Live checks against strava.com: the parsers agree with Strava's own figures. Opt in:

    OPENSTRIDE_TEST_CLUB_ID=123456 OPENSTRIDE_SESSION_FILE=auth_state.json python -m pytest tests/live --live

Read-only, but each run makes a few dozen requests as the logged-in account.
"""
import os

import pytest

from openstride import ClubScraper

pytestmark = pytest.mark.live
SPORTS = set(os.environ.get("OPENSTRIDE_SPORTS", "Run,TrailRun,VirtualRun,Walk,Hike").split(","))


@pytest.fixture(scope="module")
def club():
    club_id = os.environ.get("OPENSTRIDE_TEST_CLUB_ID")
    if not club_id:
        pytest.skip("set OPENSTRIDE_TEST_CLUB_ID")
    with ClubScraper(os.environ.get("OPENSTRIDE_SESSION_FILE", "auth_state.json"),
                     timezone=os.environ.get("OPENSTRIDE_TIMEZONE")) as s:
        yield s, int(club_id)


def test_roster_tallies_with_the_headline_and_has_admins(club):
    s, cid = club
    roster = s.get_club_roster(cid)
    assert roster.complete, f"roster {len(roster.members)} vs headline {roster.member_count}"
    assert len({m.id for m in roster.members}) == len(roster.members)
    admins = s.get_club_admins(cid)
    assert admins and {a.id for a in admins} <= {m.id for m in roster.members if m.admin}


def test_feed_parses_with_numbers(club):
    s, cid = club
    acts = s.get_club_activities(cid)
    assert all(a.id and a.athlete_id and a.start_date for a in acts)
    with_distance = [a for a in acts if a.raw_type in SPORTS]
    assert all(a.distance is not None and a.moving_time is not None for a in with_distance)


def test_profile_weeks_sum_to_the_leaderboard(club):
    """For the top of last week's leaderboard, the athlete's profile week (counted sports) must add up to Strava's
    own figure, within display rounding. Private profiles are skipped."""
    s, cid = club
    board = s.get_club_leaderboard(cid, "last")[:15]
    weeks = s.get_athlete_weeks([(e.athlete_id, e.week_start) for e in board], workers=2)
    assert not weeks.rate_limited
    by_id = {w.athlete_id: w for w in weeks.weeks if w.visible}
    mismatches = []
    for e in board:
        w = by_id.get(e.athlete_id)
        if not w:
            continue
        acts = [a for a in w.activities if a.raw_type in SPORTS]
        km = sum(a.distance or 0 for a in acts) / 1000
        if len(acts) != e.activities or abs(km - e.distance / 1000) > 0.02 * len(acts) + 0.11:
            mismatches.append(f"{e.athlete_name}: board {e.activities} acts {e.distance / 1000:.2f} km, "
                              f"profile {len(acts)} acts {km:.2f} km")
    assert not mismatches, "\n".join(mismatches)
