# Dashboard developer guide

This guide explains how the dashboard (`dashboard/`) works, and how to change or extend it. For how to
run it, see the [dashboard README](README.md).

**Contents:** [Set up](#set-up) · [Layout](#layout) · [Request and sync flow](#request-and-sync-flow) ·
[Design rules](#design-rules) · [Recipes](#recipes) · [Tests](#tests)

---

## Set up

```bash
git clone https://github.com/choonyongchan/OpenStride && cd OpenStride
python -m venv .venv
source .venv/bin/activate            # Windows: .venv\Scripts\activate
pip install -e ".[test]" -r requirements-dashboard.txt httpx
python -m playwright install chromium
cp .env.example .env                 # set OPENSTRIDE_CLUB_ID, OPENSTRIDE_CHALLENGE_START
python -m pytest -q tests/dashboard
```

To work on the page without contacting Strava, copy an existing `data/` folder from a running instance,
then start the server with syncing turned off:

```bash
OPENSTRIDE_SYNC_MINUTES=0 OPENSTRIDE_SCAN_HOUR=off python -m dotenv run --no-override -- python -m dashboard serve
```

By default, `dotenv run` lets values in `.env` replace variables already set in the shell. The
`--no-override` flag reverses that, so the two prefixes win. In PowerShell, set
`$env:OPENSTRIDE_SYNC_MINUTES = "0"` and `$env:OPENSTRIDE_SCAN_HOUR = "off"` first, then run the same
`--no-override` command.

The frontend is served straight from `dashboard/static/`. Edit a file there and reload the browser; no
build step is needed.

## Layout

| File | Role |
|---|---|
| `dashboard/__main__.py` | CLI: `serve`, `sync [--scan] [--setup]`, `login`. Loads `Settings`, and exits `2` on a config error. |
| `dashboard/config.py` | `Settings` (a frozen dataclass), built by `from_env(env=None)`, which reads environment variables only. **Every setting is defined here.** |
| `dashboard/store.py` | The data folder: CSV schemas (`*_FIELDS`), `Store` paths, `read_csv`, `append_rows`, `write_csv` (atomic), `status.json`. One `LOCK` guards all file I/O. |
| `dashboard/sync.py` | `run_sync()`: one sync, from the library to the CSV files. Also the statistics logic (`cumulate`, `fallback`). |
| `dashboard/scheduler.py` | `Scheduler`: a background thread that calls `run_sync` every `sync_minutes`, decides when the scan is due, and writes `status.json` |
| `dashboard/stats.py` | `Dataset`: loads the CSV files, cached by file modification time. Builds `report()`, `trend()`, `history_dates()` and the awards. |
| `dashboard/api.py` | `create_app()`: FastAPI routes, bearer-token auth, the `/api/v3` endpoints, and the static mount |
| `dashboard/static/` | `index.html`, `app.js`, `style.css`, `favicon.svg`. Plain JS with no framework or bundler. |

The dependencies only point one way:

```text
__main__ ─> api ─┬─> scheduler ─> sync ─┬─> store
                 │                       └─> openstride (library)
                 ├─> stats ─> store
                 └─> config  (used by all)
```

The API never calls Strava. Only `sync.py` does, through `openstride.ClubScraper`.

## Request and sync flow

### A page load

1. The browser loads `/`, which `StaticFiles` serves as `index.html`. `app.js` then calls
   `/api/club`, `/api/dates` and `/api/report`, and `/api/trend` when the Trend tab opens.
2. `Dataset._load()` compares `Store.version()`, the files' modification times, with its cache, and only
   re-reads the CSV files after they change.
3. `stats.compute()` turns the snapshots, activities and members into a `Report` dataclass. The API
   returns `asdict(report)`.

### A sync

`Scheduler._loop` runs `run_once()` every `sync_minutes` seconds × 60, or straight away after
`request()` from `POST /api/sync`. `run_once()` decides whether the scan is due, then calls
`run_sync(settings, scan=, setup=)`:

```text
ClubScraper(session_file, timezone=...)
 ├─ get_club_roster        → member_count.csv (append), members.csv (upsert; left_at only if roster.complete)
 ├─ get_club_activities    → activities.csv (append new activity_ids)
 ├─ [scan] get_athlete_weeks(jobs_for(...))
 │                          → activities.csv (append), weeks{(athlete, monday): counted rows}
 ├─ get_club_leaderboard("this") + ("last") → leaderboard{monday: {athlete: figures}}
 └─ update_statistics:
      cumulate(daily, weeks, leaderboard)   # each week's figures added on top of the athlete's last row before it
      fallback(daily, activities.csv)       # athletes not covered above: add feed activities since their last row
      → statistics.csv (rewrite)
```

How `run_once()` handles the result:

- **Success:** it writes `last_success` and `summary` to `status.json`.
- **`OpenStrideError`:** it writes `last_error`, which the page shows as a red notice.
- **`SyncIncomplete`:** this is raised after a partial scan, such as one stopped by a 429. The work done
  is already saved, and the scan is still marked done for today. That way it isn't retried every hour
  into the rate limit.
- **Any other exception:** it is logged and stored in `last_error`, and the server keeps running.

## Design rules

1. **CSV files are the database.** Users open them in spreadsheets, and backing up `data/` is the whole
   backup. Keep column names stable. Add new columns at the **end** of `*_FIELDS`. `DictWriter(extrasaction="ignore")`
   and `DictReader` tolerate the change, but old rows will have empty values in the new column.
2. **`activities.csv` and `member_count.csv` are append-only.** Never rewrite or delete their rows. Fix
   data in `statistics.csv`, which can be rebuilt, instead.
3. **All file I/O goes through `store.py`**, under `LOCK`. Use `write_csv` to rewrite a file, because it
   writes a temporary file and replaces the original, so a crash can't leave a half-written file.
4. **Never trust absence from an incomplete roster.** Only mark members as left when
   `roster.complete` is true.
5. **The API is read-only and never contacts Strava.** The only exception is `POST /api/sync`, which
   queues work for the scheduler thread.
6. **Playwright stays on the scheduler thread.** Each sync opens its own `ClubScraper`. Never call the
   library from a FastAPI route.
7. **Settings come only from environment variables**, through `config.from_env`. Validate at startup and
   raise `ValueError` naming the variable. Never put secrets in code.
8. **The frontend treats every name as untrusted.** Names come from Strava users. In `app.js`, set text
   through `el(..., {text})` or `textContent`, **never** `innerHTML`.
9. **`/api/v3` must match Strava's shapes.** The removed endpoints' paths, paging and JSON field names
   stay identical. Only add fields.
10. **Compare secrets in constant time.** `require_token` uses `hmac.compare_digest`. Keep it that way
    for any new secret.

## Recipes

### Add a setting

1. Add a field to `Settings` in `config.py`, with a comment saying what it is.
2. Read it in `from_env()` with a default. If it is malformed, raise
   `ValueError("OPENSTRIDE_X must be ...")`.
3. Add it to `.env.example`, with a comment.
4. Add it to the settings table in [README.md](README.md).
5. Add it to the test `settings` fixture in `tests/dashboard/conftest.py` if tests need it, and add a test
   for its default and its error message.

### Add an award

The awards are computed in `stats._awards(athletes)`. Each one is a single `_award()` call:

```python
"streak": _award({a.athlete_id: a.days_active for a in active}, names, lambda v: f"{v} days"),
```

1. If the award needs a new per-athlete number, add it to `stats.Athlete`, and fill it in `add_activity`
   or `add_snapshot`.
2. Add the `_award(...)` line. Use `pick=min` for a "lowest wins" award.
3. Add `["streak", "Longest streak"]` to the `AWARDS` list at the top of `static/app.js`. Its order
   sets the display order.
4. Add a test in `tests/dashboard/test_api.py` that checks the `/api/report` output.

### Add a leaderboard column

1. Add the value to the dict that `Athlete.row()` returns in `stats.py`.
2. In `static/index.html`, add a `<th data-key="...">` to the leaderboard header. Sorting works for any
   numeric key.
3. In `renderBoard()` in `static/app.js`, add a matching `el("td", ...)` in the same position.

### Add an API endpoint

Add it inside `create_app()` in `api.py`, **before** `app.mount("/", ...)`. The static mount catches
every path, so routes added after it are never reached.

```python
@app.get("/api/devices")
def devices():
    return data.report()["devices"]
```

- Read from `data` (the `Dataset`) or `store`. Never call the library.
- If the endpoint exposes anything beyond what the page shows, or triggers work, add
  `dependencies=[Depends(require_token)]`.
- Add a test using FastAPI's `TestClient` (see below).

### Add a stored field from the library

The example stores a new `ClubActivity` field in `activities.csv`.

1. Add the column to the **end** of `ACTIVITY_FIELDS` in `store.py`.
2. Map it in `sync.activity_row()`.
3. Map it back in `api.row_to_activity()`, so `/api/v3/.../activities` returns it.
4. Rows written before the change have it empty. Handle `""` wherever you read it.

### Change how totals are counted

The rules live in `sync.cumulate()`, which uses the leaderboard and then the profile weeks, and in
`sync.fallback()`, which uses the feed. Both are pure functions over dicts, and each has unit tests in
`tests/dashboard/test_sync.py`. Write the failing test first.

To rebuild `statistics.csv` with new rules, delete the file and run `python -m dashboard sync --setup`.
That command re-reads every week since the start.

### Change the frontend

- Styling: `style.css` defines tokens on `:root`, such as `--accent` and `--series`, with dark-mode
  overrides. Change the tokens rather than individual rules.
- Charts: `lineChart()` in `app.js` draws plain SVG with no chart library. To add a chart, add a series
  to `/api/trend`, then call `lineChart(host, points, unit)`.
- Keep the page working without the API token: it only uses the public `/api/*` endpoints.

## Tests

`tests/dashboard` holds integration tests. They need no browser or network, and CI requires 90% coverage
of `dashboard`.

| Fixture or helper (`tests/dashboard/conftest.py`) | Use |
|---|---|
| `settings` | A `Settings` from a dict, with `OPENSTRIDE_DATA_DIR` set to the test's `tmp_path` |
| `activity(...)` | Makes a `ClubActivity` with sensible defaults |
| `FakeScraper(roster=, feed=, boards=, weeks=)` | Stands in for `ClubScraper`. Pass it as `run_sync(s, scraper_factory=FakeScraper(...))`. |

To test the API, build the app without its scheduler thread:

```python
from fastapi.testclient import TestClient
from dashboard.api import create_app

client = TestClient(create_app(settings, start_scheduler=False))
assert client.get("/api/club").status_code == 200
```

`Scheduler(settings, sync=fake_run_sync)` lets you test scheduling logic without a browser.

```bash
python -m pytest -q tests/dashboard --cov=dashboard
python -m pytest -q --cov                 # every suite, as CI runs them
```

`tests/e2e` also covers the dashboard: it runs a real sync against the fake strava.com, so a library
change that breaks the dashboard fails there.
