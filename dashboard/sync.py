"""One sync: read the club with openstride, update the ledgers in the data directory.

Light sync (every OPENSTRIDE_SYNC_MINUTES):
    members      roster + headline count        -> members.csv, member_count.csv
    feed         the club feed (~26 h kept)     -> activities.csv (any sport)
    leaderboard  this and last week (~top 100)  -> statistics.csv
Profile scan (nightly at OPENSTRIDE_SCAN_HOUR, or `scan=True`): every current member's last two profile weeks, every
week since challenge_start for members added since the last scan (setup=True: everyone) -> activities.csv gaps and
statistics.csv.

statistics.csv holds each athlete's cumulative totals since challenge_start, one row per athlete per day, from the
best source available: the leaderboard (Strava's own weekly figure), else the profile week's activities, else the
feed ledger (the athlete's last row plus ledger activities it doesn't cover yet). A week's figures go on top of the
athlete's last row before that week, so past weeks are never re-fetched.
"""
from __future__ import annotations

from collections import defaultdict
from datetime import date, datetime, timedelta, timezone

from openstride import ClubActivity, ClubRoster, ClubScraper, LeaderboardEntry, OpenStrideError, monday_of

from .config import Settings
from .store import (ACTIVITY_FIELDS, COUNT_FIELDS, FIGURES, MEMBER_FIELDS, STAT_FIELDS, Store, append_rows,
                    local_date, now_utc, read_csv, write_csv)


class SyncIncomplete(OpenStrideError):
    """The profile scan stopped short (rate limit, errors). What it found is saved."""


def _cell(value) -> str:
    return "" if value is None else str(value)


def activity_row(a: ClubActivity) -> dict:
    """A openstride ClubActivity as an activities.csv row."""
    start = a.start_date.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ") if a.start_date else ""
    local = a.start_date_local.strftime("%Y-%m-%dT%H:%M:%SZ") if a.start_date_local else ""
    return {k: _cell(v) for k, v in {
        "activity_id": a.id, "athlete_id": a.athlete_id,
        "athlete_name": a.athlete.name if a.athlete else None,
        "athlete_firstname": a.athlete.firstname if a.athlete else None,
        "start_date_utc": start, "start_date_local": local, "activity_name": a.name, "type": a.raw_type,
        "distance_m": a.distance, "moving_time_s": a.moving_time, "elapsed_time_s": a.elapsed_time,
        "pace": a.pace, "elev_gain_m": a.total_elevation_gain, "steps": a.steps, "device_name": a.device_name,
        "workout_type": a.workout_type, "is_virtual": a.is_virtual, "is_commute": a.is_commute,
        "visibility": a.visibility, "location": a.location, "description": a.description,
        "kudos_count": a.kudos_count, "comment_count": a.comment_count, "entity": a.entity,
    }.items()}


def append_activities(store: Store, rows: list[dict], stamp: str | None = None) -> list[dict]:
    """Append rows whose activity_id isn't in activities.csv yet, stamped scraped_at; the rows appended."""
    seen = {r["activity_id"] for r in read_csv(store.activities)}
    now = stamp or now_utc()
    new = []
    for r in rows:
        if r["activity_id"] not in seen:
            seen.add(r["activity_id"])
            new.append({**r, "scraped_at": now})
    append_rows(store.activities, ACTIVITY_FIELDS, new)
    return new


def update_members(store: Store, roster: ClubRoster, stamp: str) -> list[str]:
    """Upsert members.csv from the roster; the newcomers' ids. Only a roster that tallies with the headline
    count marks absent members as left (and clears left_at for returning ones)."""
    rows = {r["athlete_id"]: r for r in read_csv(store.members)}
    new = []
    for m in roster.members:
        aid = str(m.id)
        if aid not in rows:
            rows[aid] = {"athlete_id": aid, "name": m.name, "admin": _cell(m.admin), "ingest_at": stamp,
                         "left_at": ""}
            new.append(aid)
        else:
            rows[aid]["name"] = m.name    # follow renames
            rows[aid]["admin"] = _cell(m.admin)
            if roster.complete:
                rows[aid]["left_at"] = ""
    if roster.complete:
        present = {str(m.id) for m in roster.members}
        for aid, r in rows.items():
            if aid not in present and not r.get("left_at"):
                r["left_at"] = stamp
    write_csv(store.members, MEMBER_FIELDS, list(rows.values()))
    return new


# --- statistics ---------------------------------------------------------------------------------------------

def week_totals(rows: list[dict]) -> dict:
    """Sum activity rows into FIGURES."""
    return {"distance_m": round(sum(float(r["distance_m"] or 0) for r in rows), 1),
            "moving_time_s": sum(int(float(r["moving_time_s"] or 0)) for r in rows),
            "elev_gain_m": round(sum(float(r["elev_gain_m"] or 0) for r in rows), 1),
            "activities": len(rows)}


def _add(base: dict, figs: dict) -> dict:
    out = {}
    for k in FIGURES:
        total = float(base.get(k) or 0) + float(figs[k])
        out[k] = round(total, 1) if k.endswith("_m") else int(total)
    return out


def _by_athlete(daily: dict) -> dict:
    by = defaultdict(dict)
    for (aid, day), r in daily.items():
        by[aid][day] = r
    return by


def snapshot_date(monday: str, today: date) -> str:
    """The day a week's snapshot stands for: its Sunday once over, else today."""
    return min(date.fromisoformat(monday) + timedelta(days=6), today).isoformat()


def leaderboard_figures(entries: list[LeaderboardEntry]) -> dict:
    """{monday_iso: {athlete_id: FIGURES}} from leaderboard entries."""
    out: dict = defaultdict(dict)
    for e in entries:
        out[e.week_start.isoformat()][str(e.athlete_id)] = {
            "distance_m": e.distance, "moving_time_s": e.moving_time, "elev_gain_m": e.total_elevation_gain,
            "activities": e.activities}
    return dict(out)


def cumulate(daily: dict, weeks: dict, leaderboard: dict, synced_at: str, today: date) -> dict:
    """Upsert {(athlete_id, date): row} with each week's figures added to the athlete's last row before that week.

    weeks: {(athlete_id, monday_iso): counted activity rows} from the profile scan.
    leaderboard: {monday_iso: {athlete_id: FIGURES}}; wins over the profile sum.
    """
    figs = {k: {**week_totals(rows), "source": "profile"} for k, rows in weeks.items()}
    for monday, board in leaderboard.items():
        for aid, fig in board.items():
            figs[(aid, monday)] = {**{k: fig[k] for k in FIGURES}, "source": "leaderboard"}

    by_athlete = _by_athlete(daily)
    for aid, monday in sorted(figs, key=lambda k: k[1]):   # oldest week first: it is the next week's base
        f = figs[(aid, monday)]
        if not (float(f["activities"]) or float(f["distance_m"])):
            continue   # nothing seen this week: no row, so feed-only athletes (private profiles) keep counting
        mine = by_athlete[aid]
        prior = [d for d in mine if d < monday]
        base = mine[max(prior)] if prior else {}
        day = snapshot_date(monday, today)
        daily[(aid, day)] = mine[day] = {"athlete_id": aid, "date": day, **_add(base, f),
                                         "source": f["source"], "synced_at": synced_at}
    return daily


def fallback(daily: dict, acts: list[dict], synced_at: str, today: date, s: Settings) -> dict:
    """Athletes the leaderboard and profiles didn't just cover: last row + ledger activities it doesn't include
    (dated after that row, or scraped after it was synced: late uploads). Upserted as today's "feed" row."""
    by_athlete = _by_athlete(daily)
    pending = defaultdict(list)
    for a in acts:
        d = local_date(a.get("start_date_utc"), s.tz)
        if a.get("type") not in s.sports or not d or d < s.challenge_start:
            continue
        mine = by_athlete.get(a["athlete_id"], {})
        last = mine[max(mine)] if mine else {"date": "", "synced_at": ""}
        if d > last["date"] or (a.get("scraped_at") or "") > (last["synced_at"] or ""):
            pending[a["athlete_id"]].append(a)
    day = today.isoformat()
    for aid, new in pending.items():
        mine = by_athlete[aid]
        base = mine[max(mine)] if mine else {}
        daily[(aid, day)] = {"athlete_id": aid, "date": day, **_add(base, week_totals(new)),
                             "source": "feed", "synced_at": synced_at}
    return daily


def update_statistics(store: Store, s: Settings, weeks: dict, leaderboard: dict, synced_at: str,
                      today: date) -> None:
    daily = cumulate({(r["athlete_id"], r["date"]): r for r in read_csv(store.statistics)},
                     weeks, leaderboard, synced_at, today)
    daily = fallback(daily, read_csv(store.activities), synced_at, today, s)
    write_csv(store.statistics, STAT_FIELDS, sorted(daily.values(), key=lambda r: (r["date"], r["athlete_id"])))


def last_profile_sync(store: Store) -> str:
    """When the last profile scan synced (max synced_at of its rows), '' if never."""
    return max((r["synced_at"] for r in read_csv(store.statistics) if r["source"] == "profile"), default="")


def weeks_to_sync(today: date, challenge_start: str, full: bool) -> list[date]:
    """Mondays to refresh: every week since challenge_start (full), else last week and this week, so runs
    uploaded late into the week just closed still land."""
    this = monday_of(today)
    if full:
        first = monday_of(date.fromisoformat(challenge_start))
        return [first + timedelta(weeks=i) for i in range((this - first).days // 7 + 1)]
    return [this - timedelta(weeks=1), this]


def jobs_for(members: list[dict], last_scan: str, today: date, setup: bool, challenge_start: str) -> list:
    """(athlete_id, monday) pairs: current members' last two weeks; every week since challenge_start for members
    added after the last scan (or everyone with setup)."""
    recent = weeks_to_sync(today, challenge_start, False)
    every = weeks_to_sync(today, challenge_start, True)
    jobs = []
    for m in members:
        if m.get("left_at"):
            continue
        weeks = every if setup or (m.get("ingest_at") or "") > last_scan else recent
        jobs += [(int(m["athlete_id"]), w) for w in weeks]
    return sorted(jobs, key=lambda j: (j[1], j[0]))


def run_sync(s: Settings, *, scan: bool = False, setup: bool = False, scraper_factory=ClubScraper) -> dict:
    """One sync; a summary dict. Raises OpenStrideError on the first failure (SyncIncomplete after saving a partial
    profile scan)."""
    store = Store(s.data_dir)
    today = datetime.now(s.tz).date()
    summary: dict = {}
    problem = None
    with scraper_factory(s.session_file, headless=s.headless, browser_channel=s.browser_channel or None,
                         timezone=s.timezone) as sc:
        roster = sc.get_club_roster(s.club_id)
        stamp = now_utc()
        append_rows(store.member_count, COUNT_FIELDS, [{"scraped_at": stamp, "member_count": roster.member_count}])
        summary["new_members"] = len(update_members(store, roster, stamp))
        summary["member_count"] = roster.member_count
        summary["roster_complete"] = roster.complete

        feed = [activity_row(a) for a in sc.get_club_activities(s.club_id)]
        summary["feed_new"] = len(append_activities(store, feed))   # before synced_at: older than any statistic

        weeks, synced_at = {}, now_utc()
        if scan or setup:
            jobs = jobs_for(read_csv(store.members), last_profile_sync(store), today, setup, s.challenge_start)
            result = sc.get_athlete_weeks(jobs)
            ledger = []
            for w in result.weeks:
                rows = [activity_row(a) for a in w.activities]
                ledger += rows
                weeks[(str(w.athlete_id), w.week_start.isoformat())] = [r for r in rows if r["type"] in s.sports]
            summary["scan_requests"] = len(jobs)
            summary["scan_new"] = len(append_activities(store, ledger, synced_at))
            if result.rate_limited:
                problem = (f"Strava rate-limited (HTTP 429) after {len(result.weeks)} of {len(jobs)} profile "
                           "requests; what was fetched is saved, the next scan carries on.")
            elif result.errors > 0.02 * len(jobs):
                problem = f"{result.errors} of {len(jobs)} profile requests failed; run the scan again."

        board = leaderboard_figures(sc.get_club_leaderboard(s.club_id, "this") +
                                    sc.get_club_leaderboard(s.club_id, "last"))
        summary["leaderboard_rows"] = sum(map(len, board.values()))
        update_statistics(store, s, weeks, board, synced_at, today)
    if problem:
        raise SyncIncomplete(problem)
    return summary
