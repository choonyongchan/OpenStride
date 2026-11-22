"""The logged-in browser every scrape runs in. Requests are fetch()es made from inside a strava.com page, so they
carry that page's cookies and headers exactly as the website's own requests do."""
from __future__ import annotations

import json
import os
import random
from contextlib import suppress
from pathlib import Path

from .errors import MarkupChanged, RateLimited, SessionExpired

BASE_URL = "https://www.strava.com"
LOGIN_URL = f"{BASE_URL}/login"
DEFAULT_SESSION_FILE = "auth_state.json"
DEFAULT_LOCALE = "en-US"
SESSION_EXPIRED_MSG = "The saved Strava session has expired. Run: openstride login"

# Paths Strava redirects to when the session is not logged in.
LOGIN_PATHS = ("/login", "/session", "/register", "/oauth")
# The URL Strava lands on after a successful interactive login.
LOGIN_DONE_GLOB = "**/dashboard**"
LOGIN_TIMEOUT_S = 300

LAUNCH_ARGS = ["--disable-blink-features=AutomationControlled"]
VIEWPORT = {"width": 1512, "height": 856}
DEFAULT_DELAY_S = (0.4, 0.9)   # random wait between requests, seconds

# Session cookies are credentials: owner-only file and directory.
SESSION_FILE_MODE = 0o600
SESSION_DIR_MODE = 0o700

HTTP_UNAUTHORIZED = 401
HTTP_TOO_MANY_REQUESTS = 429

# GET a same-site URL from inside a logged-in page; report where it ended up so a login redirect is visible.
FETCH_JS = """async (url) => {
    const r = await fetch(url, {credentials: 'include'});
    return {ok: r.ok, status: r.status, url: r.url, text: await r.text()};
}"""


def _is_login(url: str) -> bool:
    """Tell whether a URL is one of Strava's login pages.

    Args:
        url: Absolute or site-relative URL.

    Returns:
        True when the path after "strava.com" starts with a login path.
    """
    return url.split("strava.com", 1)[-1].startswith(LOGIN_PATHS)


def _launch(pw, headless: bool, channel: str | None, session_file: Path | None, locale: str, timezone_id: str | None):
    """Launch Chromium and a context set up like ordinary desktop browsing.

    Args:
        pw: A started Playwright instance.
        headless: Run without a window.
        channel: Installed browser channel ("chrome", "msedge"), or None for Playwright's Chromium.
        session_file: Storage-state JSON to load, if it exists.
        locale: Browser locale.
        timezone_id: IANA timezone for the context, or None for the machine's.

    Returns:
        (browser, context) tuple.
    """
    browser = pw.chromium.launch(headless=headless, channel=channel or None, args=LAUNCH_ARGS)
    # Headless Chromium otherwise advertises "HeadlessChrome/..."; real Chromium reports <major>.0.0.0.
    ver = f"{browser.version.split('.')[0]}.0.0.0"
    ctx = browser.new_context(
        storage_state=str(session_file) if session_file and session_file.exists() else None,
        user_agent=("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                    f"(KHTML, like Gecko) Chrome/{ver} Safari/537.36 Edg/{ver}"),
        locale=locale, timezone_id=timezone_id, viewport=VIEWPORT,
    )
    return browser, ctx


class Browser:
    """One Chromium with the saved session; opened lazily, closed by close(). Not thread-safe (Playwright's sync
    API binds to the thread that started it): use one Browser per thread."""

    def __init__(self, session_file: str | Path = DEFAULT_SESSION_FILE, *, headless: bool = True,
                 channel: str | None = None, locale: str = DEFAULT_LOCALE, timezone_id: str | None = None,
                 delay: tuple[float, float] = DEFAULT_DELAY_S):
        """Store settings; nothing is launched until the first request.

        Args:
            session_file: Saved login (Playwright storage state JSON).
            headless: Run Chromium without a window.
            channel: Installed browser channel, or None for Playwright's Chromium.
            locale: Browser locale.
            timezone_id: IANA timezone for the context, or None for the machine's.
            delay: (min, max) seconds to wait between requests; max 0 disables waiting.
        """
        self.session_file = Path(session_file)
        self.headless, self.channel, self.locale, self.timezone_id = headless, channel, locale, timezone_id
        self.delay = delay
        self._pw = self._browser = self._ctx = self._page = None

    def _open(self):
        """Launch the browser on first use.

        Returns:
            The single Playwright page every request runs in.

        Raises:
            SessionExpired: No saved session file.
        """
        if self._page is not None:
            return self._page
        if not self.session_file.exists():
            raise SessionExpired(f"No saved Strava session at {self.session_file}. Run: openstride login")
        from playwright.sync_api import sync_playwright   # imported here so parsing never needs Playwright
        self._pw = sync_playwright().start()
        try:
            self._browser, self._ctx = _launch(self._pw, self.headless, self.channel, self.session_file,
                                               self.locale, self.timezone_id)
            self._page = self._ctx.new_page()
        except Exception:
            self.close()
            raise
        return self._page

    def goto(self, path: str):
        """Navigate to BASE_URL + path.

        Args:
            path: Site-relative path.

        Returns:
            The page, after a short pause.

        Raises:
            SessionExpired: Strava redirected to its login page.
        """
        page = self._open()
        page.goto(BASE_URL + path, wait_until="domcontentloaded")
        if _is_login(page.url):
            raise SessionExpired(SESSION_EXPIRED_MSG)
        self.pause()
        return page

    def ensure_on_site(self, path: str):
        """Be on some strava.com page so fetches are same-origin.

        Args:
            path: Where to navigate if not already on the site.

        Returns:
            The page.
        """
        page = self._open()
        return page if page.url.startswith(BASE_URL) else self.goto(path)

    def fetch(self, path: str) -> str:
        """GET path from inside the page.

        Args:
            path: Site-relative path.

        Returns:
            The response text.

        Raises:
            SessionExpired: HTTP 401 or redirected to login.
            RateLimited: HTTP 429.
            MarkupChanged: Any other non-2xx status.
        """
        r = self._page.evaluate(FETCH_JS, path)
        if r["status"] == HTTP_UNAUTHORIZED or _is_login(r["url"]):
            raise SessionExpired(SESSION_EXPIRED_MSG)
        if r["status"] == HTTP_TOO_MANY_REQUESTS:
            raise RateLimited(f"Strava rate-limited {path} (HTTP 429); wait before retrying.")
        if not r["ok"]:
            raise MarkupChanged(f"{path} returned HTTP {r['status']}.")
        return r["text"]

    def pause(self) -> None:
        """Wait a short, random, human-sized time between requests (no-op before the browser opens)."""
        if self._page is not None and self.delay[1] > 0:
            self._page.wait_for_timeout(int(random.uniform(*self.delay) * 1000))

    def close(self) -> None:
        """Close the browser and stop Playwright. Safe to call twice; the next request reopens it."""
        with suppress(Exception):   # already gone; nothing left to release
            if self._browser is not None:
                self._browser.close()
        with suppress(Exception):
            if self._pw is not None:
                self._pw.stop()
        self._pw = self._browser = self._ctx = self._page = None


def login(session_file: str | Path = DEFAULT_SESSION_FILE, *, channel: str | None = None,
          timeout_s: int = LOGIN_TIMEOUT_S) -> Path:
    """Open a visible browser on Strava's login page, wait for the user to log in, and save the session.

    Args:
        session_file: Where to write the storage state JSON.
        channel: Installed browser channel, or None for Playwright's Chromium.
        timeout_s: How long to wait for the login to finish.

    Returns:
        Path of the saved session file.
    """
    from playwright.sync_api import sync_playwright
    path = Path(session_file)
    with sync_playwright() as pw:
        browser, ctx = _launch(pw, False, channel, None, DEFAULT_LOCALE, None)
        page = ctx.new_page()
        page.goto(LOGIN_URL)
        print(f"Log in to Strava in the browser window (waiting up to {timeout_s // 60} minutes)...")
        page.wait_for_url(LOGIN_DONE_GLOB, timeout=timeout_s * 1000)
        path.parent.mkdir(parents=True, exist_ok=True, mode=SESSION_DIR_MODE)
        state = ctx.storage_state()
        # Owner-only file, tightened even if an older copy exists.
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, SESSION_FILE_MODE)
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(state, f)
        os.chmod(path, SESSION_FILE_MODE)
        browser.close()
    return path
