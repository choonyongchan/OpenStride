"""A background thread that syncs on schedule: the light sync every OPENSTRIDE_SYNC_MINUTES, plus the profile scan
on the first sync at or after OPENSTRIDE_SCAN_HOUR each day. A manual request (POST /api/sync) runs at the next tick.
Each sync opens its own browser in this thread (Playwright's sync API is bound to one thread)."""
from __future__ import annotations

import threading
import traceback
from datetime import datetime

from openstride import OpenStrideError

from .config import Settings
from .store import Store, now_utc
from .sync import SyncIncomplete, run_sync


class Scheduler:
    def __init__(self, settings: Settings, sync=run_sync):
        self.s = settings
        self.store = Store(settings.data_dir)
        self._sync = sync
        self._wake = threading.Event()
        self._stop = threading.Event()
        self._requested: dict | None = None
        self._thread: threading.Thread | None = None
        self.running = False

    def start(self) -> None:
        self._thread = threading.Thread(target=self._loop, name="openstride-sync", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        self._wake.set()

    def request(self, scan: bool = False, setup: bool = False) -> bool:
        """Ask for a sync now; False if one is already running."""
        if self.running:
            return False
        self._requested = {"scan": scan, "setup": setup}
        self._wake.set()
        return True

    def scan_due(self, now: datetime) -> bool:
        """The profile scan runs once a day, on the first sync at or after scan_hour."""
        if self.s.scan_hour is None or now.hour < self.s.scan_hour:
            return False
        return self.store.read_status().get("last_scan_date") != now.date().isoformat()

    def run_once(self, scan: bool = False, setup: bool = False) -> dict:
        """One sync with its outcome written to status.json; the status."""
        now = datetime.now(self.s.tz)
        scan = scan or setup or self.scan_due(now)
        self.running = True
        self.store.update_status(running=True, started_at=now_utc(), scan=scan)
        try:
            summary = self._sync(self.s, scan=scan, setup=setup)
            fields = {"last_success": now_utc(), "last_error": "", "summary": summary}
            if scan:
                fields["last_scan_date"] = now.date().isoformat()
        except OpenStrideError as e:
            fields = {"last_error": f"{type(e).__name__}: {e}"}
            if scan and isinstance(e, SyncIncomplete):   # partial scan saved; don't retry it hourly into a 429
                fields["last_scan_date"] = now.date().isoformat()
        except Exception as e:   # keep the server up; the error shows on the dashboard
            traceback.print_exc()
            fields = {"last_error": f"{type(e).__name__}: {e}"}
        finally:
            self.running = False
        return self.store.update_status(running=False, finished_at=now_utc(), **fields)

    def _loop(self) -> None:
        interval = self.s.sync_minutes * 60
        while not self._stop.is_set():
            if interval > 0 or self._requested is not None:
                req, self._requested = self._requested or {}, None
                status = self.run_once(**req)
                print(f"sync finished: {status.get('last_error') or status.get('summary')}", flush=True)
            self._wake.wait(timeout=interval if interval > 0 else None)
            self._wake.clear()
