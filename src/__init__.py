"""openstride: compatible with Strava. Club activities, members and admins for Python,
plus stravalib's full API client.

Unofficial. Not affiliated with, endorsed or sponsored by Strava, Inc. For educational purposes only: review
Strava's Terms of Service before use; you are responsible for how you use it.
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
