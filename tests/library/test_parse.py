"""openstride._parse: display text -> numbers (metric and imperial, English only), feed entries, roster, leaderboard."""
import pytest

from openstride import MarkupChanged, UnsupportedLocale
from openstride import _parse as P


@pytest.mark.parametrize("raw,text", [
    ("5.2 <abbr>km</abbr>", "5.2 km"),
    ("<p>line one<br />\nline two</p>", "line one\nline two"),
    ("Bob &amp; Co", "Bob & Co"),
    (None, ""),
    (123, "123"),
])
def test_strip_markup(raw, text):
    assert P.strip_markup(raw) == text


@pytest.mark.parametrize("raw,metres", [
    ("5.2 km", 5200.0),
    ("5.23 km", 5230.0),
    ("3 mi", 4828.0),
    ("3.1 mi", 4989.0),
    ("400 m", 400.0),
    ("394 ft", 120.1),          # imperial elevation: was silently dropped before
    ("1,234 ft", 376.1),
    ("100 yd", 91.4),
    ("1,234 km", 1234000.0),
    ("1,234.5 m", 1234.5),
    (".5 km", 500.0),
    ("5.2<abbr>km</abbr>", None),   # caller strips markup first; raw markup is not a length
    ("1h 5m", None),            # a duration is not a length
    ("no distance", None),
    ("", None),
    (None, None),
])
def test_to_meters(raw, metres):
    assert P.to_meters(raw) == metres


@pytest.mark.parametrize("raw,seconds", [
    ("1h 5m 3s", 3903),
    ("1h5m", 3900),
    ("45m", 2700),
    ("45m 10s", 2710),
    ("2h", 7200),
    ("25:30", 1530),            # mm:ss, as some cards show it
    ("1:02:03", 3723),          # h:mm:ss
    ("3.1 mi", None),           # "1 mi" must not read as one minute
    ("nope", None),
    (None, None),
])
def test_to_seconds(raw, seconds):
    assert P.to_seconds(raw) == seconds


@pytest.mark.parametrize("raw,value", [("12,345 steps", 12345), ("10", 10), ("", None), (None, None)])
def test_to_int(raw, value):
    assert P.to_int(raw) == value


def test_parse_stats_keys_by_label_not_position():
    stats = [
        {"key": "stat_one", "value": "5.2 <abbr>km</abbr>"},
        {"key": "stat_one_subtitle", "value": "Distance"},
        {"key": "stat_two", "value": "25:30"},
        {"key": "stat_two_subtitle", "value": "Time"},
    ]
    assert P.parse_stats(stats) == {"Distance": "5.2 km", "Time": "25:30"}


@pytest.mark.parametrize("stats", [None, [], [{"key": "stat_one", "value": "9"}]])
def test_parse_stats_drops_unlabelled(stats):
    assert P.parse_stats(stats) == {}


def test_check_english_accepts_english_and_empty():
    P.check_english([{"Distance": "5 km"}, {}])
    P.check_english([])


def test_check_english_rejects_a_translated_page():
    with pytest.raises(UnsupportedLocale):
        P.check_english([{"Distanz": "5,2 km", "Zeit": "25:30"}])


def _stats(**pairs):
    out = []
    for i, (label, value) in enumerate(pairs.items()):
        out += [{"key": f"stat_{i}", "value": value}, {"key": f"stat_{i}_subtitle", "value": label.replace("_", " ")}]
    return out


def activity_entry(aid=111, athlete=9, name="Sam Q", type_="Run", **stats):
    return {"entity": "Activity", "cursorData": {"updated_at": "100", "rank": "r1"}, "activity": {
        "id": aid, "athlete": {"athleteId": athlete, "athleteName": name, "firstName": name.split()[0]},
        "startDate": "2026-09-14T07:00:00Z", "activityName": "Morning Run", "type": type_,
        "elapsedTime": 2000, "deviceName": "Garmin", "isVirtual": False, "isCommute": "false",
        "visibility": "everyone", "description": "<p>easy</p>",
        "stats": _stats(**(stats or {"Distance": "5 <abbr>km</abbr>", "Time": "25m", "Elev_Gain": "12 m"})),
        "kudosAndComments": {"kudosCount": 4, "comments": [{}, {}]},
    }}


def test_activity_entry():
    (row,) = P.feed_entry_activities(activity_entry())
    assert row["id"] == 111 and row["athlete_id"] == 9
    assert row["athlete_name"] == "Sam Q" and row["athlete_firstname"] == "Sam"
    assert row["start_date"] == "2026-09-14T07:00:00Z" and row["sport_type"] == "Run"
    assert (row["distance"], row["moving_time"], row["elapsed_time"], row["total_elevation_gain"]) == \
        (5000.0, 1500, 2000, 12.0)
    assert row["kudos_count"] == 4 and row["comment_count"] == 2
    assert row["is_virtual"] is False and row["is_commute"] is False and row["description"] == "easy"


def test_imperial_activity_entry():
    (row,) = P.feed_entry_activities(activity_entry(Distance="3.1 mi", Time="30m 5s", Elev_Gain="394 ft"))
    assert (row["distance"], row["moving_time"], row["total_elevation_gain"]) == (4989.0, 1805, 120.1)


def test_group_activity_fans_out_one_row_per_athlete():
    entry = {"entity": "GroupActivity", "rowData": {"activities": [
        {"activity_id": 1, "athlete_id": 10, "athlete_name": "A", "start_date": "2026-09-14T01:00:00Z",
         "start_date_local": "2026-09-14T09:00:00Z", "name": "Run A", "type": "TrailRun", "elapsed_time": 100,
         "num_comments": 3, "stats": _stats(Distance="1 km")},
        {"activity_id": 2, "athlete_id": 11, "athlete_name": "B", "start_date": "2026-09-14T02:00:00Z",
         "name": "Run B", "elapsed_time": 200, "num_comments": 0, "stats": []},
    ]}}
    rows = P.feed_entry_activities(entry)
    assert [(r["id"], r["athlete_id"], r["comment_count"]) for r in rows] == [(1, 10, 3), (2, 11, 0)]
    assert rows[0]["distance"] == 1000.0 and rows[0]["start_date_local"] == "2026-09-14T09:00:00Z"
    assert all(r["entity"] == "GroupActivity" for r in rows)


@pytest.mark.parametrize("entry", [{"entity": "Club"}, {}, {"entity": "GroupActivity"}])
def test_other_entries_have_no_activities(entry):
    assert P.feed_entry_activities(entry) == []


def test_feed_page_drops_rows_without_ids_and_checks_language():
    assert P.feed_page([{"entity": "Activity"}, activity_entry()])[0]["id"] == 111
    with pytest.raises(UnsupportedLocale):
        P.feed_page([activity_entry(Distanz="5,2 km")])


# --- members page ---------------------------------------------------------------------------------------------

def test_member_count():
    assert P.parse_member_count("<span class='membership-count'>\n1055 members\n</span>") == 1055
    assert P.parse_member_count('<span class="membership-count">1,055 members</span>') == 1055
    assert P.parse_member_count("<span class='membership-count'>1 member</span>") == 1
    assert P.parse_member_count("<h3>1055 members</h3>") is None
    assert P.parse_member_count("") is None


def _li(aid, name):
    return (f"<li><div class='avatar'></div><div class='text-headline'><a href=\"/athletes/{aid}\">{name}</a></div>"
            f"<div class='location'>Singapore</div></li>")


def members_page(admins, members, headings=True):
    h_admin, h_member = ("<h2>Admins</h2>", "<h2>Members</h2>") if headings else ("", "")
    return ("<nav><a class='nav-link' href='/athletes/999'>Me</a></nav>"
            f"{h_admin}<ul class='list-athletes'>" + "".join(_li(*a) for a in admins) + "</ul>"
            f"{h_member}<ul class=\"list-athletes\">" + "".join(_li(*m) for m in members) + "</ul>"
            "<div class='text-headline'><a href=\"/athletes/888\">Sidebar</a></div>")


def test_roster_marks_admins_and_ignores_rows_outside_the_lists():
    html = members_page([("1", "Alice Anon")], [("2", "Bob &amp; Co"), ("3", " Cara ")])
    assert P.parse_roster(html) == [(1, "Alice Anon", True), (2, "Bob & Co", False), (3, "Cara", False)]


def test_roster_without_headings_does_not_guess_admins():
    assert P.parse_roster(members_page([("1", "A")], [("2", "B")], headings=False)) == [(1, "A", None), (2, "B", None)]


def test_roster_of_a_blocked_page_is_empty():
    assert P.parse_roster("<p>blocked</p>") == []


@pytest.mark.parametrize("name,parts", [("Ada Lovelace King", ("Ada", "Lovelace King")), ("Cher", ("Cher", None)),
                                        ("", (None, None))])
def test_split_name(name, parts):
    assert P.split_name(name) == parts


# --- leaderboard ----------------------------------------------------------------------------------------------

def test_leaderboard_rows():
    rows = [{"id": "2779948", "name": "Jo Example", "rank": "1", "dist": "94.6 km", "acts": "10", "elev": "1,254 m",
             "time": "8h 34m"},
            {"id": "5", "name": "Imperial Ian", "rank": "2", "dist": "10.0 mi", "acts": "2", "elev": "--",
             "time": "1h 30m"}]
    assert P.leaderboard_rows(rows) == [
        {"rank": 1, "athlete_id": 2779948, "athlete_name": "Jo Example", "distance": 94600.0, "activities": 10,
         "total_elevation_gain": 1254.0, "moving_time": 30840},
        {"rank": 2, "athlete_id": 5, "athlete_name": "Imperial Ian", "distance": 16093.4, "activities": 2,
         "total_elevation_gain": 0.0, "moving_time": 5400}]


def test_unreadable_leaderboard_row_is_markup_changed():
    with pytest.raises(MarkupChanged):
        P.leaderboard_rows([{"name": "no id"}])
