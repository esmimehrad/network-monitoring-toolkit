"""Tests for the SQLite monitoring history.

Every test gets a brand-new database file in a temporary folder, so tests never
see each other's data and never touch data/monitoring.db.
"""

import sqlite3
from datetime import UTC, datetime, timedelta

import pytest

from network_monitor.database import MonitoringDatabase
from network_monitor.models import (
    CheckResult,
    CheckStatus,
    CheckType,
    Criticality,
    Device,
    DeviceReport,
    Environment,
    MonitoringRun,
)

START = datetime(2026, 11, 9, 12, 0, 0, tzinfo=UTC)

ROUTER = Device(
    name="core-router-01",
    host="192.168.1.1",
    device_type="router",
    environment=Environment.PRODUCTION,
    criticality=Criticality.HIGH,
    ports=(22,),
)
SERVER = Device(
    name="web-server-01",
    host="web-01.example.local",
    device_type="server",
    environment=Environment.DEVELOPMENT,
    criticality=Criticality.MEDIUM,
    ports=(443,),
)


@pytest.fixture
def database(tmp_path):
    """An empty database in a temporary folder, closed automatically after the test."""
    with MonitoringDatabase(tmp_path / "monitoring.db") as db:
        yield db


def make_run(started_at: datetime = START, router_up: bool = True) -> MonitoringRun:
    """Build a two-device run: the router (up or down) and a reachable web server."""
    router_status = CheckStatus.SUCCESS if router_up else CheckStatus.TIMEOUT
    router_results = (
        CheckResult(
            ROUTER.name,
            ROUTER.host,
            CheckType.ICMP,
            router_status,
            response_time_ms=1.5 if router_up else None,
            timestamp=started_at,
        ),
        CheckResult(
            ROUTER.name,
            ROUTER.host,
            CheckType.TCP,
            router_status,
            port=22,
            error=None if router_up else "No response within 3 seconds",
            timestamp=started_at + timedelta(seconds=1),
        ),
    )
    server_results = (
        CheckResult(
            SERVER.name,
            SERVER.host,
            CheckType.DNS,
            CheckStatus.SUCCESS,
            response_time_ms=0.8,
            detail="203.0.113.10",
            timestamp=started_at,
        ),
    )
    return MonitoringRun(
        reports=(
            DeviceReport(ROUTER, router_results, reachable=router_up),
            DeviceReport(SERVER, server_results, reachable=True),
        ),
        started_at=started_at,
        duration_seconds=3.25,
        workers=2,
    )


# --- Creating the database -----------------------------------------------------


def test_tables_are_created(tmp_path):
    path = tmp_path / "monitoring.db"
    MonitoringDatabase(path).close()

    with sqlite3.connect(path) as connection:
        names = {row[0] for row in connection.execute("SELECT name FROM sqlite_master")}

    assert {"runs", "device_runs", "check_results"} <= names


def test_missing_parent_folder_is_created(tmp_path):
    path = tmp_path / "new" / "folder" / "monitoring.db"

    with MonitoringDatabase(path):
        pass

    assert path.exists()


# --- Saving runs ---------------------------------------------------------------


def test_save_run_returns_increasing_ids(database):
    first = database.save_run(make_run())
    second = database.save_run(make_run(START + timedelta(minutes=5)))

    assert (first, second) == (1, 2)


def test_saved_results_come_back_exactly_the_same(database):
    run = make_run()
    database.save_run(run)

    history = database.get_check_history(ROUTER.name)

    assert list(reversed(history)) == list(run.reports[0].results)


def test_run_summary_is_saved(database):
    database.save_run(make_run(router_up=False))

    [record] = database.get_recent_runs()

    assert record.id == 1
    assert record.started_at == START
    assert record.duration_seconds == 3.25
    assert record.workers == 2
    assert record.device_count == 2
    assert record.reachable_devices == 1
    assert record.total_checks == 3
    assert record.failed_checks == 2


def test_data_survives_closing_and_reopening(tmp_path):
    path = tmp_path / "monitoring.db"
    with MonitoringDatabase(path) as db:
        db.save_run(make_run())

    with MonitoringDatabase(path) as db:
        assert len(db.get_check_history(SERVER.name)) == 1


def test_failed_save_leaves_nothing_behind(database):
    broken_result = CheckResult(None, "10.0.0.1", CheckType.TCP, CheckStatus.SUCCESS, port=22)
    broken_run = MonitoringRun(
        reports=(DeviceReport(ROUTER, (broken_result,), reachable=True),),
        started_at=START,
        duration_seconds=1.0,
        workers=1,
    )

    with pytest.raises(sqlite3.IntegrityError):
        database.save_run(broken_run)

    assert database.get_recent_runs() == []
    assert database.get_device_reachability(ROUTER.name) == []


# --- Reading history -----------------------------------------------------------


def test_history_is_newest_first_and_only_for_that_device(database):
    database.save_run(make_run(START))
    database.save_run(make_run(START + timedelta(hours=1), router_up=False))

    history = database.get_check_history(ROUTER.name)

    assert len(history) == 4
    assert all(result.device_name == ROUTER.name for result in history)
    assert history[0].status == CheckStatus.TIMEOUT
    assert history[0].timestamp > history[-1].timestamp


def test_history_respects_the_limit(database):
    for hour in range(3):
        database.save_run(make_run(START + timedelta(hours=hour)))

    assert len(database.get_check_history(ROUTER.name, limit=4)) == 4
    assert len(database.get_recent_runs(limit=2)) == 2


def test_unknown_device_has_no_history(database):
    database.save_run(make_run())

    assert database.get_check_history("does-not-exist") == []


def test_device_reachability_is_newest_first(database):
    database.save_run(make_run(START, router_up=True))
    database.save_run(make_run(START + timedelta(hours=1), router_up=False))
    database.save_run(make_run(START + timedelta(hours=2), router_up=False))

    assert database.get_device_reachability(ROUTER.name) == [False, False, True]
    assert database.get_device_reachability(SERVER.name, limit=2) == [True, True]


@pytest.mark.parametrize("limit", [0, -5])
def test_invalid_limit_is_rejected(database, limit):
    with pytest.raises(ValueError, match="limit"):
        database.get_check_history(ROUTER.name, limit=limit)


def test_sql_injection_attempt_is_treated_as_plain_text(database):
    database.save_run(make_run())

    history = database.get_check_history("x' OR '1'='1")

    assert history == []
    assert len(database.get_check_history(ROUTER.name)) == 2
