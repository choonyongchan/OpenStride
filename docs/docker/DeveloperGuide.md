# Docker developer guide

This guide explains how the image is built, and how to change it. For how to run it, see the
[Docker README](README.md).

## Files

| File | Role |
|---|---|
| `Dockerfile` | Builds `openstride-dashboard`: Python, the library, the dashboard, Chromium, an unprivileged user and a health check |
| `docker-compose.yml` | One `dashboard` service: builds the image, maps port 8000, reads `.env`, mounts `./data` at `/data`, `restart: unless-stopped` |
| `.dockerignore` | Keeps `.git`, `.venv`, `tests`, `data`, `.env` and build output out of the build context |

The image contains no application code of its own. It runs `python -m dashboard serve`, so every change
to behaviour belongs in `src/` or `dashboard/`. See the [library](../library/DeveloperGuide.md) and
[dashboard](../dashboard/DeveloperGuide.md) developer guides.

## The Dockerfile, step by step

```dockerfile
FROM python:3.14-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 \
    PLAYWRIGHT_BROWSERS_PATH=/ms-playwright \
    OPENSTRIDE_DATA_DIR=/data
```

- `PYTHONUNBUFFERED=1` sends `sync finished:` lines to `docker logs` straight away.
- `PLAYWRIGHT_BROWSERS_PATH=/ms-playwright` installs Chromium in a fixed, shared location instead of
  under root's home folder, so the `app` user can find it at runtime.
- `OPENSTRIDE_DATA_DIR=/data` points the dashboard at the volume. The session file defaults to
  `/data/auth_state.json`.

```dockerfile
COPY pyproject.toml README.md LICENSE requirements-dashboard.txt ./
COPY src ./src
RUN pip install --no-cache-dir . -r requirements-dashboard.txt \
 && python -m playwright install --with-deps chromium \
 && rm -rf /var/lib/apt/lists/*
COPY dashboard ./dashboard
```

**The layer order is deliberate.** The slow layer, which installs Chromium and its system packages,
only depends on the library and the requirements. Editing `dashboard/` only rebuilds the last `COPY`, so
it takes seconds. Editing `src/` reinstalls everything, because the library is installed as a package
(`pip install .`), not in editable mode. `README.md` and `LICENSE` are copied because the hatch build
reads them.

```dockerfile
RUN useradd --create-home --uid 1000 app && mkdir -p /data && chown app:app /data
USER app
VOLUME ["/data"]
EXPOSE 8000
HEALTHCHECK --interval=60s --timeout=5s --start-period=20s \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/api/club', timeout=4)"
CMD ["python", "-m", "dashboard", "serve"]
```

- The app runs as uid 1000, not root. Chromium is started with its normal sandbox settings.
- The health check uses the standard library (`urllib`), because the slim image has no `curl`.
  `/api/club` only reads `status.json`, so the check is cheap and never contacts Strava.

## Build and test locally

```bash
docker compose build                                   # or: docker build -t openstride-dashboard .
docker compose up                                      # foreground, logs in the terminal
docker compose run --rm dashboard python -m dashboard sync   # one sync, then exit
docker compose run --rm dashboard python -c "import openstride; print(openstride.__version__)"
```

To work quickly on the dashboard code without rebuilding, mount the source over the copy in the image:

```yaml
# docker-compose.override.yml (don't commit)
services:
  dashboard:
    volumes:
      - ./data:/data
      - ./dashboard:/app/dashboard
```

Then run `docker compose restart` after each Python change. Static files in `dashboard/static/` update on
the next page reload.

To check a sync works without Strava, run the e2e suite on the host instead
(`python -m pytest tests/e2e`). It uses the same Chromium code path, against a fake site.

## Common changes

### Change the Python version

1. Edit `FROM python:3.X-slim`.
2. Keep it within the versions CI tests: `python: ["3.11", "3.14"]` in `.github/workflows/ci.yml`. If
   you move outside them, add the new version to CI first.
3. Rebuild with `--no-cache` so pip and Playwright install again.

### Change the Chromium version

Chromium's version is set by the installed `playwright` package. To update it, change the version range
in `pyproject.toml` (`playwright>=1.45`), then rebuild. Playwright downloads the matching browser.

### Change a default setting for the image

Add it to the `ENV` line, such as `OPENSTRIDE_SYNC_MINUTES=30`. Values in `.env` still override it at
runtime. Only use `ENV` for values that suit every deployment; site-specific values belong in `.env`.

### Change the scrape schedule

The scraping schedule isn't set in Docker. The process inside the container schedules itself through
`OPENSTRIDE_SYNC_MINUTES` and `OPENSTRIDE_SCAN_HOUR`, which are set in `.env`. Don't add cron to the
image: it would start a second browser alongside the scheduler. To change how scheduling works, edit
`dashboard/scheduler.py`.

### Add a service

For example, to add a reverse proxy, add a second service in `docker-compose.yml` and keep the dashboard
internal:

```yaml
services:
  dashboard:
    build: .
    env_file: .env
    volumes: ["./data:/data"]
    expose: ["8000"]              # internal only
    restart: unless-stopped
  caddy:
    image: caddy:2
    ports: ["80:80", "443:443"]
    volumes: ["./Caddyfile:/etc/caddy/Caddyfile"]
    restart: unless-stopped
```

With a `Caddyfile` such as `club.example.com { reverse_proxy dashboard:8000 }`.

## Rules

- **Never put secrets in the image.** `.env` and `data/` stay in `.dockerignore`. Don't use `ARG` or
  `ENV` for tokens.
- **Keep the slow layer first.** Copy fast-changing paths after `playwright install`.
- **Keep it a single process.** The scheduler runs in the same process as the API, and Playwright is
  bound to its thread. Don't split sync and serve into separate containers that share `/data` while a
  sync runs: the file lock (`store.LOCK`) only works within one process.
- **Keep the user unprivileged.** If a tool needs root, install it before `USER app`.
- **Keep `.dockerignore` up to date.** When you add a folder that holds local or secret data, add it
  there too.
