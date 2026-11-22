"""The data directory: four CSV ledgers (open them in any spreadsheet) and a status file.

    activities.csv    one row per activity, any sport, deduped on activity_id, append-only
    members.csv       everyone ever on the roster: athlete_id, name, admin, ingest_at, left_at
    member_count.csv  the club's headline member count, one row per sync
    statistics.csv    per athlete per day: cumulative totals of the counted sports since challenge_start

One lock serialises every read and write, so the API never sees a half-written file.
"""
from __future__ import annotations

import csv
import json
import threading
from datetime import datetime, timezone
from pathlib import Path

LOCK = threading.RLock()

ACTIVITY_FIELDS = [
    "activity_id", "athlete_id", "athlete_name", "athlete_firstname",
    "start_date_utc", "start_date_local", "activity_name", "type",
    "distance_m", "moving_time_s", "elapsed_time_s", "pace", "elev_gain_m", "steps",
    "device_name", "workout_type", "is_virtual", "is_commute",
    "visibility", "location", "description", "kudos_count", "comment_count",
    "entity", "scraped_at",
]
MEMBER_FIELDS = ["athlete_id", "name", "admin", "ingest_at", "left_at"]
COUNT_FIELDS = ["scraped_at", "member_count"]
FIGURES = ["distance_m", "moving_time_s", "elev_gain_m", "activities"]
STAT_FIELDS = ["athlete_id", "date", *FIGURES, "source", "synced_at"]


class Store:
    def __init__(self, data_dir: str | Path):
        self.dir = Path(data_dir)
        self.activities = self.dir / "activities.csv"
        self.members = self.dir / "members.csv"
        self.member_count = self.dir / "member_count.csv"
        self.statistics = self.dir / "statistics.csv"
        self.status_file = self.dir / "status.json"

    def version(self) -> tuple:
        """Changes whenever a ledger changes: a cache key."""
        return tuple(p.stat().st_mtime_ns if p.exists() else 0
                     for p in (self.activities, self.members, self.member_count, self.statistics))

    def read_status(self) -> dict:
        with LOCK:
            try:
                return json.loads(self.status_file.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                return {}

    def update_status(self, **fields) -> dict:
        with LOCK:
            status = {**self.read_status(), **fields}
            self.dir.mkdir(parents=True, exist_ok=True)
            tmp = self.status_file.with_suffix(".tmp")
            tmp.write_text(json.dumps(status, indent=2), encoding="utf-8")
            tmp.replace(self.status_file)
            return status


def read_csv(path: Path) -> list[dict]:
    """Every row of the CSV at path as a dict; [] if it doesn't exist yet."""
    with LOCK:
        if not path.exists():
            return []
        with path.open(encoding="utf-8", newline="") as f:
            return list(csv.DictReader(f))


def append_rows(path: Path, fields: list, rows: list) -> None:
    """Append rows, writing the header first if the file is new."""
    if not rows:
        return
    with LOCK:
        path.parent.mkdir(parents=True, exist_ok=True)
        new = not path.exists()
        with path.open("a", encoding="utf-8", newline="") as f:
            w = csv.DictWriter(f, fieldnames=fields, extrasaction="ignore")
            if new:
                w.writeheader()
            w.writerows(rows)


def write_csv(path: Path, fields: list, rows: list) -> None:
    """Replace the CSV via a temp file, so a crash never leaves it half-written."""
    with LOCK:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".tmp")
        with tmp.open("w", encoding="utf-8", newline="") as f:
            w = csv.DictWriter(f, fieldnames=fields, extrasaction="ignore")
            w.writeheader()
            w.writerows(rows)
        tmp.replace(path)


def now_utc() -> str:
    """Now as an ISO UTC timestamp to the second: the scraped_at / ingest_at / synced_at stamp."""
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def local_date(iso_utc: str | None, tzinfo) -> str:
    """An ISO UTC timestamp (naive = UTC) as a local YYYY-MM-DD, or '' if unusable."""
    raw = (iso_utc or "").strip()
    try:
        dt = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        return ""
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(tzinfo).date().isoformat()
