"""Client: stravalib's full Strava API client, with the three removed club endpoints served by ClubScraper."""
from __future__ import annotations

from pathlib import Path

import stravalib

from ._browser import DEFAULT_SESSION_FILE
from .models import ClubActivity, ClubAdmin, ClubAthlete
from .scraper import ClubScraper


class Client(stravalib.Client):
    """A drop-in stravalib.Client. Every official API method (OAuth, athlete, activities, streams, segments,
    routes, uploads...) is stravalib's own; get_club_activities, get_club_members and get_club_admins, whose
    endpoints Strava removed on 2026-09-01, are provided by OpenStride with a saved login session.

        client = Client(access_token="...", session_file="auth_state.json")
        me = client.get_athlete()                              # official API
        for a in client.get_club_activities(123456):           # OpenStride
            print(a.athlete.firstname, a.distance)
        client.close()

    The club methods return lists (not stravalib's lazy BatchedResultsIterator); iterate them the same way.
    """

    def __init__(self, access_token: str | None = None, *args, session_file: str | Path = DEFAULT_SESSION_FILE,
                 scraper: ClubScraper | None = None, **kwargs):
        """Make the client; the club backend is created on first club call.

        Args:
            access_token: OAuth token for the official API (optional if you only use the club methods).
            session_file: Saved login for the club methods (`openstride login`). Ignored if scraper is given.
            scraper: A ClubScraper to use for the club methods instead of making one.
            *args, **kwargs: Passed to stravalib.Client.
        """
        super().__init__(access_token, *args, **kwargs)
        self._session_file = session_file
        self._scraper = scraper

    @property
    def scraper(self) -> ClubScraper:
        """The ClubScraper behind the club methods (made on first use).

        Returns:
            The scraper.
        """
        if self._scraper is None:
            self._scraper = ClubScraper(self._session_file)
        return self._scraper

    def close(self) -> None:
        """Close the club backend's browser, if one was opened."""
        if self._scraper is not None:
            self._scraper.close()

    def __enter__(self) -> Client:
        """Enter the context.

        Returns:
            This client.
        """
        return self

    def __exit__(self, *exc) -> None:
        """Close the club backend on leaving the context.

        Args:
            *exc: Exception info, ignored.
        """
        self.close()

    def get_club_activities(self, club_id: int, limit: int | None = None) -> list[ClubActivity]:
        """Recent club activities (about the last 26 hours), newest first. See ClubScraper.get_club_activities.

        Args:
            club_id: Strava club id.
            limit: Return at most this many; None for all.

        Returns:
            The activities.
        """
        return self.scraper.get_club_activities(club_id, limit)

    def get_club_members(self, club_id: int, limit: int | None = None) -> list[ClubAthlete]:
        """The club's members, admins first. See ClubScraper.get_club_members.

        Args:
            club_id: Strava club id.
            limit: Return at most this many; None for all.

        Returns:
            The members.
        """
        return self.scraper.get_club_members(club_id, limit)

    def get_club_admins(self, club_id: int, limit: int | None = None) -> list[ClubAdmin]:
        """The club's administrators. See ClubScraper.get_club_admins.

        Args:
            club_id: Strava club id.
            limit: Return at most this many; None for all.

        Returns:
            The admins.
        """
        return self.scraper.get_club_admins(club_id, limit)
