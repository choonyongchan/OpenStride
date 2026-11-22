"""Settings, all from environment variables (a .env file works with docker compose). No secrets in code.

Required: OPENSTRIDE_CLUB_ID, OPENSTRIDE_CHALLENGE_START. Everything else has a default; see .env.example.
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from zoneinfo import ZoneInfo

FOOT_SPORTS = "Run,TrailRun,VirtualRun,Walk,Hike"


@dataclass(frozen=True)
class Settings:
    club_id: int
    club_name: str
    challenge_start: str          # YYYY-MM-DD, local; activities before it don't count
    timezone: str                 # IANA zone: when a day / week starts
    sports: frozenset[str]        # Strava sport types that count; match the club's sport (its leaderboard's)
    data_dir: Path                # the CSVs and the session live here
    session_file: Path
    sync_minutes: int             # light sync (members, feed, leaderboard) every N minutes; 0 = never
    scan_hour: int | None         # local hour of the nightly profile scan; None = never
    headless: bool
    browser_channel: str
    api_token: str                # bearer token for /api/v3/* and POST /api/sync; "" disables them

    @property
    def tz(self) -> ZoneInfo:
        return ZoneInfo(self.timezone)


def _bool(value: str) -> bool:
    return value.strip().lower() in ("1", "true", "yes", "on")


def from_env(env: dict | None = None) -> Settings:
    """Settings from env (default os.environ). ValueError names the variable that is missing or malformed."""
    e = os.environ if env is None else env

    def get(name: str, default: str | None = None) -> str:
        value = e.get(name, default)
        if value is None or value == "":
            raise ValueError(f"{name} is required")
        return value

    try:
        club_id = int(get("OPENSTRIDE_CLUB_ID"))
    except ValueError as err:
        raise ValueError(f"OPENSTRIDE_CLUB_ID must be a number ({err})") from None
    start = get("OPENSTRIDE_CHALLENGE_START")
    try:
        date.fromisoformat(start)
    except ValueError:
        raise ValueError("OPENSTRIDE_CHALLENGE_START must be a date like 2026-09-14") from None
    timezone = e.get("OPENSTRIDE_TIMEZONE") or "UTC"
    ZoneInfo(timezone)   # fails loudly on a typo
    data_dir = Path(e.get("OPENSTRIDE_DATA_DIR") or "data")
    scan = (e.get("OPENSTRIDE_SCAN_HOUR") if e.get("OPENSTRIDE_SCAN_HOUR") is not None else "23").strip()
    return Settings(
        club_id=club_id,
        club_name=e.get("OPENSTRIDE_CLUB_NAME") or f"Club {club_id}",
        challenge_start=start,
        timezone=timezone,
        sports=frozenset(s.strip() for s in (e.get("OPENSTRIDE_SPORTS") or FOOT_SPORTS).split(",") if s.strip()),
        data_dir=data_dir,
        session_file=Path(e.get("OPENSTRIDE_SESSION_FILE") or data_dir / "auth_state.json"),
        sync_minutes=int(e.get("OPENSTRIDE_SYNC_MINUTES") or 60),
        scan_hour=int(scan) if scan not in ("", "none", "off", "-1") else None,
        headless=_bool(e.get("OPENSTRIDE_HEADLESS") or "true"),
        browser_channel=e.get("OPENSTRIDE_BROWSER_CHANNEL") or "",
        api_token=e.get("OPENSTRIDE_API_TOKEN") or "",
    )
