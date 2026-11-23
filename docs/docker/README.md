# OpenStride in Docker

The Docker image runs the [dashboard](../dashboard/README.md) in a container that is always on. It holds
the same single process: the API, the web page, and the scheduler that syncs with Strava. Chromium and
every dependency are already installed.

Leave it running and the dashboard stays **close to real time**:

- a light sync (roster, feed and leaderboard) runs every hour;
- a profile scan runs every night to fill any gaps;
- the container restarts on its own after a crash or a reboot.

**Contents:** [Docker or a plain install?](#docker-or-a-plain-install) · [Prerequisites](#prerequisites) ·
[Set up](#set-up) · [Everyday tasks](#everyday-tasks) · [Without Compose](#without-compose) ·
[Security](#security) · [Troubleshooting](#troubleshooting)

---

## Docker or a plain install?

The hourly sync is **not** specific to Docker. `python -m dashboard serve` runs the same schedule. What
Docker adds:

| | Plain install (`python -m dashboard serve`) | Docker (`docker compose up -d`) |
|---|---|---|
| Hourly sync and nightly scan | yes | yes |
| Python, Chromium and system libraries | you install them | included in the image |
| Restarts after a crash or reboot | you set up systemd or a similar tool | `restart: unless-stopped` |
| Health check | no | `GET /api/club` every 60 s |
| Loading `.env` | `python -m dotenv run --` | Compose reads it by itself (`env_file: .env`) |
| Runs as | your user | the unprivileged user `app` (uid 1000) |
| Logging in to Strava | in place | **on another machine with a screen**, then copy the session file in |

Use Docker for a server, NAS or home lab that should keep the dashboard up. Use the plain install to try
it out, or to change the code.

## Prerequisites

- Docker Engine 24+ with the Compose plugin (`docker compose version`), or Docker Desktop on
  Windows or macOS.
- Python 3.14+ **on a machine with a screen**, for the one-time Strava login. This can be your laptop.
- A Strava account in the club, with its display language set to English. See the
  [library prerequisites](../library/README.md#prerequisites-and-assumptions).

## Set up

All commands run from the repository root.

### 1. Get the code

```bash
git clone https://github.com/choonyongchan/OpenStride && cd OpenStride
```

### 2. Configure

Copy the template:

| OS / shell | Command |
|---|---|
| macOS / Linux | `cp .env.example .env` |
| Windows PowerShell | `Copy-Item .env.example .env` |

Edit `.env` and set at least `OPENSTRIDE_CLUB_ID` and `OPENSTRIDE_CHALLENGE_START`. Every variable is
described in the [dashboard settings table](../dashboard/README.md#all-settings).

Some settings work differently in the container:

| Variable | In Docker |
|---|---|
| `OPENSTRIDE_DATA_DIR` | The image sets it to `/data`. Leave it unset. |
| `OPENSTRIDE_SESSION_FILE` | Defaults to `/data/auth_state.json`, which is `./data/auth_state.json` on the host. Leave it unset. |
| `OPENSTRIDE_HEADLESS` | Must stay `true`, because the container has no screen. |
| `OPENSTRIDE_SYNC_MINUTES` | `60` gives hourly updates. Lower values make the page fresher but send more requests to Strava. Don't go below `15`. |
| `OPENSTRIDE_SCAN_HOUR` | The hour of the nightly scan, in `OPENSTRIDE_TIMEZONE` |
### 3. Log in once, on a machine with a screen

The container can't open a login window. Log in on your own computer and save the session into
`./data/`:

```bash
mkdir -p data                     # Windows PowerShell: mkdir data
pip install openstride
python -m playwright install chromium
openstride login --session-file data/auth_state.json
```

If the server is a different machine, copy `data/auth_state.json` to the same path there, for example
with `scp`. **The file logs anyone in as you, so copy it over a secure channel.**

On Linux, the container runs as uid 1000. If your user has a different uid, give it access to the
folder:

```bash
sudo chown -R 1000:1000 data
```

### 4. Start

```bash
docker compose up -d
```

The first run builds the image, which takes a few minutes because it downloads Chromium. Then open
**http://localhost:8000**. The first sync starts straight away.

### 5. Optional: backfill since the challenge start

```bash
docker compose run --rm dashboard python -m dashboard sync --setup
```

This reads every member's profile weeks since `OPENSTRIDE_CHALLENGE_START`. If Strava rate-limits it,
everything fetched so far is kept, and the nightly scan continues from there.

## Everyday tasks

| Task | Command |
|---|---|
| Show status and health | `docker compose ps` (the `STATUS` column shows `healthy`) |
| Follow the logs | `docker compose logs -f` (one `sync finished: ...` line per sync) |
| Sync now | `docker compose exec dashboard python -m dashboard sync` |
| Sync now, with the scan | `docker compose exec dashboard python -m dashboard sync --scan` |
| Sync through the API | `curl -X POST -H "Authorization: Bearer $TOKEN" localhost:8000/api/sync` (needs `OPENSTRIDE_API_TOKEN`) |
| Apply `.env` changes | `docker compose up -d`, which recreates the container |
| Update to the latest code | `git pull && docker compose up -d --build` |
| Stop | `docker compose down`. The data in `./data` is kept. |
| Back up | Copy the `./data` folder. |

### When the Strava session expires

The dashboard shows `Last sync failed: SessionExpired`. Fix it on a machine with a screen:

```bash
openstride login --session-file data/auth_state.json
```

Then copy the file to the server's `./data/` if needed. **Nothing needs restarting:** the next sync loads
the new session.

### Change the port

In `docker-compose.yml`, edit the host side of `ports`. For example, `"8080:8000"` serves the dashboard
on port 8080. To make it reachable only from the machine itself, bind it to localhost:
`"127.0.0.1:8000:8000"`.

### Time zone

`OPENSTRIDE_TIMEZONE` controls everything the dashboard shows. The container's system clock stays in UTC,
which is fine, so log timestamps are in UTC.

## Without Compose

```bash
docker build -t openstride-dashboard .
docker run -d --name openstride --restart unless-stopped \
  -p 8000:8000 -v "$PWD/data:/data" --env-file .env openstride-dashboard
```

In Windows PowerShell, use `-v "${PWD}/data:/data"`.

`docker run --env-file` and Compose `env_file` read the file the same way. Inline `# comments` after a
value are **not** stripped by `docker run --env-file`, so remove them from `.env` if you use this form.

## Security

- **`data/auth_state.json` is a Strava login.** `.dockerignore` keeps `data/` and `.env` out of the
  image. Never `COPY` them in, and never push an image that contains them.
- **The page has no login.** Anyone who can reach port 8000 can see member names and totals. Put it
  behind a reverse proxy with HTTPS, such as Caddy, Traefik or nginx, and add authentication if the club
  is private.
- **`/api/v3/*` exposes more than the page**, such as activity descriptions and locations. It stays off
  until you set `OPENSTRIDE_API_TOKEN`. Use a long random token.
- The container runs as an unprivileged user, and stores everything in the `/data` volume.

## Troubleshooting

| Symptom | Fix |
|---|---|
| `config error: OPENSTRIDE_CLUB_ID is required` in the logs | `.env` is missing, or isn't next to `docker-compose.yml`. |
| `SessionExpired` and no `auth_state.json` | Do [step 3](#3-log-in-once-on-a-machine-with-a-screen). Check that the file is in `./data/`, not the repository root. |
| `PermissionError: '/data/...'` | On Linux, run `sudo chown -R 1000:1000 data`. |
| The container shows `unhealthy` | Check `docker compose logs`. Usually there's a config error, or the port is already taken. |
| `RateLimited` or HTTP 429 | Raise `OPENSTRIDE_SYNC_MINUTES`, and don't run a `--setup` scan during a busy period. |
| The build fails at `playwright install --with-deps` | The build needs internet access to download Chromium and its system packages. Retry, or check your proxy. |

Other errors are covered in [dashboard troubleshooting](../dashboard/README.md#troubleshooting).

---

To change the image, see the [developer guide](DeveloperGuide.md).
