"""The FastAPI app: the dashboard's JSON, Strava-shaped replacements for the removed club endpoints, and the
static frontend.

    GET  /api/club                       club name, member count, sync status
    GET  /api/report?date=YYYY-MM-DD     totals, awards, leaderboard as of a day (default today)
    GET  /api/dates                      days with history
    GET  /api/trend                      cumulative club totals per day
    GET  /api/v3/clubs/{id}/activities   \\
    GET  /api/v3/clubs/{id}/members       > same paths and JSON as Strava's removed endpoints (+ extra fields),
    GET  /api/v3/clubs/{id}/admins       /  bearer OPENSTRIDE_API_TOKEN; ?page=1&per_page=30
    POST /api/sync?scan=false&setup=false   run a sync now (bearer OPENSTRIDE_API_TOKEN)
"""
from __future__ import annotations

import hmac
from contextlib import asynccontextmanager
from datetime import date
from pathlib import Path

from fastapi import Depends, FastAPI, HTTPException, Query, Request
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from openstride import ClubActivity, ClubAdmin, ClubAthlete

from .config import Settings, from_env
from .scheduler import Scheduler
from .stats import Dataset
from .store import Store, read_csv

STATIC = Path(__file__).parent / "static"


def _num(value, cast):
    return cast(float(value)) if value not in (None, "") else None


def row_to_activity(r: dict) -> ClubActivity:
    """An activities.csv row back into the API model (the inverse of sync.activity_row)."""
    flag = {"True": True, "False": False}
    return ClubActivity.from_feed({
        "id": int(r["activity_id"]), "athlete_id": int(r["athlete_id"]),
        "athlete_name": r.get("athlete_name") or None, "athlete_firstname": r.get("athlete_firstname") or None,
        "name": r.get("activity_name") or None, "sport_type": r.get("type") or None,
        "start_date": r.get("start_date_utc") or None, "start_date_local": r.get("start_date_local") or None,
        "distance": _num(r.get("distance_m"), float), "moving_time": _num(r.get("moving_time_s"), int),
        "elapsed_time": _num(r.get("elapsed_time_s"), int), "total_elevation_gain": _num(r.get("elev_gain_m"), float),
        "steps": _num(r.get("steps"), int), "pace": r.get("pace") or None, "device_name": r.get("device_name") or None,
        "workout_type": _num(r.get("workout_type"), int), "is_virtual": flag.get(r.get("is_virtual")),
        "is_commute": flag.get(r.get("is_commute")), "visibility": r.get("visibility") or None,
        "location": r.get("location") or None, "description": r.get("description") or None,
        "kudos_count": _num(r.get("kudos_count"), int), "comment_count": _num(r.get("comment_count"), int),
        "entity": r.get("entity") or None,
    })


def _page(items: list, page: int, per_page: int) -> list:
    return items[(page - 1) * per_page: page * per_page]


def create_app(settings: Settings | None = None, *, scheduler: Scheduler | None = None,
               start_scheduler: bool = True) -> FastAPI:
    s = settings or from_env()
    store = Store(s.data_dir)
    data = Dataset(s)
    sched = scheduler or Scheduler(s)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        if start_scheduler:
            sched.start()
        yield
        sched.stop()

    app = FastAPI(title="openstride dashboard", lifespan=lifespan,
                  description="Unofficial. Not affiliated with Strava, Inc. For educational purposes only.")

    def require_token(request: Request) -> None:
        if not s.api_token:
            raise HTTPException(503, "Set OPENSTRIDE_API_TOKEN to enable this endpoint.")
        auth = request.headers.get("authorization", "")
        if not hmac.compare_digest(auth.encode(), f"Bearer {s.api_token}".encode()):
            raise HTTPException(401, "Authorization: Bearer <OPENSTRIDE_API_TOKEN> required.",
                                headers={"WWW-Authenticate": "Bearer"})

    def club_only(club_id: int) -> None:
        if club_id != s.club_id:
            raise HTTPException(404, f"This server only syncs club {s.club_id}.")

    paging = {"page": Query(1, ge=1), "per_page": Query(30, ge=1, le=200)}

    @app.get("/api/club")
    def club():
        status = store.read_status()
        return {"id": s.club_id, "name": s.club_name, "challenge_start": s.challenge_start,
                "timezone": s.timezone, "sports": sorted(s.sports),
                "strava_url": f"https://www.strava.com/clubs/{s.club_id}",
                "sync": {k: status.get(k) for k in ("running", "last_success", "last_error", "finished_at")}}

    @app.get("/api/report")
    def report(day: date | None = Query(None, alias="date")):
        return data.report(day.isoformat() if day else None)

    @app.get("/api/dates")
    def dates():
        return data.history_dates()

    @app.get("/api/trend")
    def trend():
        return data.trend()

    @app.post("/api/sync", dependencies=[Depends(require_token)])
    def sync_now(scan: bool = False, setup: bool = False):
        if not sched.request(scan=scan, setup=setup):
            return JSONResponse({"started": False, "reason": "a sync is already running"}, status_code=409)
        return JSONResponse({"started": True}, status_code=202)

    @app.get("/api/v3/clubs/{club_id}/activities", dependencies=[Depends(require_token)])
    def club_activities(club_id: int, page: int = paging["page"], per_page: int = paging["per_page"]):
        club_only(club_id)
        rows = sorted(read_csv(store.activities), key=lambda r: (r.get("start_date_utc") or "", r["activity_id"]),
                      reverse=True)
        return [row_to_activity(r).model_dump(mode="json", exclude_none=True) for r in _page(rows, page, per_page)]

    def current_members() -> list[ClubAthlete]:
        rows = [r for r in read_csv(store.members) if not r.get("left_at")]
        admin = {"True": True, "False": False}
        members = [ClubAthlete.from_roster(int(r["athlete_id"]), r["name"], admin.get(r.get("admin") or ""))
                   for r in rows]
        return sorted(members, key=lambda m: not m.admin)   # admins first, like Strava

    @app.get("/api/v3/clubs/{club_id}/members", dependencies=[Depends(require_token)])
    def club_members(club_id: int, page: int = paging["page"], per_page: int = paging["per_page"]):
        club_only(club_id)
        return [m.model_dump(mode="json", exclude_none=True) for m in _page(current_members(), page, per_page)]

    @app.get("/api/v3/clubs/{club_id}/admins", dependencies=[Depends(require_token)])
    def club_admins(club_id: int, page: int = paging["page"], per_page: int = paging["per_page"]):
        club_only(club_id)
        admins = [ClubAdmin.from_member(m) for m in current_members() if m.admin]
        return [a.model_dump(mode="json", exclude_none=True) for a in _page(admins, page, per_page)]

    app.mount("/", StaticFiles(directory=STATIC, html=True), name="static")
    return app
