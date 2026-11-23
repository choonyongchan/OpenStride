"""The dashboard command line and the background scheduler, wired to the real store with a fake sync."""
import dataclasses
import threading
from datetime import datetime

import pytest

import dashboard.__main__ as main_mod
from dashboard.scheduler import Scheduler
from dashboard.sync import SyncIncomplete
from openstride import SessionExpired

ENV = {"OPENSTRIDE_CLUB_ID": "1", "OPENSTRIDE_CHALLENGE_START": "2026-09-14"}


@pytest.fixture
def env(monkeypatch, tmp_path):
    for k, v in {**ENV, "OPENSTRIDE_DATA_DIR": str(tmp_path)}.items():
        monkeypatch.setenv(k, v)


def test_config_error_exits_2(monkeypatch, capsys):
    monkeypatch.delenv("OPENSTRIDE_CLUB_ID", raising=False)
    assert main_mod.main(["sync"]) == 2 and "config error" in capsys.readouterr().err


def test_sync_command(env, monkeypatch, capsys):
    calls = []
    monkeypatch.setattr("dashboard.sync.run_sync", lambda s, scan, setup: calls.append((scan, setup)) or {"ok": 1})
    assert main_mod.main(["sync", "--scan"]) == 0 and calls == [(True, False)]
    assert "{'ok': 1}" in capsys.readouterr().out

    def expired(s, scan, setup):
        raise SessionExpired("log in")

    monkeypatch.setattr("dashboard.sync.run_sync", expired)
    assert main_mod.main(["sync"]) == 1 and "SessionExpired: log in" in capsys.readouterr().err


def test_serve_and_login_commands(env, monkeypatch, capsys):
    served = {}
    monkeypatch.setattr("uvicorn.run", lambda app, host, port: served.update(host=host, port=port, app=app))
    assert main_mod.main(["serve", "--port", "9999"]) == 0
    assert served["port"] == 9999 and served["app"].title == "openstride dashboard"
    monkeypatch.setattr(main_mod, "login", lambda path, channel: path)
    assert main_mod.main(["login"]) == 0 and "auth_state.json" in capsys.readouterr().out


def test_run_once_records_success_and_scan_date(settings):
    sched = Scheduler(settings, sync=lambda s, scan, setup: {"scan": scan})
    status = sched.run_once(setup=True)
    assert status["summary"] == {"scan": True} and status["last_error"] == "" and status["last_scan_date"]
    assert not status["running"] and not sched.running


@pytest.mark.parametrize("error,scan_marked", [(SyncIncomplete("429"), True), (SessionExpired("x"), False),
                                                (RuntimeError("bug"), False)])
def test_run_once_records_failures(settings, error, scan_marked):
    def fail(s, scan, setup):
        raise error

    status = Scheduler(settings, sync=fail).run_once(scan=True)
    assert type(error).__name__ in status["last_error"]
    assert ("last_scan_date" in status) is scan_marked


def test_scan_due_once_a_day_after_the_hour(settings):
    sched = Scheduler(settings, sync=lambda s, scan, setup: {})
    hour = settings.scan_hour
    late = datetime(2026, 9, 20, hour, 5, tzinfo=settings.tz)
    assert sched.scan_due(late)
    assert not sched.scan_due(late.replace(hour=hour - 1))
    sched.store.update_status(last_scan_date=late.date().isoformat())
    assert not sched.scan_due(late)


def test_loop_runs_requests_and_stops(settings):
    settings = dataclasses.replace(settings, sync_minutes=0)   # only on demand
    done = threading.Event()
    seen = []

    def sync(s, scan, setup):
        seen.append(setup)
        done.set()
        return {}

    sched = Scheduler(settings, sync=sync)
    sched.start()
    assert sched.request(setup=True)
    assert done.wait(5) and seen == [True]
    sched.running = True
    assert not sched.request()                      # refuse while one is running
    sched.running = False
    sched.stop()
    sched._thread.join(5)
    assert not sched._thread.is_alive()
