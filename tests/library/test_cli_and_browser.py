"""The command line (scraper and login faked) and the Browser's own logic (Playwright faked)."""
import json
import stat
import sys

import pytest

from openstride import RateLimited, SessionExpired, _browser, cli
from openstride._browser import Browser, _is_login
from openstride.models import ClubAthlete


class FakeScraper:
    def __init__(self, *a, **k):
        pass

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        pass

    def get_club_member_count(self, club_id):
        return 3

    def get_club_members(self, club_id, limit):
        return [ClubAthlete.from_roster(1, "Ada Lovelace", True)][:limit]

    get_club_admins = get_club_members

    def get_club_activities(self, club_id, limit):
        raise RateLimited("slow down")


@pytest.fixture
def fake_cli(monkeypatch):
    monkeypatch.setattr(cli, "ClubScraper", FakeScraper)
    monkeypatch.setattr(cli, "login", lambda path, channel: f"{path}|{channel}")


def test_cli_commands(fake_cli, capsys):
    assert cli.main(["count", "1"]) == 0 and capsys.readouterr().out.strip() == "3"
    assert cli.main(["admins", "1", "--limit", "1"]) == 0
    assert json.loads(capsys.readouterr().out)[0]["name"] == "Ada Lovelace"
    assert cli.main(["login", "--session-file", "s.json", "--channel", "chrome"]) == 0
    assert "s.json|chrome" in capsys.readouterr().out


def test_cli_reports_errors_with_exit_1(fake_cli, capsys):
    assert cli.main(["activities", "1"]) == 1
    assert "error: slow down" in capsys.readouterr().err


def test_cli_needs_a_command():
    with pytest.raises(SystemExit):
        cli.main([])


def test_module_entry_point(fake_cli, monkeypatch):
    monkeypatch.setattr(sys, "argv", ["openstride", "count", "1"])
    import runpy
    with pytest.raises(SystemExit) as e:
        runpy.run_module("openstride", run_name="__main__")
    assert e.value.code == 0


@pytest.mark.parametrize("url,expected", [("https://www.strava.com/login", True), ("/session/new", True),
                                          ("https://www.strava.com/clubs/1", False), ("/dashboard", False)])
def test_is_login(url, expected):
    assert _is_login(url) is expected


class FakePage:
    def __init__(self, result=None, url="https://www.strava.com/dashboard"):
        self.result, self.url, self.waits, self.gone_to = result, url, [], []

    def evaluate(self, script, arg):
        return self.result

    def goto(self, url, wait_until):
        self.gone_to.append(url)

    def wait_for_timeout(self, ms):
        self.waits.append(ms)


def opened(page, **kw):
    b = Browser("unused.json", **kw)
    b._page = page
    return b


@pytest.mark.parametrize("result,error", [
    ({"ok": False, "status": 401, "url": "https://www.strava.com/x", "text": ""}, SessionExpired),
    ({"ok": True, "status": 200, "url": "https://www.strava.com/login", "text": ""}, SessionExpired),
    ({"ok": False, "status": 429, "url": "https://www.strava.com/x", "text": ""}, RateLimited),
])
def test_fetch_errors(result, error):
    with pytest.raises(error):
        opened(FakePage(result)).fetch("/x")


def test_fetch_ok_goto_and_pause():
    page = FakePage({"ok": True, "status": 200, "url": "https://www.strava.com/x", "text": "hi"})
    b = opened(page, delay=(0.001, 0.002))
    assert b.fetch("/x") == "hi"
    assert b.ensure_on_site("/dashboard") is page and page.gone_to == []     # already on the site
    page.url = "about:blank"
    b.ensure_on_site("/dashboard")
    assert page.gone_to == ["https://www.strava.com/dashboard"] and page.waits
    page.url = "https://www.strava.com/login"
    with pytest.raises(SessionExpired):
        b.goto("/clubs/1")


def test_no_pause_when_disabled_or_closed():
    page = FakePage()
    opened(page, delay=(0, 0)).pause()
    Browser("unused.json").pause()
    assert page.waits == []


def test_close_is_safe_twice_and_swallows_errors():
    class Boom:
        def close(self):
            raise RuntimeError

        stop = close

    b = Browser("unused.json")
    b._browser, b._pw, b._page = Boom(), Boom(), FakePage()
    b.close()
    b.close()
    assert b._page is None


def test_open_without_session_is_session_expired(tmp_path):
    with pytest.raises(SessionExpired):
        Browser(tmp_path / "missing.json")._open()


class FakeCtx:
    def new_page(self):
        return self.page

    def storage_state(self):
        return {"cookies": [{"name": "c"}], "origins": []}


class FakePW:
    """Stands in for sync_playwright(): context manager and .start()/.stop()."""

    def __init__(self, fail=False):
        self.fail, self.stopped = fail, False
        self.chromium = self

    def __call__(self):
        return self

    def start(self):
        return self

    def stop(self):
        self.stopped = True

    __enter__ = start

    def __exit__(self, *exc):
        pass


def test_open_launches_once_and_cleans_up_on_failure(tmp_path, monkeypatch):
    session = tmp_path / "s.json"
    session.write_text("{}")
    page, pw = FakePage(), FakePW()
    ctx = FakeCtx()
    ctx.page = page
    monkeypatch.setattr("playwright.sync_api.sync_playwright", pw)
    monkeypatch.setattr(_browser, "_launch", lambda *a: (None, ctx))
    b = Browser(session)
    assert b._open() is page and b._open() is page

    def broken(*a):
        raise RuntimeError("no chromium")

    monkeypatch.setattr(_browser, "_launch", broken)
    b2 = Browser(session)
    with pytest.raises(RuntimeError):
        b2._open()
    assert pw.stopped and b2._page is None


def test_login_saves_an_owner_only_session(tmp_path, monkeypatch):
    class LoginPage(FakePage):
        def goto(self, url):
            self.gone_to.append(url)

        def wait_for_url(self, glob, timeout):
            self.waited = (glob, timeout)

    class B:
        closed = False

        def close(self):
            B.closed = True

    ctx = FakeCtx()
    ctx.page = LoginPage()
    monkeypatch.setattr("playwright.sync_api.sync_playwright", FakePW())
    monkeypatch.setattr(_browser, "_launch", lambda *a: (B(), ctx))
    path = _browser.login(tmp_path / "sub" / "auth.json", timeout_s=60)
    assert json.loads(path.read_text())["cookies"] == [{"name": "c"}]
    assert ctx.page.gone_to == [_browser.LOGIN_URL] and B.closed
    if sys.platform != "win32":
        assert stat.S_IMODE(path.stat().st_mode) == 0o600
