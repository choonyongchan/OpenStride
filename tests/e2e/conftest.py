"""A fake strava.com served to a real headless Chromium through Playwright request routing. Nothing leaves the
machine: every request to any host is answered here, so the library's real JavaScript (fetches, DOM reads, the
profile-week parser) runs end to end against known pages."""
import html
import json
from urllib.parse import parse_qs, urlparse

import pytest

from openstride import _browser

from ..library.test_parse import activity_entry, members_page

CLUB = 1
ADMINS = [("10", "Alice Anon")]
MEMBERS = [("11", "Bob Bogus")]
FEED = [activity_entry(1, 10, "Alice Anon"), activity_entry(2, 11, "Bob Bogus", type_="Ride")]


def _leaderboard(rows):
    trs = "".join(f"<tr><td>{rank}</td><td><a class='athlete-name' href='/athletes/{aid}'>{name}</a></td>"
                  f"<td>{dist}</td><td>{acts}</td><td>{elev}</td><td>{time}</td></tr>"
                  for rank, aid, name, dist, acts, elev, time in rows)
    return ("<span class='button last-week'>Last week</span>"
            f"<table class='leaderboard'><thead><tr><th>#</th></tr></thead><tbody>{trs}</tbody></table>")


def _week_js(entries):
    """A profile-week response: JS that hands the week's HTML to jQuery, entries in data-react-props."""
    inner = "<p>private</p>" if entries is None else \
        f"<div data-react-props=\"{html.escape(json.dumps({'appContext': {'preFetchedEntries': entries}}))}\"></div>"
    return f"jQuery('#interval-rides').html({json.dumps(inner)});"


class FakeStrava:
    """Routes for the pages the library reads; `overrides` {path: (status, body)} injects failures."""

    def __init__(self):
        self.overrides, self.requests = {}, []

    def answer(self, route):
        url = urlparse(route.request.url)
        self.requests.append(url.path)
        q = {k: v[0] for k, v in parse_qs(url.query).items()}
        if url.path in self.overrides:
            status, body = self.overrides[url.path]
            headers = {"location": "https://www.strava.com/login"} if status == 302 else {}
            return route.fulfill(status=status, body=body, headers=headers)
        status, ctype, body = 200, "text/html", "<html><body>ok</body></html>"
        if url.path == f"/clubs/{CLUB}/feed":
            ctype, body = "application/json", json.dumps({"entries": FEED, "pagination": {"hasMore": False}})
        elif url.path == f"/clubs/{CLUB}/members" and "page" in q:
            members = MEMBERS if q["page"] == "1" else []
            body = f"<span class='membership-count'>2 members</span>" + members_page(ADMINS, members)
        elif url.path == f"/clubs/{CLUB}/leaderboard":
            body = _leaderboard([("1", 10, "Alice Anon", "12.5 km", "2", "40 m", "1h 5m")])
        elif url.path.startswith("/athletes/") and url.path.endswith("/interval"):
            aid = int(url.path.split("/")[2])
            ctype = "text/javascript"
            body = _week_js([activity_entry(100 + aid, aid, "Alice Anon")] if aid == 10 else None)
        elif url.path not in ("/dashboard", f"/clubs/{CLUB}/recent_activity", f"/clubs/{CLUB}/members", "/login"):
            status = 404
        return route.fulfill(status=status, content_type=ctype, body=body)


@pytest.fixture
def fake_strava(monkeypatch, tmp_path):
    """Patch the browser launch so every context is routed to FakeStrava; yields (site, session_file)."""
    site = FakeStrava()
    real_launch = _browser._launch

    def launch(*args, **kwargs):
        browser, ctx = real_launch(*args, **kwargs)
        ctx.route("**/*", site.answer)
        return browser, ctx

    monkeypatch.setattr(_browser, "_launch", launch)
    session = tmp_path / "auth_state.json"
    session.write_text(json.dumps({"cookies": [], "origins": []}))
    return site, session
