# openstride dashboard: FastAPI backend + static frontend + Chromium for the scraper.
#   docker build -t openstride-dashboard .
#   docker run -p 8000:8000 -v ./data:/data --env-file .env openstride-dashboard
FROM python:3.14-slim

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 \
    PLAYWRIGHT_BROWSERS_PATH=/ms-playwright \
    OPENSTRIDE_DATA_DIR=/data

WORKDIR /app

# Dependencies first, so code edits don't reinstall Chromium.
COPY pyproject.toml README.md LICENSE requirements-dashboard.txt ./
COPY src ./src
RUN pip install --no-cache-dir . -r requirements-dashboard.txt \
 && python -m playwright install --with-deps chromium \
 && rm -rf /var/lib/apt/lists/*

COPY dashboard ./dashboard

RUN useradd --create-home --uid 1000 app && mkdir -p /data && chown app:app /data
USER app
VOLUME ["/data"]
EXPOSE 8000

HEALTHCHECK --interval=60s --timeout=5s --start-period=20s \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/api/club', timeout=4)"

CMD ["python", "-m", "dashboard", "serve"]
