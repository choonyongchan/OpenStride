"""What the dashboard shows, computed from the ledgers: totals, awards, a leaderboard keyed by athlete_id (so two
athletes with the same name stay two rows), history by day and a trend.

Totals come from statistics.csv (cumulative per athlete); the activity ledger only feeds the per-activity awards
(longest, fastest, climber, rest time) and devices.
"""
from __future__ import annotations

from collections import Counter
from dataclasses import asdict, dataclass, field
from datetime import datetime

from .config import Settings
from .store import Store, local_date, read_csv


def _num(value) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def fmt_time(seconds: float) -> str:
    h, rem = divmod(int(seconds), 3600)
    return f"{h}h {rem // 60}m"


def fmt_kmh(ms: float) -> str:
    return f"{ms * 3.6:.1f} km/h"


@dataclass
class Athlete:
    athlete_id: str
    name: str
    km: float = 0.0
    elev: float = 0.0
    time_s: float = 0.0
    acts: int = 0
    speeds: list = field(default_factory=list)   # m/s of activities over 0.5 km
    longest: float = 0.0                          # km
    devices: set = field(default_factory=set)
    hill_elev: float = 0.0                        # from "real hill" activities (>= 5 km, >= 8 m+/km)
    hill_km: float = 0.0
    rest_s: float = 0.0                           # elapsed minus moving time

    def add_snapshot(self, row: dict) -> None:
        self.km += _num(row.get("distance_m")) / 1000
        self.elev += _num(row.get("elev_gain_m"))
        self.time_s += _num(row.get("moving_time_s"))
        self.acts += int(_num(row.get("activities")))

    def add_activity(self, act: dict) -> None:
        dist_m, elev = _num(act.get("distance_m")), _num(act.get("elev_gain_m"))
        moving, elapsed = _num(act.get("moving_time_s")), _num(act.get("elapsed_time_s"))
        km = dist_m / 1000
        self.longest = max(self.longest, km)
        if moving > 0 and km > 0.5:
            self.speeds.append(dist_m / moving)
        if act.get("device_name"):
            self.devices.add(act["device_name"])
        if km >= 5 and elev / km >= 8:
            self.hill_elev += elev
            self.hill_km += km
        self.rest_s += max(0.0, elapsed - moving)

    @property
    def avg_speed(self) -> float | None:
        return sum(self.speeds) / len(self.speeds) if self.speeds else None

    def row(self, leader_km: float) -> dict:
        gap, speed = leader_km - self.km, self.avg_speed
        return {
            "athlete_id": self.athlete_id, "name": self.name,
            "km": round(self.km, 1), "elev": round(self.elev), "time": fmt_time(self.time_s),
            "time_s": int(self.time_s), "acts": self.acts,
            "avg_speed": fmt_kmh(speed) if speed is not None else "–",
            "avg_speed_ms": round(speed, 4) if speed is not None else 0,
            "longest": round(self.longest, 1),
            "gap": round(gap, 1) if gap > 0 else 0,
            "elev_per_km": round(self.elev / self.km, 1) if self.km > 0 else None,
        }


@dataclass
class Report:
    date: str = ""
    total_km: float = 0.0
    total_elev: float = 0.0
    activity_count: int = 0
    active_count: int = 0          # athletes with at least one counted activity
    member_count: int = 0          # Strava's headline count as of the date, else members on the roster
    leaderboard: list = field(default_factory=list)
    awards: dict = field(default_factory=dict)
    devices: list = field(default_factory=list)


def _award(values: dict, names: dict, fmt, pick=max) -> dict | None:
    if not values:
        return None
    aid = pick(values, key=values.get)
    return {"athlete_id": aid, "name": names[aid], "value": fmt(values[aid])}


def _awards(athletes: dict) -> dict:
    names = {a.athlete_id: a.name for a in athletes.values()}
    active = [a for a in athletes.values() if a.acts > 0]
    climber = {a.athlete_id: a.hill_elev / a.hill_km for a in athletes.values()
               if a.hill_km >= 30 and a.hill_elev / a.hill_km > 5}
    rest = {a.athlete_id: a.rest_s for a in athletes.values() if a.rest_s > 60}
    return {
        "distance": _award({a.athlete_id: a.km for a in active}, names, lambda v: f"{v:.1f} km"),
        "elevation": _award({a.athlete_id: a.elev for a in active}, names, lambda v: f"{v:,.0f} m"),
        "time": _award({a.athlete_id: a.time_s for a in active}, names, fmt_time),
        "fastest": _award({a.athlete_id: a.avg_speed for a in athletes.values() if a.avg_speed is not None},
                          names, fmt_kmh),
        "longest": _award({a.athlete_id: a.longest for a in active if a.longest > 0}, names,
                          lambda v: f"{v:.1f} km"),
        "climber": _award(climber, names, lambda v: f"{v:.1f} m+/km"),
        "flattest": _award({a.athlete_id: a.elev / a.km for a in athletes.values() if a.km >= 50}, names,
                           lambda v: f"{v:.1f} m+/km", pick=min),
        "rest": _award(rest, names, lambda v: f"{int(v // 60)} min stopped"),
    }


def compute(snapshots: list, acts: list, members: list, names: dict) -> Report:
    """One period: totals from cumulative snapshot rows, awards from ledger activities, a zero row per member who
    has not been active. names: {athlete_id: display name}."""
    athletes: dict[str, Athlete] = {}

    def athlete(aid: str) -> Athlete:
        if aid not in athletes:
            athletes[aid] = Athlete(aid, names.get(aid) or f"Athlete {aid}")
        return athletes[aid]

    for r in snapshots:
        athlete(r["athlete_id"]).add_snapshot(r)
    for a in acts:
        athlete(a["athlete_id"]).add_activity(a)
    ranked = sorted(athletes.values(), key=lambda a: (-a.km, a.name))
    leader = ranked[0].km if ranked else 0.0
    board = [a.row(leader) for a in ranked]
    board += [Athlete(m["athlete_id"], names.get(m["athlete_id"]) or m["athlete_id"]).row(leader)
              for m in sorted(members, key=lambda m: m.get("name", "")) if m["athlete_id"] not in athletes]
    devices = Counter(d for a in athletes.values() for d in a.devices)
    return Report(
        total_km=round(sum(a.km for a in athletes.values()), 1),
        total_elev=round(sum(a.elev for a in athletes.values())),
        activity_count=sum(a.acts for a in athletes.values()),
        active_count=sum(1 for a in athletes.values() if a.acts > 0),
        member_count=len(members),
        leaderboard=board,
        awards=_awards(athletes),
        devices=[{"device": d, "count": c} for d, c in devices.most_common()],
    )


def latest_by_athlete(snapshots: list, day: str) -> list:
    """Per athlete, the latest snapshot dated on/before day: their cumulative totals as of then."""
    latest: dict = {}
    for r in snapshots:
        if r["date"] <= day and r["date"] > latest.get(r["athlete_id"], {}).get("date", ""):
            latest[r["athlete_id"]] = r
    return list(latest.values())


def member_on(m: dict, day: str) -> bool:
    """Whether a members.csv row was in the club on day: ingested by then and not left by then."""
    left = (m.get("left_at") or "")[:10]
    return (m.get("ingest_at") or "")[:10] <= day and not (left and left <= day)


class Dataset:
    """The ledgers loaded once (reloaded when a file changes), and the views the API serves."""

    def __init__(self, settings: Settings):
        self.s = settings
        self.store = Store(settings.data_dir)
        self._version = None

    def _load(self) -> None:
        version = self.store.version()
        if version == self._version:
            return
        tz = self.s.tz
        acts = []
        for r in read_csv(self.store.activities):
            d = local_date(r.get("start_date_utc"), tz)
            if d and d >= self.s.challenge_start and r.get("type") in self.s.sports:
                acts.append({**r, "_date": d})
        self.acts = acts
        self.snapshots = read_csv(self.store.statistics)
        self.members = read_csv(self.store.members)
        counts = sorted(read_csv(self.store.member_count), key=lambda r: r["scraped_at"])
        self.counts = {local_date(r["scraped_at"], tz): int(r["member_count"]) for r in counts}
        self.names = {a["athlete_id"]: a["athlete_name"] for a in self.acts if a.get("athlete_name")}
        self.names.update({m["athlete_id"]: m["name"] for m in self.members if m.get("name")})
        self.dates = sorted({r["date"] for r in self.snapshots})
        self._version = version

    def today(self) -> str:
        return datetime.now(self.s.tz).date().isoformat()

    def count_as_of(self, day: str) -> int | None:
        known = [d for d in self.counts if d <= day]
        return self.counts[max(known)] if known else None

    def report(self, day: str | None = None) -> dict:
        """The dashboard as of the end of `day` (default today)."""
        self._load()
        day = day or self.today()
        so_far = latest_by_athlete(self.snapshots, day)
        ran = {r["athlete_id"] for r in so_far}
        members = [m for m in self.members if member_on(m, day) or m["athlete_id"] in ran]
        rep = compute(so_far, [a for a in self.acts if a["_date"] <= day], members, self.names)
        rep.date = day
        rep.member_count = self.count_as_of(day) or rep.member_count
        return asdict(rep)

    def history_dates(self) -> list[str]:
        self._load()
        return sorted(set(self.dates) | {self.today()})

    def trend(self) -> list[dict]:
        """Per day with a snapshot (and today): the club's cumulative totals."""
        self._load()
        out, latest = [], {}
        by_date: dict = {}
        for r in self.snapshots:
            by_date.setdefault(r["date"], []).append(r)
        for day in self.history_dates():
            for r in by_date.get(day, []):
                latest[r["athlete_id"]] = r
            rows = latest.values()
            members = sum(1 for m in self.members if member_on(m, day))
            out.append({
                "date": day,
                "km": round(sum(_num(r["distance_m"]) for r in rows) / 1000, 1),
                "elev": round(sum(_num(r["elev_gain_m"]) for r in rows)),
                "activities": int(sum(_num(r["activities"]) for r in rows)),
                "active": sum(1 for r in rows if _num(r["activities"]) > 0),
                "members": self.count_as_of(day) or members,
            })
        return out
