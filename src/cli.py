"""Command line: `openstride login` saves a session; `openstride activities|members|admins|count CLUB_ID` print JSON.

    openstride login [--session-file auth_state.json] [--channel chrome]
    openstride activities 123456 [--limit 20] [--session-file ...]
"""
from __future__ import annotations

import argparse
import json
import sys

from . import __version__
from ._browser import DEFAULT_SESSION_FILE, login
from .errors import OpenStrideError
from .scraper import ClubScraper


def main(argv: list[str] | None = None) -> int:
    """Run the command line.

    Args:
        argv: Arguments without the program name; None reads sys.argv.

    Returns:
        Exit code: 0 on success, 1 on an OpenStrideError.
    """
    p = argparse.ArgumentParser(prog="openstride", description="Strava-compatible club data (unofficial).")
    p.add_argument("--version", action="version", version=f"openstride {__version__}")
    sub = p.add_subparsers(dest="command", required=True)

    lg = sub.add_parser("login", help="log in in a browser window and save the session")
    lg.add_argument("--session-file", default=DEFAULT_SESSION_FILE)
    lg.add_argument("--channel", default=None, help='installed browser to use, e.g. "chrome" or "msedge"')

    for name, help_ in (("activities", "recent club activities"), ("members", "club members"),
                        ("admins", "club admins"), ("count", "club member count")):
        c = sub.add_parser(name, help=help_)
        c.add_argument("club_id", type=int)
        c.add_argument("--session-file", default=DEFAULT_SESSION_FILE)
        if name != "count":
            c.add_argument("--limit", type=int, default=None)

    args = p.parse_args(argv)
    try:
        if args.command == "login":
            print(f"Session saved to {login(args.session_file, channel=args.channel)}")
            return 0
        with ClubScraper(args.session_file) as s:
            if args.command == "count":
                print(s.get_club_member_count(args.club_id))
                return 0
            fetch = {"activities": s.get_club_activities, "members": s.get_club_members,
                     "admins": s.get_club_admins}[args.command]
            items = fetch(args.club_id, args.limit)
            print(json.dumps([i.model_dump(mode="json", exclude_none=True) for i in items], indent=2))
            return 0
    except OpenStrideError as e:
        print(f"error: {e}", file=sys.stderr)
        return 1
