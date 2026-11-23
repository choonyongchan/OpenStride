# OpenStride dashboard

The dashboard is a self-hosted web page for a Strava club challenge. It shows:

- club totals;
- awards;
- a sortable leaderboard of every member, including members with no activity yet;
- standings as of any past day;
- trend charts.

It also serves the three Strava club endpoints that were removed (`/api/v3/clubs/{id}/activities`,
`/members` and `/admins`), built from everything it has synced.

It keeps itself up to date: a sync runs every hour by default, and a deeper profile scan runs nightly. All
data is stored as CSV files you can open in any spreadsheet.

> To run it always-on in a container, see [Docker](../docker/README.md). Docker runs the same dashboard,
> with automatic restarts.

**Contents:** [How it works](#how-it-works) · [Prerequisites](#prerequisites) · [Install](#1-install) ·
[Configure](#2-configure-env) · [Log in and run](#3-log-in-and-run) · [Commands](#commands) ·
[Syncing](#syncing) · [Data files](#data-files) · [API](#api) · [Operating it](#operating-it) ·
[Troubleshooting](#troubleshooting)

---

## How it works

The dashboard is **one Python process**. The backend and frontend are not run separately, and the
frontend has no build step.

```text
python -m dashboard serve
├── FastAPI (uvicorn, :8000)
│   ├── /api/*            JSON for the page, plus the Strava-shaped /api/v3/clubs/* endpoints
│   └── /                 static frontend: dashboard/static/index.html, app.js, style.css
└── Scheduler thread
    └── every OPENSTRIDE_SYNC_MINUTES:  openstride.ClubScraper → strava.com
                                        → data/*.csv → read by the API
```

The scheduler uses the [OpenStride library](../library/README.md) to read the club as your logged-in
account. The API reads only from the CSV files in `data/`. Opening the page never sends a request to
Strava.

## Prerequisites

- **Python 3.11 or newer**, and **Git**.
- **A Strava account in the club**, with its display language set to **English**. To list every member,
  the account must be allowed to view the club's members.
- **A screen for the first login.** This opens a browser window. Later syncs run headless.
- About 300 MB of disk space for Chromium.

See the [library prerequisites](../library/README.md#prerequisites-and-assumptions) for the full list.

## 1. Install

Run these commands from the folder where you keep projects.

**macOS / Linux**

```bash
git clone https://github.com/choonyongchan/OpenStride && cd OpenStride
python3 -m venv .venv
source .venv/bin/activate
```

**Windows (PowerShell)**

```powershell
git clone https://github.com/choonyongchan/OpenStride; cd OpenStride
python -m venv .venv
.venv\Scripts\Activate.ps1
```

If PowerShell blocks the activation script, run
`Set-ExecutionPolicy -Scope CurrentUser RemoteSigned` once.

**Then, on every OS** (with the venv active):

```bash
pip install -e . -r requirements-dashboard.txt
python -m playwright install chromium
```

## 2. Configure `.env`

The dashboard is configured only through environment variables. The repository includes a template.
Copy it:

| OS / shell | Command |
|---|---|
| macOS / Linux | `cp .env.example .env` |
| Windows PowerShell | `Copy-Item .env.example .env` |
| Windows cmd | `copy .env.example .env` |

Open `.env` in any text editor and set the two required values:

```ini
OPENSTRIDE_CLUB_ID=123456               # the number in strava.com/clubs/<id>
OPENSTRIDE_CHALLENGE_START=2026-09-14   # local date; activities before it don't count
```

### All settings

| Variable | Default | Meaning |
|---|---|---|
| `OPENSTRIDE_CLUB_ID` | **required** | Strava club id |
| `OPENSTRIDE_CHALLENGE_START` | **required** | First day that counts, `YYYY-MM-DD`, in the club's time zone |
| `OPENSTRIDE_CLUB_NAME` | `Club <id>` | Name shown on the dashboard |
| `OPENSTRIDE_TIMEZONE` | `UTC` | IANA zone, such as `Asia/Singapore` or `Europe/London`. It sets when days and weeks start and when the nightly scan runs. |
| `OPENSTRIDE_SPORTS` | `Run,TrailRun,VirtualRun,Walk,Hike` | Comma-separated Strava sport types that count. **Match the club's sport**, which is the sport its Strava leaderboard counts. |
| `OPENSTRIDE_SYNC_MINUTES` | `60` | Light sync interval in minutes. `0` means sync only when asked. |
| `OPENSTRIDE_SCAN_HOUR` | `23` | Local hour (0–23) of the nightly profile scan. `off` disables it. |
| `OPENSTRIDE_API_TOKEN` | empty | Bearer token that turns on `/api/v3/*` and `POST /api/sync`. Empty keeps them off. |
| `OPENSTRIDE_DATA_DIR` | `data` | Folder for the CSV files and `status.json` |
| `OPENSTRIDE_SESSION_FILE` | `<data dir>/auth_state.json` | Strava session file |
| `OPENSTRIDE_HEADLESS` | `true` | `false` shows the browser during syncs, for debugging |
| `OPENSTRIDE_BROWSER_CHANNEL` | empty | Empty uses the bundled Chromium. `chrome` or `msedge` uses an installed browser. |
| `HOST`, `PORT` | `0.0.0.0`, `8000` | Address `serve` listens on. Also available as `--host` and `--port`. |

The settings are checked at startup. A missing or malformed value stops the program with exit code `2`
and a message naming the variable, for example `config error: OPENSTRIDE_CLUB_ID is required`.

To make an API token, run:

```bash
python -c "import secrets; print(secrets.token_urlsafe(32))"
```

> **`.env` and `data/auth_state.json` are secrets.** Both are in `.gitignore`. Don't commit them.

## 3. Log in and run

The dashboard reads environment variables, not the `.env` file itself. To load `.env`, put
**`python -m dotenv run --`** in front of each command. This works the same on Windows, macOS and Linux,
in any shell. `python-dotenv` was already installed by `uvicorn[standard]`.

```bash
python -m dotenv run -- python -m dashboard login    # once: log in, saves data/auth_state.json
python -m dotenv run -- python -m dashboard serve    # open http://localhost:8000
```

The first sync starts as soon as the server starts. The page says "No data yet" until that sync finishes,
which usually takes under a minute.

To use another env file, add `-f`:

```bash
python -m dotenv -f club-b.env run -- python -m dashboard serve --port 8001
```

Run the commands with the venv active, so that `python` is the venv's Python.

<details>
<summary>Alternative: set the variables in the shell yourself</summary>

Variables set this way only last for the current terminal session.

macOS / Linux (bash, zsh):

```bash
export OPENSTRIDE_CLUB_ID=123456 OPENSTRIDE_CHALLENGE_START=2026-09-14
python -m dashboard serve
```

Windows PowerShell:

```powershell
$env:OPENSTRIDE_CLUB_ID = "123456"; $env:OPENSTRIDE_CHALLENGE_START = "2026-09-14"
python -m dashboard serve
```

Windows cmd:

```bat
set OPENSTRIDE_CLUB_ID=123456
set OPENSTRIDE_CHALLENGE_START=2026-09-14
python -m dashboard serve
```

Don't use `source .env` or `set -a; . ./.env` in bash. Values with spaces, such as
`OPENSTRIDE_CLUB_NAME=My Running Club`, and inline `# comments` break it.

</details>

## Commands

Put `python -m dotenv run --` in front of each of these.

| Command | Does |
|---|---|
| `python -m dashboard serve [--host H] [--port P]` | Runs the API, the page and the scheduler until stopped (Ctrl+C) |
| `python -m dashboard sync` | Runs one light sync now, prints a summary and exits |
| `python -m dashboard sync --scan` | Runs a light sync plus the profile scan |
| `python -m dashboard sync --setup` | Runs a light sync plus a full rescan of every week since the start, for every member |
| `python -m dashboard login` | Opens a browser to log in, and saves the session to `OPENSTRIDE_SESSION_FILE` |

`sync` exits with `0` when it succeeds and `1` when it fails, printing `sync failed: <Error>: <message>`.
It exits with `2` when the configuration is wrong.

`sync` can run while `serve` is running, but don't start two syncs at the same moment, because each one
opens its own browser.

## Syncing

### What each sync reads

| Step | When | Reads | Writes |
|---|---|---|---|
| **Roster** | every sync | every members page and the headline count | `members.csv`, `member_count.csv` |
| **Feed** | every sync | the club feed (about the last 26 hours) | `activities.csv` |
| **Leaderboard** | every sync | Strava's weekly leaderboard, this week and last week (about the top 100, club sport only) | `statistics.csv` |
| **Profile scan** | once a day, on the first sync at or after `OPENSTRIDE_SCAN_HOUR`, or with `--scan` | each current member's last two profile weeks. Members who joined since the last scan get every week since the start. | `activities.csv`, `statistics.csv` |
| **Setup scan** | only with `--setup` | every week since the start, for everyone | as above |

With the defaults, the page is never more than about an hour behind Strava.

### Where the totals come from

`statistics.csv` keeps each athlete's **cumulative totals since the challenge start**, with one row per
athlete per day. For each week, it uses the best source available:

1. **The leaderboard.** This is Strava's own weekly figure, and the most accurate source.
2. **The profile week.** This is the sum of the athlete's own activities in that week. It covers athletes
   below the top 100.
3. **The feed.** This adds activities from the feed that are newer than the athlete's last row. It
   covers athletes whose profile is private to your account.

The `source` column records which one was used.

### First run

The first light sync only sees the last 26 hours of the feed and the two leaderboard weeks. If the
challenge started earlier, backfill it once:

```bash
python -m dotenv run -- python -m dashboard sync --setup
```

A setup scan sends one request per member per week. For a large club, or a long challenge, Strava may
rate-limit it (HTTP 429). Everything fetched before that is saved, and the next nightly scan continues
from there.

## Data files

Everything is stored in `OPENSTRIDE_DATA_DIR` (default `data/`).

| File | Holds | Written |
|---|---|---|
| `activities.csv` | Every activity seen, of any sport. One row per `activity_id`. | Append-only, never rewritten |
| `members.csv` | Everyone who was ever on the roster: `athlete_id`, `name`, `admin`, `ingest_at`, `left_at` | Rewritten each sync |
| `member_count.csv` | The headline member count at each sync | Append-only |
| `statistics.csv` | Cumulative totals per athlete per day: `distance_m`, `moving_time_s`, `elev_gain_m`, `activities`, `source`, `synced_at` | Rewritten each sync |
| `status.json` | The last sync's outcome: `last_success`, `last_error`, `summary`, `last_scan_date` | Each sync |
| `auth_state.json` | The Strava session. **Secret.** | `login` |

A member is only marked as left (`left_at`) when the roster read was complete, meaning its count matched
Strava's headline. A partial read never removes anyone.

Files are replaced through a temporary file, so a crash never leaves a half-written CSV. You can open
them while the server runs. Don't edit them while a sync is running.

## API

| Method and path | Auth | Returns |
|---|---|---|
| `GET /api/club` | none | Club id, name, challenge start, time zone, sports, Strava URL, sync status |
| `GET /api/report?date=YYYY-MM-DD` | none | Totals, awards, leaderboard and devices as of the end of that day (default today) |
| `GET /api/dates` | none | Days that have history |
| `GET /api/trend` | none | The club's cumulative km, elevation, activities, active athletes and members per day |
| `GET /api/v3/clubs/{id}/activities?page=&per_page=` | token | The removed Strava endpoint: every synced activity, newest first |
| `GET /api/v3/clubs/{id}/members?page=&per_page=` | token | Current members, admins first |
| `GET /api/v3/clubs/{id}/admins?page=&per_page=` | token | Current admins |
| `POST /api/sync?scan=false&setup=false` | token | `202` when the sync is queued, `409` if one is already running |
| `GET /docs` | none | Interactive OpenAPI docs |

How the token works:

- The endpoints marked **token** need the header `Authorization: Bearer <OPENSTRIDE_API_TOKEN>`.
- If no token is set, they answer `503`. A wrong token gets `401`.
- `/api/v3/*` only serves the configured club. Any other id gets `404`.
- Paging works like Strava's: `page` starts at 1, and `per_page` defaults to 30 (max 200).
- The responses have the same JSON shape as Strava's old endpoints, plus the extra fields described in
  the [library models](../library/README.md#models).

```bash
curl -H "Authorization: Bearer $TOKEN" \
  "http://localhost:8000/api/v3/clubs/123456/activities?per_page=5"
curl -X POST -H "Authorization: Bearer $TOKEN" "http://localhost:8000/api/sync?scan=true"
```
## Operating it

- **When the session expires**, the page shows a red notice with `SessionExpired`. Run
  `python -m dotenv run -- python -m dashboard login` on a machine with a screen. If the server has no
  screen, log in elsewhere and copy the new `auth_state.json` over. The next sync picks it up, so there
  is no need to restart the server.
- **To check sync health**, look at the server log (`sync finished: ...`), or read `data/status.json`, or
  use `GET /api/club` → `sync`.
- **To back up**, copy the `data/` folder. The CSV files are the entire state, so restoring them restores
  the dashboard.
- **To expose it to the internet**, put it behind a reverse proxy with HTTPS, such as Caddy or nginx.
  The page itself has no login, so anyone with the URL can see member names and totals.
- **To keep it running**, use [Docker](../docker/README.md), or a process manager. A systemd example:

  ```ini
  # /etc/systemd/system/openstride.service
  [Service]
  WorkingDirectory=/opt/OpenStride
  ExecStart=/opt/OpenStride/.venv/bin/python -m dotenv run -- python -m dashboard serve
  Restart=always
  User=openstride
  [Install]
  WantedBy=multi-user.target
  ```

- **To track more than one club**, run one server per club, each with its own env file, `OPENSTRIDE_DATA_DIR`
  and `--port`.

## Troubleshooting

| Symptom | Cause and fix |
|---|---|
| `config error: OPENSTRIDE_... is required` | `.env` wasn't loaded. Put `python -m dotenv run --` in front of the command, and run it from the folder that contains `.env`. |
| `SessionExpired` | There is no session file, or it expired. Run `login` again. |
| `UnsupportedLocale` | Set Strava's language to English at strava.com/settings/display. |
| `RateLimited` / `SyncIncomplete ... HTTP 429` | Strava throttled the account. Wait. Light syncs carry on, and the scan continues the next night. Raise `OPENSTRIDE_SYNC_MINUTES` if this keeps happening. |
| `MarkupChanged` | Strava changed a page. Update OpenStride (`git pull`, then `pip install -e .`). |
| Totals don't match Strava's leaderboard | Check that `OPENSTRIDE_SPORTS` matches the club's sport, and that `OPENSTRIDE_TIMEZONE` matches the club's time zone. |
| Some members always show zero | Their profiles are private to your account and they haven't appeared in the feed, or they haven't been active. |
| `Executable doesn't exist ... chromium` | Run `python -m playwright install chromium` with the venv active. |

---

To change or extend the dashboard, see the [developer guide](DeveloperGuide.md).
