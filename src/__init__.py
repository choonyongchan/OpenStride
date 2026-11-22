"""openstride: Strava's removed club endpoints (club activities, members, admins), read from the club web pages,
plus stravalib's full API client.

Unofficial. Not affiliated with, endorsed or sponsored by Strava, Inc. For educational purposes only: using it
may breach Strava's Terms of Service, and you are responsible for how you use it.
"""
from ._browser import login
from .client import Client
from .errors import MarkupChanged, RateLimited, SessionExpired, OpenStrideError, UnsupportedLocale
from .models import AthleteWeek, ClubActivity, ClubAdmin, ClubAthlete, ClubRoster, LeaderboardEntry
from .scraper import AthleteWeeks, ClubScraper, monday_of, week_id

__version__ = "0.1.0"

__all__ = [
    "Client", "ClubScraper", "login",
    "ClubActivity", "ClubAthlete", "ClubAdmin", "ClubRoster", "LeaderboardEntry", "AthleteWeek", "AthleteWeeks",
    "OpenStrideError", "SessionExpired", "RateLimited", "MarkupChanged", "UnsupportedLocale",
    "monday_of", "week_id", "__version__",
]
