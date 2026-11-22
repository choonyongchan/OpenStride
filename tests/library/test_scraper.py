"""ClubScraper over a fake browser (no Chromium): feed paging, roster walks, admins, profile-week batches."""
import json
from datetime import date

import pytest

from openstride import ClubScraper, MarkupChanged, SessionExpired, UnsupportedLocale
from openstride.scraper import week_id

from .test_parse import activity_entry, members_page


class FakePage:
    def __init__(self, evaluate):
        self._evaluate = evaluate

    def evaluate(self, script, arg=None):
        return self._evaluate(script, arg)


class FakeBrowser:
    """Answers fetch(path) from `responses` {path prefix: [text, ...]} (consumed in order, sticky on the last)."""

    def __init__(self, responses=None, evaluate=None):
        self.responses, self.fetched, self.visited = responses or {}, [], []
        self.page = FakePage(evaluate or (lambda s, a: None))

    def goto(self, path):
        self.visited.append(path)
        return self.page

    def ensure_on_site(self, path):
        return self.page

    def fetch(self, path):
        self.fetched.append(path)
        key = max((k for k in self.responses if path.startswith(k)), key=len)
        queue = self.responses[key]
        item = queue.pop(0) if len(queue) > 1 else queue[0]
        if isinstance(item, Exception):
            raise item
        return item

    def pause(self):
        pass

    def close(self):
        pass


def scraper(browser):
    s = ClubScraper("unused.json")
    s._browser = browser
    return s


def feed(entries, more):
    return json.dumps({"entries": entries, "pagination": {"hasMore": more}})


FEED = "/clubs/1/feed?feed_type=club&num_entries=100"


def test_activities_follow_the_cursor_and_dedupe():
    b = FakeBrowser({FEED: [feed([activity_entry(1, 10, "A One")], True),
                            feed([activity_entry(2, 11, "B Two"), activity_entry(1, 10, "A One")], False)]})
    acts = scraper(b).get_club_activities(1)
    assert [(a.id, a.athlete_id) for a in acts] == [(1, 10), (2, 11)]
    assert b.fetched[1] == f"{FEED}&before=100&cursor=r1"
    assert b.visited == ["/clubs/1/recent_activity"]


def test_activities_limit_stops_early():
    b = FakeBrowser({FEED: [feed([activity_entry(i, 10, "A One") for i in range(1, 6)], True)]})
    assert [a.id for a in scraper(b).get_club_activities(1, limit=3)] == [1, 2, 3]
    assert len(b.fetched) == 1


def test_never_ending_feed_stops_at_the_circuit_breaker():
    pages = [feed([activity_entry(i, 10, "A One")], True) for i in range(1, 60)]
    b = FakeBrowser({FEED: pages})
    assert len(scraper(b).get_club_activities(1)) == 50


@pytest.mark.parametrize("text,error", [("<html>blocked</html>", MarkupChanged),
                                        (feed([activity_entry(Distanz="5,2 km")], False), UnsupportedLocale)])
def test_bad_feed_pages_raise(text, error):
    with pytest.raises(error):
        scraper(FakeBrowser({FEED: [text]})).get_club_activities(1)


def test_fetch_errors_propagate():
    with pytest.raises(SessionExpired):
        scraper(FakeBrowser({FEED: [SessionExpired("x")]})).get_club_activities(1)


def count_page(count, admins, members):
    return f"<span class='membership-count'>{count:,} members</span>" + members_page(admins, members)


MEMBERS = "/clubs/1/members?page="


def test_roster_walks_pages_until_nobody_new():
    admins = [("1", "Alice Anon")]
    b = FakeBrowser({f"{MEMBERS}1": [count_page(3, admins, [("2", "Bob B")])],
                     f"{MEMBERS}2": [count_page(3, admins, [("3", "Cara C")])],
                     f"{MEMBERS}3": [count_page(3, admins, [])]})
    roster = scraper(b).get_club_roster(1)
    assert roster.member_count == 3 and roster.complete
    assert [(m.id, m.admin) for m in roster.members] == [(1, True), (2, False), (3, False)]
    assert [f for f in b.fetched] == [f"{MEMBERS}1", f"{MEMBERS}2", f"{MEMBERS}3"]


def test_roster_is_walked_again_when_it_does_not_tally():
    b = FakeBrowser({f"{MEMBERS}1": [count_page(3, [("1", "A")], [("2", "B")]),     # first walk: 2 of 3
                                     count_page(3, [("1", "A")], [("2", "B"), ("3", "C")])],
                     f"{MEMBERS}2": [count_page(3, [("1", "A")], [])]})
    roster = scraper(b).get_club_roster(1)
    assert roster.complete and len(roster.members) == 3


def test_members_limit_and_admins():
    b = FakeBrowser({f"{MEMBERS}1": [count_page(2, [("1", "Alice Anon")], [("2", "Bob B")])],
                     f"{MEMBERS}2": [count_page(2, [("1", "Alice Anon")], [])]})
    s = scraper(b)
    assert [m.id for m in s.get_club_members(1, limit=1)] == [1]
    assert [(a.id, a.firstname, a.lastname) for a in s.get_club_admins(1)] == [(1, "Alice", "Anon")]
    assert s.get_club_member_count(1) == 2


def test_admins_without_headings_is_markup_changed_not_empty():
    page = "<span class='membership-count'>1 member</span><ul class='list-athletes'>" \
           "<li><div class='text-headline'><a href=\"/athletes/1\">A</a></div></li></ul>"
    with pytest.raises(MarkupChanged):
        scraper(FakeBrowser({f"{MEMBERS}1": [page]})).get_club_admins(1)


def test_missing_member_count_is_markup_changed():
    with pytest.raises(MarkupChanged):
        scraper(FakeBrowser({f"{MEMBERS}1": ["<p>nothing</p>"]})).get_club_roster(1)


def test_athlete_weeks_keep_own_activities_and_flag_private_profiles():
    monday = date(2026, 9, 14)

    def evaluate(script, arg):
        out = []
        for url in arg["urls"]:
            if "/athletes/7/" in url:
                out.append({"entries": [activity_entry(1, 7, "Sam Q"), activity_entry(2, 8, "Other O")]})
            else:
                out.append({"entries": None})
        return out

    s = scraper(FakeBrowser(evaluate=evaluate))
    result = s.get_athlete_weeks([(7, monday), (8, date(2026, 9, 16))])   # any day -> its Monday
    assert not result.rate_limited and result.errors == 0
    w7, w8 = result.weeks
    assert (w7.athlete_id, w7.week_start, w7.visible, [a.id for a in w7.activities]) == (7, monday, True, [1])
    assert (w8.week_start, w8.visible, w8.activities) == (monday, False, [])


def test_athlete_weeks_stop_at_a_429_and_report_the_rest():
    urls_seen = []

    def evaluate(script, arg):
        urls_seen.extend(arg["urls"])
        return [{"entries": []}, {"limited": True}, None]

    s = scraper(FakeBrowser(evaluate=evaluate))
    jobs = [(i, date(2026, 9, 14)) for i in range(1, 6)]
    result = s.get_athlete_weeks(jobs, batch_size=3)
    assert result.rate_limited
    assert [w.athlete_id for w in result.weeks] == [1]
    assert [a for a, _ in result.unfetched] == [2, 3, 4, 5]
    assert len(urls_seen) == 3                                   # the second batch was never sent
    assert urls_seen[0].endswith(f"interval={week_id(date(2026, 9, 14))}&interval_type=week&chart_type=miles&year_offset=0")


def test_athlete_weeks_count_errors_and_raise_on_expiry():
    s = scraper(FakeBrowser(evaluate=lambda sc, a: [{"error": "HTTP 500"}]))
    assert s.get_athlete_weeks([(1, date(2026, 9, 14))]).errors == 1
    s = scraper(FakeBrowser(evaluate=lambda sc, a: [{"expired": True, "status": 401}]))
    with pytest.raises(SessionExpired):
        s.get_athlete_weeks([(1, date(2026, 9, 14))])
    s = scraper(FakeBrowser(evaluate=lambda sc, a: [{"limited": True}]))
    result = s.get_athlete_weeks([(1, date(2026, 9, 14))])
    assert result.rate_limited and result.unfetched == [(1, date(2026, 9, 14))]


def test_no_session_file_is_session_expired(tmp_path):
    with pytest.raises(SessionExpired):
        ClubScraper(tmp_path / "missing.json").get_club_activities(1)
