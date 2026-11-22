"""Return types. The three club models subclass stravalib's, so code written against
stravalib.Client.get_club_activities / get_club_members / get_club_admins keeps working (isinstance checks,
attribute names, units). They add the fields Strava's web pages expose but the old API never returned
(activity id, athlete id, start date, full name, ...).

Units follow the Strava API: metres, seconds, sport types as Strava's enum strings.
"""
from __future__ import annotations

from datetime import date, datetime
from typing import Literal, get_args

from pydantic import BaseModel
from stravalib import model as _m
from stravalib import strava_model as _sm

from ._parse import split_name

_SPORT_TYPES = frozenset(get_args(_sm.SportType.model_fields["root"].annotation))
_ACTIVITY_TYPES = frozenset(get_args(_sm.ActivityType.model_fields["root"].annotation))
# sport_type -> the deprecated `type` Strava's API reported alongside it. Only mappings Strava documents;
# any other sport_type that is not itself an ActivityType gets type=None.
_LEGACY_TYPE = {"TrailRun": "Run", "MountainBikeRide": "Ride", "GravelRide": "Ride",
                "EMountainBikeRide": "EBikeRide"}
SUMMARY_STATE = 2              # stravalib resource_state for summary-level objects
MEMBER_ACCEPTED = "accepted"   # ClubAthlete.member for everyone on the roster


def sport_and_type(raw: str | None) -> tuple[str | None, str | None]:
    """Map a sport string from Strava's pages to stravalib's sport_type and legacy type.

    Args:
        raw: Sport as Strava sent it, e.g. "TrailRun".

    Returns:
        (sport_type, type), each None when stravalib doesn't know it.
    """
    sport = raw if raw in _SPORT_TYPES else None
    legacy = raw if raw in _ACTIVITY_TYPES else _LEGACY_TYPE.get(raw or "")
    return sport, legacy


class ClubAthlete(_sm.ClubAthlete):
    """A club member (the removed GET /clubs/{id}/members). Adds `id` and the full display `name`.

    Unlike the old API, `lastname` is the full last name, not an initial. `member` is "accepted" for everyone
    on the roster; `admin` is None when the page didn't say; `owner` is always None (the page doesn't show it).
    """

    id: int | None = None
    name: str | None = None

    @classmethod
    def from_roster(cls, athlete_id: int, name: str, is_admin: bool | None) -> ClubAthlete:
        """Build from one members-page row.

        Args:
            athlete_id: Strava athlete id.
            name: Display name.
            is_admin: Whether the row was under the Admins heading; None if unknown.

        Returns:
            The member.
        """
        first, last = split_name(name)
        return cls(id=athlete_id, name=name, firstname=first, lastname=last, member=MEMBER_ACCEPTED,
                   admin=is_admin, owner=None, resource_state=SUMMARY_STATE)


class ClubAdmin(_m.SummaryAthlete):
    """A club administrator (the removed GET /clubs/{id}/admins returned SummaryAthlete). Adds the full `name`."""

    name: str | None = None

    @classmethod
    def from_member(cls, member: ClubAthlete) -> ClubAdmin:
        """Build from a member known to be an admin.

        Args:
            member: The member.

        Returns:
            The admin.
        """
        return cls(id=member.id, name=member.name, firstname=member.firstname, lastname=member.lastname,
                   resource_state=SUMMARY_STATE)


class ClubActivity(_m.ClubActivity):
    """A club activity (the removed GET /clubs/{id}/activities), with the fields the old API left out.

    `distance`, `moving_time` and `total_elevation_gain` are parsed from the numbers Strava displays, so they are
    rounded the way the page rounds them (distance to 10 m, elevation to 1 m or 1 ft). `raw_type` is the sport
    exactly as Strava sent it, kept even when stravalib's SportType doesn't know it yet.
    """

    athlete: ClubAthlete | None = None
    id: int
    athlete_id: int
    raw_type: str | None = None
    start_date: datetime | None = None
    start_date_local: datetime | None = None
    steps: int | None = None
    pace: str | None = None
    device_name: str | None = None
    is_virtual: bool | None = None
    is_commute: bool | None = None
    visibility: str | None = None
    location: str | None = None
    description: str | None = None
    kudos_count: int | None = None
    comment_count: int | None = None
    entity: str | None = None   # "Activity" or "GroupActivity": which kind of feed card it came from

    @classmethod
    def from_feed(cls, row: dict) -> ClubActivity:
        """Build from one flat feed row.

        Args:
            row: One _parse.feed_entry_activities() dict.

        Returns:
            The activity, with a summary athlete and stravalib sport/type set where known.
        """
        sport, legacy = sport_and_type(row.get("sport_type"))
        first, last = split_name(row.get("athlete_name") or "")
        athlete = ClubAthlete(id=row["athlete_id"], name=row.get("athlete_name"),
                              firstname=row.get("athlete_firstname") or first, lastname=last, resource_state=SUMMARY_STATE)
        fields = {k: v for k, v in row.items() if k in cls.model_fields and k != "sport_type"}
        return cls(**fields, athlete=athlete, sport_type=sport, type=legacy, raw_type=row.get("sport_type"))


class ClubRoster(BaseModel):
    """One walk of a club's members pages. `complete` is False when the roster didn't tally with the headline
    count (someone joined or left mid-walk, or a page failed); treat absences as unreliable then."""

    member_count: int
    members: list[ClubAthlete]

    @property
    def complete(self) -> bool:
        """Whether the roster tallies with the headline count.

        Returns:
            True when len(members) == member_count.
        """
        return len(self.members) == self.member_count


class LeaderboardEntry(BaseModel):
    """One row of a club's weekly leaderboard (the club's sport only, about the top 100 athletes)."""

    week: Literal["this", "last"]
    week_start: date
    rank: int | None
    athlete_id: int
    athlete_name: str
    distance: float                 # metres
    activities: int
    total_elevation_gain: float     # metres
    moving_time: int                # seconds


class AthleteWeek(BaseModel):
    """One athlete's own activities in one Monday-Sunday week, from their profile.
    `visible` is False when the profile is private to the logged-in account (then `activities` is empty)."""

    athlete_id: int
    week_start: date
    visible: bool
    activities: list[ClubActivity]
