"""dashboard.sync: ledgers, member upserts, the statistics cumulation, and one whole sync over a fake scraper."""
from datetime import date

import pytest

from dashboard import sync as S
from dashboard.api import row_to_activity
from dashboard.config import from_env
from dashboard.store import Store, read_csv
from openstride import AthleteWeek, AthleteWeeks, ClubAthlete, ClubRoster

from .conftest import FakeScraper, activity, board_entry


def test_activity_row_round_trips_through_the_api_model():
    a = activity(7, 10, "Alice Anon", "TrailRun", km=5.23, elev=120.1)
    row = S.activity_row(a)
    assert row["activity_id"] == "7" and row["type"] == "TrailRun" and row["start_date_utc"] == "2026-09-15T00:00:00Z"
    assert row["distance_m"] == "5230.0" and row["is_virtual"] == "False" and row["steps"] == ""
    back = row_to_activity(row)
    assert back.model_dump() == a.model_dump()


def test_append_activities_is_deduped_and_stamped(settings):
    store = Store(settings.data_dir)
    rows = [S.activity_row(activity(1, 10)), S.activity_row(activity(2, 11))]
    assert len(S.append_activities(store, rows[:1], "T1")) == 1
    assert len(S.append_activities(store, rows, "T2")) == 1
    assert [(r["activity_id"], r["scraped_at"]) for r in read_csv(store.activities)] == [("1", "T1"), ("2", "T2")]
    assert store.activities.read_text(encoding="utf-8").count("activity_id,") == 1


def roster(count, *people):
    return ClubRoster(member_count=count, members=[ClubAthlete.from_roster(a, n, adm) for a, n, adm in people])


def test_members_follow_a_tallied_roster_only(settings):
    store = Store(settings.data_dir)
    assert S.update_members(store, roster(2, (1, "A", True), (2, "B", False)), "T1") == ["1", "2"]
    S.update_members(store, roster(2, (1, "A", True), (3, "C", False)), "T2")       # 2 left, 3 joined (tallied)
    rows = {r["athlete_id"]: r for r in read_csv(store.members)}
    assert rows["2"]["left_at"] == "T2" and rows["3"]["ingest_at"] == "T2" and rows["1"]["admin"] == "True"
    S.update_members(store, roster(3, (1, "A2", True), (3, "C", False)), "T3")     # 2 of 3: not tallied
    rows = {r["athlete_id"]: r for r in read_csv(store.members)}
    assert rows["1"]["name"] == "A2" and rows["2"]["left_at"] == "T2"              # nobody marked by an untallied walk
    S.update_members(store, roster(3, (1, "A", True), (2, "B", False), (3, "C", False)), "T4")
    assert {r["athlete_id"]: r for r in read_csv(store.members)}["2"]["left_at"] == ""   # rejoined


def _run(km):
    return {"distance_m": km * 1000, "moving_time_s": 600, "elev_gain_m": 1.0}


def test_cumulate_adds_each_week_to_the_last_row_before_it():
    old = {("1", "2026-09-20"): {"athlete_id": "1", "date": "2026-09-20", "distance_m": "9000.0",
                                 "moving_time_s": "3000", "elev_gain_m": "0", "activities": "2"},
           ("1", "2026-09-25"): {"athlete_id": "1", "date": "2026-09-25", "distance_m": "12000.0",
                                 "moving_time_s": "4000", "elev_gain_m": "0", "activities": "3"}}
    weeks = {("1", "2026-09-21"): [_run(4), _run(1)], ("1", "2026-09-28"): [_run(2)], ("2", "2026-09-28"): [_run(3)],
             ("3", "2026-09-28"): []}
    board = {"2026-09-28": {"2": {"distance_m": 7000.0, "moving_time_s": 2400, "elev_gain_m": 5.0, "activities": 2}}}
    daily = S.cumulate(old, weeks, board, "T", date(2026, 9, 28))
    assert daily[("1", "2026-09-27")]["distance_m"] == 14000.0 and daily[("1", "2026-09-27")]["activities"] == 4
    assert daily[("1", "2026-09-28")] == {"athlete_id": "1", "date": "2026-09-28", "source": "profile",
                                          "synced_at": "T", "distance_m": 16000.0, "moving_time_s": 4800,
                                          "elev_gain_m": 3.0, "activities": 5}
    assert daily[("2", "2026-09-28")]["distance_m"] == 7000.0 and daily[("2", "2026-09-28")]["source"] == "leaderboard"
    assert ("3", "2026-09-28") not in daily
    assert daily[("1", "2026-09-25")]["distance_m"] == "12000.0"


def test_fallback_counts_only_the_sports_from_the_start(settings):
    def act(aid, type_="Run", start="2026-09-20T01:00:00Z"):
        return {"athlete_id": aid, "type": type_, "start_date_utc": start, "scraped_at": "2026-09-20T05:00:00+00:00",
                "distance_m": "5000", "moving_time_s": "1500", "elev_gain_m": "10"}
    daily = S.fallback({}, [act("1", "Ride"), act("1", start="2026-09-10T01:00:00Z"), act("2")], "T",
                       date(2026, 9, 21), settings)
    assert list(daily) == [("2", "2026-09-21")] and daily[("2", "2026-09-21")]["source"] == "feed"


@pytest.mark.parametrize("today,full,mondays", [
    (date(2026, 10, 1), False, ["2026-09-21", "2026-09-28"]),
    (date(2026, 10, 5), False, ["2026-09-28", "2026-10-05"]),
    (date(2026, 10, 1), True, ["2026-09-14", "2026-09-21", "2026-09-28"]),
])
def test_weeks_to_sync(today, full, mondays):
    assert [m.isoformat() for m in S.weeks_to_sync(today, "2026-09-14", full)] == mondays


def test_jobs_scan_new_members_from_the_start_and_skip_leavers():
    members = [{"athlete_id": "1", "ingest_at": "2026-09-01T00:00:00+00:00", "left_at": ""},
               {"athlete_id": "2", "ingest_at": "2026-09-30T00:00:00+00:00", "left_at": ""},
               {"athlete_id": "3", "ingest_at": "2026-09-01T00:00:00+00:00", "left_at": "2026-09-20T00:00:00+00:00"}]
    jobs = S.jobs_for(members, "2026-09-29T00:00:00+00:00", date(2026, 10, 1), False, "2026-09-14")
    assert sorted({a for a, _ in jobs}) == [1, 2]
    assert len([j for j in jobs if j[0] == 2]) == 3 and len([j for j in jobs if j[0] == 1]) == 2


def test_run_sync_writes_every_ledger(settings):
    fake = FakeScraper(boards={"this": [board_entry(10, 8.0)], "last": []})
    summary = S.run_sync(settings, scraper_factory=fake)
    store = Store(settings.data_dir)
    assert summary["member_count"] == 2 and summary["feed_new"] == 2
    assert [r["athlete_id"] for r in read_csv(store.members)] == ["10", "11"]
    assert [r["member_count"] for r in read_csv(store.member_count)] == ["2"]
    stats = read_csv(store.statistics)
    assert [(r["athlete_id"], r["source"]) for r in stats] == [("10", "leaderboard")]   # Bob only rode: not counted
    assert fake.week_requests is None                                                  # no scan unless asked


def test_scan_rate_limited_saves_what_it_found_then_raises(settings):
    monday = date(2026, 9, 14)
    weeks = AthleteWeeks(weeks=[AthleteWeek(athlete_id=10, week_start=monday, visible=True,
                                            activities=[activity(5, 10)])],
                         rate_limited=True, unfetched=[(11, monday)])
    fake = FakeScraper(feed=[], weeks=weeks)
    with pytest.raises(S.SyncIncomplete, match="rate-limited"):
        S.run_sync(settings, scan=True, scraper_factory=fake)
    store = Store(settings.data_dir)
    assert [r["activity_id"] for r in read_csv(store.activities)] == ["5"]
    assert [r["source"] for r in read_csv(store.statistics)] == ["profile"]


def test_existing_csvs_from_the_old_pipeline_still_load(settings):
    """members.csv written before the admin column existed is read and rewritten without loss."""
    store = Store(settings.data_dir)
    store.members.write_text("athlete_id,name,ingest_at,left_at\n10,Alice Anon,2026-09-09T11:18:06+00:00,\n",
                             encoding="utf-8")
    S.update_members(store, roster(1, (10, "Alice Anon", True)), "T")
    (row,) = read_csv(store.members)
    assert row == {"athlete_id": "10", "name": "Alice Anon", "admin": "True", "ingest_at": "2026-09-09T11:18:06+00:00",
                   "left_at": ""}


def test_config_requires_club_and_start():
    with pytest.raises(ValueError, match="OPENSTRIDE_CLUB_ID"):
        from_env({"OPENSTRIDE_CHALLENGE_START": "2026-09-14"})
    with pytest.raises(ValueError, match="OPENSTRIDE_CHALLENGE_START"):
        from_env({"OPENSTRIDE_CLUB_ID": "1", "OPENSTRIDE_CHALLENGE_START": "14/09/2026"})
    s = from_env({"OPENSTRIDE_CLUB_ID": "1", "OPENSTRIDE_CHALLENGE_START": "2026-09-14", "OPENSTRIDE_SCAN_HOUR": "off"})
    assert s.scan_hour is None and s.sports == {"Run", "TrailRun", "VirtualRun", "Walk", "Hike"}
    assert s.session_file.name == "auth_state.json" and s.sync_minutes == 60 and s.api_token == ""
