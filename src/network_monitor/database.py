"""Store monitoring results in SQLite and read the history back.

SQLite keeps the whole database in one file (data/monitoring.db by default)
and is built into Python, so there is no database server to install.

Design notes:

* Every query uses ``?`` or ``:name`` placeholders. Values are never pasted
  into the SQL text, which makes SQL injection impossible.
* A run is saved in one transaction: either all of it is saved, or, if any
  part fails, none of it is.
* Only the main thread uses the database. Worker threads return their results
  and never touch the connection (see scheduler.py), so no locking is needed.
"""

import sqlite3
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from types import TracebackType
from typing import Self

from network_monitor.models import CheckResult, CheckStatus, CheckType, MonitoringRun

SCHEMA = """
CREATE TABLE IF NOT EXISTS runs (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    started_at        TEXT    NOT NULL,
    duration_seconds  REAL    NOT NULL,
    workers           INTEGER NOT NULL,
    device_count      INTEGER NOT NULL,
    reachable_devices INTEGER NOT NULL,
    total_checks      INTEGER NOT NULL,
    failed_checks     INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS device_runs (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id      INTEGER NOT NULL REFERENCES runs (id),
    device_name TEXT    NOT NULL,
    host        TEXT    NOT NULL,
    device_type TEXT    NOT NULL,
    environment TEXT    NOT NULL,
    criticality TEXT    NOT NULL,
    reachable   INTEGER NOT NULL,
    error       TEXT
);

CREATE TABLE IF NOT EXISTS check_results (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id           INTEGER NOT NULL REFERENCES runs (id),
    device_name      TEXT    NOT NULL,
    host             TEXT    NOT NULL,
    check_type       TEXT    NOT NULL,
    status           TEXT    NOT NULL,
    port             INTEGER,
    response_time_ms REAL,
    error            TEXT,
    detail           TEXT,
    checked_at       TEXT    NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_check_results_device
    ON check_results (device_name, checked_at);

CREATE INDEX IF NOT EXISTS idx_device_runs_device
    ON device_runs (device_name, run_id);
"""


@dataclass(frozen=True)
class RunRecord:
    """Summary of one saved monitoring run, as read back from the database."""

    id: int
    started_at: datetime
    duration_seconds: float
    workers: int
    device_count: int
    reachable_devices: int
    total_checks: int
    failed_checks: int


class MonitoringDatabase:
    """An open connection to the monitoring history database.

    Use it with ``with`` so the connection is always closed:

        with MonitoringDatabase("data/monitoring.db") as database:
            run_id = database.save_run(run)
    """

    def __init__(self, path: Path | str) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._connection = sqlite3.connect(self.path)
        self._connection.row_factory = sqlite3.Row
        self._connection.execute("PRAGMA foreign_keys = ON")
        self._connection.executescript(SCHEMA)

    def close(self) -> None:
        """Close the database connection."""
        self._connection.close()

    def __enter__(self) -> Self:
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        self.close()

    def save_run(self, run: MonitoringRun) -> int:
        """Save a whole monitoring run in one transaction and return its run id."""
        with self._connection:
            cursor = self._connection.execute(
                """
                INSERT INTO runs (started_at, duration_seconds, workers, device_count,
                                  reachable_devices, total_checks, failed_checks)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    run.started_at.isoformat(),
                    run.duration_seconds,
                    run.workers,
                    len(run.reports),
                    run.reachable_devices,
                    run.total_checks,
                    run.failed_checks,
                ),
            )
            run_id = cursor.lastrowid

            self._connection.executemany(
                """
                INSERT INTO device_runs (run_id, device_name, host, device_type,
                                         environment, criticality, reachable, error)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                [
                    (
                        run_id,
                        report.device.name,
                        report.device.host,
                        report.device.device_type,
                        report.device.environment.value,
                        report.device.criticality.value,
                        int(report.reachable),
                        report.error,
                    )
                    for report in run.reports
                ],
            )

            self._connection.executemany(
                """
                INSERT INTO check_results (run_id, device_name, host, check_type, status,
                                           port, response_time_ms, error, detail, checked_at)
                VALUES (:run_id, :device_name, :host, :check_type, :status,
                        :port, :response_time_ms, :error, :detail, :timestamp)
                """,
                [{**result.to_dict(), "run_id": run_id} for result in run.results],
            )

        return run_id

    def get_check_history(self, device_name: str, limit: int = 50) -> list[CheckResult]:
        """Return the most recent check results for one device, newest first."""
        _check_limit(limit)
        rows = self._connection.execute(
            """
            SELECT device_name, host, check_type, status, port, response_time_ms,
                   error, detail, checked_at
            FROM check_results
            WHERE device_name = ?
            ORDER BY checked_at DESC, id DESC
            LIMIT ?
            """,
            (device_name, limit),
        ).fetchall()
        return [_row_to_check_result(row) for row in rows]

    def get_recent_runs(self, limit: int = 10) -> list[RunRecord]:
        """Return summaries of the most recent runs, newest first."""
        _check_limit(limit)
        rows = self._connection.execute(
            """
            SELECT id, started_at, duration_seconds, workers, device_count,
                   reachable_devices, total_checks, failed_checks
            FROM runs
            ORDER BY id DESC
            LIMIT ?
            """,
            (limit,),
        ).fetchall()
        return [
            RunRecord(
                id=row["id"],
                started_at=datetime.fromisoformat(row["started_at"]),
                duration_seconds=row["duration_seconds"],
                workers=row["workers"],
                device_count=row["device_count"],
                reachable_devices=row["reachable_devices"],
                total_checks=row["total_checks"],
                failed_checks=row["failed_checks"],
            )
            for row in rows
        ]

    def get_device_reachability(self, device_name: str, limit: int = 10) -> list[bool]:
        """Return whether the device was reachable in its most recent runs, newest first."""
        _check_limit(limit)
        rows = self._connection.execute(
            """
            SELECT reachable
            FROM device_runs
            WHERE device_name = ?
            ORDER BY run_id DESC
            LIMIT ?
            """,
            (device_name, limit),
        ).fetchall()
        return [bool(row["reachable"]) for row in rows]


def _check_limit(limit: int) -> None:
    """Reject limits that make no sense, such as 0 or negative numbers."""
    if limit < 1:
        raise ValueError(f"limit must be at least 1, got {limit}")


def _row_to_check_result(row: sqlite3.Row) -> CheckResult:
    """Rebuild a CheckResult from one database row."""
    return CheckResult(
        device_name=row["device_name"],
        host=row["host"],
        check_type=CheckType(row["check_type"]),
        status=CheckStatus(row["status"]),
        port=row["port"],
        response_time_ms=row["response_time_ms"],
        error=row["error"],
        detail=row["detail"],
        timestamp=datetime.fromisoformat(row["checked_at"]),
    )
