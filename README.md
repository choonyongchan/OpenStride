# OpenStride

**OpenStride: compatible with Strava. A Python library for club activities, members and admins, plus a self-hosted club dashboard.**

On 1 September 2026 Strava removed three API endpoints:
`GET /clubs/{id}/activities`, `GET /clubs/{id}/members` and `GET /clubs/{id}/admins`.
OpenStride provides these three methods again, with the same arguments and return types,
using your own logged-in Strava session. Everything else in the official API is served by
[stravalib](https://github.com/stravalib/stravalib), which OpenStride builds on.

> **Unofficial. For educational purposes only.**
> OpenStride is not affiliated with, endorsed by or sponsored by Strava, Inc. "Strava" is a
> trademark of Strava, Inc. Review Strava's Terms of Service before use: using OpenStride
> may get the account you log in with restricted or banned. You are responsible for how you
> use OpenStride and for the club members' data it returns. Only use it with clubs you
> belong to, keep the request rate low, and respect members' privacy choices.

This repository has three parts:

| Part | What it is | Where |
|---|---|---|
| **Library** | `pip install openstride`: `ClubScraper`, and `Client` (a drop-in `stravalib.Client`) | `src/` |
| **Dashboard** | FastAPI backend + static frontend: leaderboard, awards, history, trend, and Strava-shaped `/api/v3/clubs/...` endpoints | `dashboard/` |
| **Docker image** | the dashboard with Chromium, one command to run | `Dockerfile`, `docker-compose.yml` |

---

## 1. The library

```bash
pip install openstride
python -m playwright install chromium
openstride login                       # log in to Strava in the window that opens; saves auth_state.json
```

### Drop-in for stravalib

```python
from openstride import Client

with Client(access_token="...", session_file="auth_state.json") as client:
    me = client.get_athlete()                        # official API, via stravalib
    for a in client.get_club_activities(123456):     # removed endpoint, restored
        print(a.athlete.firstname, a.sport_type, a.distance, a.moving_time)
    members = client.get_club_members(123456)
    admins = client.get_club_admins(123456)
```

`Client` subclasses `stravalib.Client`, so every other method is stravalib's. The three
club methods take the same arguments (`club_id`, `limit`) and return the same model types
(`isinstance(a, stravalib.model.ClubActivity)` holds), as lists instead of lazy iterators.
`access_token` is optional if you only use the club methods.

### Without an API token

```python
from datetime import date
from openstride import ClubScraper

with ClubScraper("auth_state.json", timezone="Asia/Singapore") as s:
    activities = s.get_club_activities(123456)        # about the last 26 hours, newest first
    roster = s.get_club_roster(123456)                # .member_count, .members, .complete
    admins = s.get_club_admins(123456)
    count = s.get_club_member_count(123456)
    board = s.get_club_leaderboard(123456, "last")    # Strava's weekly club leaderboard, ~top 100
    many = s.get_athlete_weeks([(987654, date(2026, 9, 14)), ...])   # stops cleanly at HTTP 429
```

From the command line: `openstride activities 123456`, `openstride members 123456`,
`openstride admins 123456`, `openstride count 123456` (JSON on stdout).

### What you get, compared with the old endpoints

| Removed endpoint | Old API returned | OpenStride returns |
|---|---|---|
| `get_club_activities` | athlete first name + last initial, name, distance, moving/elapsed time, elevation, type, sport_type, workout_type. **No activity id, date or athlete id.** | all of those, plus `id`, `athlete_id`, full name, `start_date`, `start_date_local`, device, kudos, comments, description, location, visibility, steps, pace. `raw_type` keeps sports stravalib doesn't know yet. |
| `get_club_members` | first name, last initial, member/admin/owner flags. **No athlete id.** | `id`, full `name`, `firstname`, `lastname`, `admin`, `member="accepted"`. `owner` is always `None` (Strava doesn't expose it). |
| `get_club_admins` | summary athletes | `id`, full `name`, `firstname`, `lastname` |

### Accuracy: read this before relying on the numbers

- **Numbers match what Strava displays**, so they are rounded the way Strava
  rounds them on screen: distance to 0.01 km or 0.01 mi (about 10 m), elevation to 1 m or 1 ft,
  and durations over an hour shown without seconds lose them. Imperial accounts are
  supported and converted to metres.
- **The account's Strava language must be English.** Labels and number formats are
  localised; a non-English account raises `UnsupportedLocale` rather than returning wrong
  numbers. Change it at strava.com/settings/display.
- **The club feed keeps limited entries.** Poll frequently (hourly), or use the profile weeks
  (`get_athlete_weeks`) to backfill. Strava rate-limits those; the call stops at the first
  HTTP 429 and tells you what it didn't fetch.
- **You see what the logged-in account sees.** Private activities and private profiles are
  invisible; the full member list needs an account that may view the club's members.
- **Strava can change its service at any time.** OpenStride raises `MarkupChanged` instead of
  returning wrong data. All errors subclass `OpenStrideError`:
  `SessionExpired` (run `openstride login` again), `RateLimited`, `MarkupChanged`, `UnsupportedLocale`.

One `ClubScraper` drives one Chromium and is not thread-safe; use one per thread.

---

## 2. The dashboard

A club challenge dashboard: totals, awards, a sortable leaderboard (every member, active
or not), history by day and trend charts. It syncs on a schedule and keeps its data as
CSV files you can open in any spreadsheet.

```bash
git clone <this repo> && cd OpenStride
python -m venv .venv && . .venv/bin/activate      # Windows: .venv\Scripts\activate
pip install -e . -r requirements-dashboard.txt
python -m playwright install chromium
cp .env.example .env                               # set OPENSTRIDE_CLUB_ID and OPENSTRIDE_CHALLENGE_START
set -a; . ./.env; set +a                           # or export the variables your own way
python -m dashboard login                          # saves data/auth_state.json
python -m dashboard serve                          # http://localhost:8000
```

| Command | Does |
|---|---|
| `python -m dashboard serve` | API + dashboard; syncs every `OPENSTRIDE_SYNC_MINUTES`, profile scan at `OPENSTRIDE_SCAN_HOUR` |
| `python -m dashboard sync` | one light sync now (members, feed, leaderboard) |
| `python -m dashboard sync --scan` | ...plus the profile scan (fills gaps the feed missed) |
| `python -m dashboard sync --setup` | rescan every week since the start, for everyone |
| `python -m dashboard login` | log in again when the session expires |

### Where the numbers come from

Each sync reads the roster (and the headline member count), the club feed and the weekly
leaderboard; the nightly scan reads every current member's last two profile weeks (every
week since the start for newcomers). Per athlete and day, `statistics.csv` keeps cumulative
totals from the best source available: Strava's leaderboard figure, else the profile week,
else the feed. Set `OPENSTRIDE_SPORTS` to the club's sport so the three agree; the default is
foot sports (Run, TrailRun, VirtualRun, Walk, Hike).

| File in `data/` | Holds |
|---|---|
| `activities.csv` | every activity seen, any sport, one row each, never rewritten |
| `members.csv` | everyone ever on the roster, with `admin`, `ingest_at`, `left_at` |
| `member_count.csv` | the headline member count at each sync |
| `statistics.csv` | cumulative totals per athlete per day, with `source` |
| `status.json` | the last sync's outcome (shown on the dashboard when it failed) |
| `auth_state.json` | the Strava session. **Secret: never commit or share it.** |

### API

| Endpoint | |
|---|---|
| `GET /api/club` | name, settings, last sync |
| `GET /api/report?date=YYYY-MM-DD` | totals, awards, leaderboard as of a day |
| `GET /api/dates`, `GET /api/trend` | days with history; cumulative totals per day |
| `GET /api/v3/clubs/{id}/activities` | **the removed endpoints, same paths and JSON**, from the synced data (all history, not just 26 h), `?page=&per_page=` (max 200) |
| `GET /api/v3/clubs/{id}/members` | |
| `GET /api/v3/clubs/{id}/admins` | |
| `POST /api/sync?scan=&setup=` | run a sync now |

`/api/v3/*` and `POST /api/sync` need `Authorization: Bearer $OPENSTRIDE_API_TOKEN`; they are
off until you set the token. They expose more than the dashboard does (descriptions,
locations), so keep the token private.

```bash
curl -H "Authorization: Bearer $OPENSTRIDE_API_TOKEN" localhost:8000/api/v3/clubs/123456/activities?per_page=5
```

---

## 3. Docker

```bash
cp .env.example .env        # fill in OPENSTRIDE_CLUB_ID, OPENSTRIDE_CHALLENGE_START, ...
mkdir -p data
# Log in once on a machine with a screen (the container has none), then copy the session in:
pip install openstride && python -m playwright install chromium && openstride login --session-file data/auth_state.json
docker compose up -d        # http://localhost:8000
docker compose run --rm dashboard python -m dashboard sync --setup   # optional: backfill since the start
```

The image runs as an unprivileged user with everything in the `/data` volume. When the
dashboard shows "SessionExpired", run `openstride login --session-file data/auth_state.json`
again; nothing else needs restarting.

---

## Development

Tests come in three layers, each run and coverage-gated in CI: `tests/library` (unit), `tests/dashboard`
(integration), and `tests/e2e` (headless Chromium against a local fake site).

```bash
pip install -e ".[test]" -r requirements-dashboard.txt httpx
python -m playwright install chromium
python -m pytest -q --cov                    # unit + integration + e2e, offline: never contacts Strava
OPENSTRIDE_TEST_CLUB_ID=123456 python -m pytest tests/live --live   # vs live Strava: roster tallies, profile weeks = leaderboard
python -m build && twine check dist/*        # the PyPI package (library only)
```

The library lives in `src/` (`_parse.py` is pure functions, unit-tested). The dashboard
calls the library. To publish: bump `version` in `pyproject.toml` and `__version__`, then
`twine upload dist/*`.

## Licence

MIT. Builds on [stravalib](https://github.com/stravalib/stravalib) (Apache-2.0) and
[Playwright](https://playwright.dev/). Dashboard originally based on
[DatabenderSK/strava-club-dashboard](https://github.com/DatabenderSK/strava-club-dashboard).
