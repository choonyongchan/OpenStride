"""ClubScraper: Strava's removed club endpoints, read from the club pages a logged-in member sees."""
from __future__ import annotations

import json
from collections.abc import Iterable
from datetime import date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from pydantic import BaseModel, ValidationError

from . import _parse
from ._browser import DEFAULT_LOCALE, DEFAULT_SESSION_FILE, SESSION_EXPIRED_MSG, Browser
from .errors import MarkupChanged, SessionExpired
from .models import AthleteWeek, ClubActivity, ClubAdmin, ClubAthlete, ClubRoster, LeaderboardEntry

FEED_PAGES_MAX = 50       # circuit breaker: the feed only keeps about a day, a page or two
MEMBER_PAGES_MAX = 400    # circuit breaker: 12,000 members at 30 a page
FEED_PAGE_SIZE = 100
LEADERBOARD_SETTLE_MS = 1000   # after switching to last week, let the table re-render
WEEKS_WORKERS = 4
WEEKS_BATCH_SIZE = 24
HOME_PATH = "/dashboard"
WEEK_URL = "/athletes/{athlete_id}/interval?interval={week}&interval_type=week&chart_type=miles&year_offset=0"

# Leaderboard rank-table rows: the athlete id comes from the profile link, the rest are cell texts.
LEADERBOARD_JS = """() => [...document.querySelectorAll('.leaderboard tbody tr')].map(tr => {
    const c = [...tr.children].map(td => td.innerText.trim());
    const a = tr.querySelector('a.athlete-name');
    return a && {id: a.getAttribute('href').split('/').pop(), name: a.innerText.trim(),
                 rank: c[0], dist: c[2], acts: c[3], elev: c[4], time: c[5]};
}).filter(Boolean)"""

# Fetch a batch of profile-week URLs with `workers` concurrent requests inside the logged-in page. Each response
# is JavaScript; the week's activity list is the HTML string passed to jQuery('#interval-rides').html(...), whose
# feed component carries the entries in data-react-props.appContext.preFetchedEntries. The string literal is
# handed to the JS engine to unescape (its escapes aren't JSON).
# Per URL: {entries: [...]} | {entries: null} (private: no list) | {expired} | {error} | {limited} | null.
# The first 429 stops every worker (unfetched URLs stay null): retrying in parallel only deepens Strava's block.
WEEKS_JS = r"""async ({urls, workers}) => {
    const ATTEMPTS = 4, BACKOFF_MS = 2000, JITTER_MS = 1000, GAP_MS = 150, GAP_JITTER_MS = 350;
    const sleep = (ms) => new Promise(res => setTimeout(res, ms));
    const parse = (t) => {
        const marker = "jQuery('#interval-rides').html(";
        const start = t.indexOf(marker);
        // Only a "..." literal is evaluated: the scan below stops at its closing quote, so no code can follow.
        if (start < 0 || t[start + marker.length] !== '"') return null;
        let i = start + marker.length + 1;
        while (i < t.length && t[i] !== '"') i += t[i] === '\\' ? 2 : 1;
        const html = Function('return ' + t.slice(start + marker.length, i + 1))();
        const el = new DOMParser().parseFromString(html, 'text/html').querySelector('[data-react-props]');
        return el ? (JSON.parse(el.getAttribute('data-react-props')).appContext.preFetchedEntries || []) : null;
    };
    const one = async (url) => {
        let last = '';
        for (let attempt = 0; attempt < ATTEMPTS; attempt++) {
            if (attempt) await sleep(BACKOFF_MS * 2 ** attempt + Math.random() * JITTER_MS);
            try {
                const r = await fetch(url, {credentials: 'include', headers: {'X-Requested-With': 'XMLHttpRequest'}});
                if (r.url.includes('/login') || r.status === 401) return {expired: true, status: r.status};
                if (r.status === 429) { limited = true; return {limited: true}; }
                if (r.status >= 500) { last = 'HTTP ' + r.status; continue; }
                if (!r.ok) return {error: 'HTTP ' + r.status};
                return {entries: parse(await r.text())};
            } catch (e) { last = String(e); }
        }
        return {error: last};
    };
    const out = new Array(urls.length).fill(null);
    let next = 0, limited = false;
    const worker = async () => {
        while (next < urls.length && !limited) {
            const i = next++;
            out[i] = await one(urls[i]);
            await sleep(GAP_MS + Math.random() * GAP_JITTER_MS);
        }
    };
    await Promise.all(Array.from({length: Math.min(workers, urls.length)}, worker));
    return out;
}"""


def monday_of(d: date) -> date:
    """Find the Monday of a date's week.

    Args:
        d: Any date.

    Returns:
        The Monday on or before d.
    """
    return d - timedelta(days=d.weekday())


def week_id(monday: date) -> str:
    """Build Strava's interval id for a week: ISO year + ISO week, e.g. 2026-09-14 -> '202638'.

    Args:
        monday: The week's Monday.

    Returns:
        The six-digit interval id.
    """
    y, w, _ = monday.isocalendar()
    return f"{y}{w:02d}"


def _activities(rows: Iterable[dict]) -> list[ClubActivity]:
    """Build ClubActivity models from flat feed rows.

    Args:
        rows: _parse.feed_page() rows.

    Returns:
        The models.

    Raises:
        MarkupChanged: A row doesn't fit the model.
    """
    try:
        return [ClubActivity.from_feed(r) for r in rows]
    except ValidationError as e:
        raise MarkupChanged(f"An activity didn't have the expected shape: {e}") from e


def _member_count(page_html: str) -> int:
    """Read a members page's headline count.

    Args:
        page_html: A members page.

    Returns:
        The member count.

    Raises:
        MarkupChanged: The headline is missing.
    """
    count = _parse.parse_member_count(page_html)
    if count is None:
        raise MarkupChanged("No member count on the club's members page; Strava may have changed it.")
    return count


class AthleteWeeks(BaseModel):
    """Result of ClubScraper.get_athlete_weeks: what was fetched, and what wasn't.
    `rate_limited` means Strava answered 429 and the remaining requests were not sent (they are in `unfetched`)."""

    weeks: list[AthleteWeek]
    errors: int = 0
    rate_limited: bool = False
    unfetched: list[tuple[int, date]] = []


class ClubScraper:
    """Reads Strava club data from the website with a saved login session (see `openstride login`).

    Use it as a context manager so the browser is closed::

        with ClubScraper("auth_state.json") as s:
            acts = s.get_club_activities(123456)

    Every call opens pages as the logged-in account, so it sees what that account sees: activities visible to it,
    and the full roster only if the account may view the club's members. Not thread-safe; one per thread.
    """

    def __init__(self, session_file: str | Path = DEFAULT_SESSION_FILE, *, headless: bool = True,
                 browser_channel: str | None = None, timezone: str | None = None, locale: str = DEFAULT_LOCALE):
        """Set up the scraper; the browser opens on the first request.

        Args:
            session_file: Saved login from `openstride login` (Playwright storage state JSON).
            headless: Run Chromium without a window.
            browser_channel: "" / None for Playwright's Chromium, or "chrome" / "msedge" for an installed browser.
            timezone: IANA zone the club's weeks are counted in (default: this machine's local zone).
            locale: Browser locale. Strava's own language setting must be English either way.
        """
        self.timezone = timezone
        self._browser = Browser(session_file, headless=headless, channel=browser_channel, locale=locale,
                                timezone_id=timezone)

    def __enter__(self) -> ClubScraper:
        """Enter the context.

        Returns:
            This scraper.
        """
        return self

    def __exit__(self, *exc) -> None:
        """Close the browser on leaving the context.

        Args:
            *exc: Exception info, ignored.
        """
        self.close()

    def close(self) -> None:
        """Close the browser. Safe to call twice; the next call reopens it."""
        self._browser.close()

    def _today(self) -> date:
        """Today's date in the scraper's timezone.

        Returns:
            Today's date.
        """
        return datetime.now(ZoneInfo(self.timezone) if self.timezone else None).date()

    # --- activities ----------------------------------------------------------------------------------------

    def get_club_activities(self, club_id: int, limit: int | None = None) -> list[ClubActivity]:
        """Recent activities of the club's members, newest upload first (replaces GET /clubs/{id}/activities).

        Strava's club feed keeps roughly the last 26 hours, whatever is asked for. Any sport. Each activity
        appears once, even when it is also part of a group activity card.

        Args:
            club_id: Strava club id.
            limit: Stop after this many activities; None for all.

        Returns:
            The activities.

        Raises:
            MarkupChanged: The feed is not JSON or its paging cursor is missing.
        """
        b = self._browser
        b.goto(f"/clubs/{club_id}/recent_activity")
        base = f"/clubs/{club_id}/feed?feed_type=club&num_entries={FEED_PAGE_SIZE}"
        url, out, seen = base, [], set()
        for _ in range(FEED_PAGES_MAX):
            text = b.fetch(url)
            try:
                data = json.loads(text)
            except ValueError as e:
                raise MarkupChanged(f"The club feed was not JSON: {text[:120]!r}") from e
            entries = data.get("entries") or []
            for act in _activities(_parse.feed_page(entries)):
                if act.id not in seen:
                    seen.add(act.id)
                    out.append(act)
                    if limit is not None and len(out) >= limit:
                        return out
            if not entries or not (data.get("pagination") or {}).get("hasMore"):
                break
            cursor = entries[-1].get("cursorData") or {}
            if "updated_at" not in cursor or "rank" not in cursor:
                raise MarkupChanged("The club feed's paging cursor is missing.")
            url = f"{base}&before={cursor['updated_at']}&cursor={cursor['rank']}"
            b.pause()
        return out

    # --- members -------------------------------------------------------------------------------------------

    def _members_page(self, club_id: int, page: int) -> str:
        """Fetch one members page.

        Args:
            club_id: Strava club id.
            page: 1-based page number.

        Returns:
            The page HTML.
        """
        return self._browser.fetch(f"/clubs/{club_id}/members?page={page}")

    def _open_members(self, club_id: int) -> str:
        """Open the club's members page in the browser, then fetch its first page.

        Args:
            club_id: Strava club id.

        Returns:
            Page 1's HTML.
        """
        self._browser.goto(f"/clubs/{club_id}/members")
        return self._members_page(club_id, 1)

    def _walk_roster(self, club_id: int, first: str) -> ClubRoster:
        """Read every members page once.

        Args:
            club_id: Strava club id.
            first: Page 1's HTML, already fetched.

        Returns:
            The headline count and every member seen.

        Raises:
            MarkupChanged: Page 1 has no member count.
        """
        count = _member_count(first)
        members: dict[int, ClubAthlete] = {}

        def add(rows) -> int:
            """Merge roster rows into `members`.

            Args:
                rows: _parse.parse_roster() tuples.

            Returns:
                How many athletes were new.
            """
            new = 0
            for aid, name, is_admin in rows:
                if aid not in members:
                    members[aid] = ClubAthlete.from_roster(aid, name, is_admin)
                    new += 1
                elif is_admin:   # admins are listed on every page; any listing as admin counts
                    members[aid].admin = True
            return new

        add(_parse.parse_roster(first))
        for n in range(2, MEMBER_PAGES_MAX + 1):
            self._browser.pause()
            if not add(_parse.parse_roster(self._members_page(club_id, n))):
                break   # past the end: nobody new on this page
        return ClubRoster(member_count=count, members=list(members.values()))

    def get_club_roster(self, club_id: int) -> ClubRoster:
        """The headline member count and every member, from one walk of the members pages.

        Walked twice if the first walk doesn't tally with the count, since a join or leave mid-walk shifts
        the pages.

        Args:
            club_id: Strava club id.

        Returns:
            The roster; check `complete` before trusting absences.
        """
        roster = self._walk_roster(club_id, self._open_members(club_id))
        if not roster.complete:
            roster = self._walk_roster(club_id, self._members_page(club_id, 1))
        return roster

    def get_club_members(self, club_id: int, limit: int | None = None) -> list[ClubAthlete]:
        """The club's members, admins first (replaces GET /clubs/{id}/members). `admin` marks administrators.

        Args:
            club_id: Strava club id.
            limit: Return at most this many; None for all.

        Returns:
            The members.
        """
        return self.get_club_roster(club_id).members[:limit]

    def get_club_admins(self, club_id: int, limit: int | None = None) -> list[ClubAdmin]:
        """The club's administrators (replaces GET /clubs/{id}/admins).

        Args:
            club_id: Strava club id.
            limit: Return at most this many; None for all.

        Returns:
            The admins.

        Raises:
            MarkupChanged: The members page no longer marks admins.
        """
        rows = _parse.parse_roster(self._open_members(club_id))
        if rows and all(is_admin is None for _, _, is_admin in rows):
            raise MarkupChanged("The members page no longer says who is an admin.")
        admins = {aid: name for aid, name, is_admin in rows if is_admin}   # every page lists all the admins
        return [ClubAdmin.from_member(ClubAthlete.from_roster(aid, name, True))
                for aid, name in admins.items()][:limit]

    def get_club_member_count(self, club_id: int) -> int:
        """The member count Strava shows on the club page.

        Args:
            club_id: Strava club id.

        Returns:
            The member count.

        Raises:
            MarkupChanged: The headline is missing.
        """
        return _member_count(self._open_members(club_id))

    # --- leaderboard ---------------------------------------------------------------------------------------

    def get_club_leaderboard(self, club_id: int, week: str = "this") -> list[LeaderboardEntry]:
        """The club's weekly leaderboard: about the top 100 athletes, club sport only.

        Figures are Strava's own weekly totals (moving time).

        Args:
            club_id: Strava club id.
            week: "this" or "last".

        Returns:
            The leaderboard rows.

        Raises:
            ValueError: week is not "this" or "last".
            MarkupChanged: A row can't be read.
        """
        if week not in ("this", "last"):
            raise ValueError('week must be "this" or "last"')
        page = self._browser.goto(f"/clubs/{club_id}/leaderboard")
        if week == "last":
            page.locator("span.button.last-week").click()
            page.wait_for_load_state("networkidle")
            page.wait_for_timeout(LEADERBOARD_SETTLE_MS)
        rows = _parse.leaderboard_rows(page.evaluate(LEADERBOARD_JS))
        start = monday_of(self._today()) - timedelta(weeks=1 if week == "last" else 0)
        return [LeaderboardEntry(week=week, week_start=start, **r) for r in rows]

    # --- athlete weeks -------------------------------------------------------------------------------------

    def get_athlete_weeks(self, requests: Iterable[tuple[int, date]], workers: int = WEEKS_WORKERS,
                          batch_size: int = WEEKS_BATCH_SIZE) -> AthleteWeeks:
        """Many (athlete_id, week_start) profile weeks, `workers` at a time.

        Stops at Strava's first HTTP 429 and returns what it fetched (rate_limited=True, the rest in `unfetched`).

        Args:
            requests: (athlete_id, any day of the week) pairs.
            workers: Concurrent requests inside the page.
            batch_size: URLs per page.evaluate round trip.

        Returns:
            The weeks read, the error count, and what was left unfetched.

        Raises:
            SessionExpired: Immediately, when the session has expired.
        """
        jobs = [(int(a), monday_of(w)) for a, w in requests]
        weeks, errors = [], 0
        page = self._browser.ensure_on_site(HOME_PATH)
        for b in range(0, len(jobs), batch_size):
            batch = jobs[b:b + batch_size]
            urls = [WEEK_URL.format(athlete_id=a, week=week_id(m)) for a, m in batch]
            results = page.evaluate(WEEKS_JS, {"urls": urls, "workers": workers})
            unfetched = []
            for (aid, monday), res in zip(batch, results, strict=True):
                if res is None or res.get("limited"):   # never sent: a 429 stopped the batch
                    unfetched.append((aid, monday))
                    continue
                if res.get("expired"):
                    raise SessionExpired(SESSION_EXPIRED_MSG)
                if "error" in res:
                    errors += 1
                    continue
                entries = res.get("entries")
                own = [a for a in _activities(_parse.feed_page(entries or [])) if a.athlete_id == aid]
                weeks.append(AthleteWeek(athlete_id=aid, week_start=monday, visible=entries is not None,
                                         activities=own))
            if unfetched:
                return AthleteWeeks(weeks=weeks, errors=errors, rate_limited=True,
                                    unfetched=unfetched + jobs[b + len(batch):])
        return AthleteWeeks(weeks=weeks, errors=errors)
