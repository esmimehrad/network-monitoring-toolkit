"""Tests for run summaries and JSON/CSV report files."""

import csv
import json
from datetime import UTC, datetime

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
from network_monitor.reporting import (
    CSV_COLUMNS,
    format_summary,
    summarize_run,
    write_csv_report,
    write_json_report,
)

START = datetime(2026, 11, 10, 1, 15, 42, tzinfo=UTC)


def make_device(name: str, criticality: Criticality, ports: tuple[int, ...] = (22,)) -> Device:
    """Build a valid Device with the given name, criticality and ports."""
    return Device(
        name=name,
        host=f"{name}.example.local",
        device_type="server",
        environment=Environment.PRODUCTION,
        criticality=criticality,
        ports=ports,
    )


def check(
    device: Device,
    check_type: CheckType,
    status: CheckStatus,
    port: int | None = None,
    response_time_ms: float | None = None,
    error: str | None = None,
) -> CheckResult:
    """Build a CheckResult for the device with a fixed timestamp."""
    return CheckResult(
        device.name,
        device.host,
        check_type,
        status,
        port=port,
        response_time_ms=response_time_ms,
        error=error,
        timestamp=START,
    )


def make_run() -> MonitoringRun:
    """A run with one healthy device, one half-broken device and one dead device."""
    healthy = make_device("web-01", Criticality.LOW, ports=(443,))
    ssh_down = make_device("app-01", Criticality.MEDIUM, ports=(22,))
    dead = make_device("core-router-01", Criticality.HIGH, ports=(22, 443))

    return MonitoringRun(
        reports=(
            DeviceReport(
                healthy,
                (
                    check(healthy, CheckType.DNS, CheckStatus.SUCCESS, response_time_ms=1.0),
                    check(healthy, CheckType.ICMP, CheckStatus.SUCCESS, response_time_ms=2.0),
                    check(healthy, CheckType.TCP, CheckStatus.SUCCESS, 443, response_time_ms=4.0),
                ),
                reachable=True,
            ),
            DeviceReport(
                ssh_down,
                (
                    check(ssh_down, CheckType.DNS, CheckStatus.SUCCESS, response_time_ms=3.0),
                    check(ssh_down, CheckType.ICMP, CheckStatus.SUCCESS, response_time_ms=6.0),
                    check(ssh_down, CheckType.TCP, CheckStatus.REFUSED, 22, error="refused"),
                ),
                reachable=True,
            ),
            DeviceReport(
                dead,
                (
                    check(dead, CheckType.DNS, CheckStatus.SKIPPED),
                    check(dead, CheckType.ICMP, CheckStatus.TIMEOUT, error="no reply"),
                    check(dead, CheckType.TCP, CheckStatus.TIMEOUT, 22, error="timed out"),
                    check(dead, CheckType.TCP, CheckStatus.TIMEOUT, 443, error="timed out"),
                ),
                reachable=False,
            ),
        ),
        started_at=START,
        duration_seconds=8.14,
        workers=3,
    )


# --- Summary numbers -----------------------------------------------------------


def test_summary_counts_devices_and_checks():
    summary = summarize_run(make_run())

    assert summary.total_devices == 3
    assert summary.reachable_devices == 2
    assert summary.availability_percent == 66.7
    assert summary.total_checks == 10
    assert summary.succeeded_checks == 5
    assert summary.failed_checks == 4
    assert summary.skipped_checks == 1


def test_average_response_time_uses_only_successful_timed_checks():
    summary = summarize_run(make_run())

    assert summary.average_response_time_ms == 3.2
    assert summary.average_response_time_ms_by_type == {"dns": 2.0, "icmp": 4.0, "tcp": 4.0}


def test_most_problematic_devices_put_unreachable_and_critical_first():
    summary = summarize_run(make_run())

    names = [device.name for device in summary.problematic_devices]
    assert names == ["core-router-01", "app-01"]
    assert summary.problematic_devices[0].reachable is False
    assert summary.problematic_devices[0].failed_checks == 3


def test_failed_ports_lists_tcp_failures_only():
    summary = summarize_run(make_run())

    assert [(port.device_name, port.port, port.status) for port in summary.failed_ports] == [
        ("app-01", 22, "refused"),
        ("core-router-01", 22, "timeout"),
        ("core-router-01", 443, "timeout"),
    ]


def test_crashed_device_is_listed_as_problematic():
    broken = make_device("broken-01", Criticality.LOW)
    run = MonitoringRun(
        reports=(DeviceReport(broken, (), reachable=False, error="RuntimeError: boom"),),
        started_at=START,
        duration_seconds=0.1,
        workers=1,
    )

    summary = summarize_run(run)

    assert summary.problematic_devices[0].error == "RuntimeError: boom"
    assert "RuntimeError: boom" in format_summary(run, summary)


def test_empty_run_has_zero_availability_and_no_average():
    run = MonitoringRun(reports=(), started_at=START, duration_seconds=0.0, workers=1)

    summary = summarize_run(run)

    assert summary.availability_percent == 0.0
    assert summary.average_response_time_ms is None
    assert summary.problematic_devices == ()


# --- Terminal summary ----------------------------------------------------------


def test_format_summary_contains_the_key_lines():
    run = make_run()

    text = format_summary(run, summarize_run(run))

    assert "2/3 (66.7% availability)" in text
    assert "10 total, 5 succeeded, 4 failed, 1 skipped" in text
    assert "3.2 ms overall (dns 2.0 ms, icmp 4.0 ms, tcp 4.0 ms)" in text
    assert "1. core-router-01" in text
    assert "Failed ports:" in text


def test_format_summary_for_a_perfect_run():
    device = make_device("web-01", Criticality.LOW, ports=())
    run = MonitoringRun(
        reports=(
            DeviceReport(
                device,
                (check(device, CheckType.ICMP, CheckStatus.SUCCESS, response_time_ms=1.0),),
                reachable=True,
            ),
        ),
        started_at=START,
        duration_seconds=1.0,
        workers=1,
    )

    text = format_summary(run, summarize_run(run))

    assert "none - every device passed every check" in text


# --- JSON report ---------------------------------------------------------------


def test_json_report_is_written_with_summary_and_devices(tmp_path):
    path = write_json_report(make_run(), tmp_path / "reports")

    assert path.name == "report-20261110-011542.json"
    report = json.loads(path.read_text(encoding="utf-8"))
    assert report["report_format_version"] == 1
    assert report["run"]["started_at"] == "2026-11-10T01:15:42+00:00"
    assert report["summary"]["availability_percent"] == 66.7
    assert [device["name"] for device in report["devices"]] == [
        "web-01",
        "app-01",
        "core-router-01",
    ]
    assert report["devices"][2]["checks"][1]["status"] == "timeout"


def test_existing_report_is_never_overwritten(tmp_path):
    first = write_json_report(make_run(), tmp_path)
    second = write_json_report(make_run(), tmp_path)

    assert first != second
    assert second.name == "report-20261110-011542-1.json"
    assert first.exists() and second.exists()


# --- CSV report ----------------------------------------------------------------


def test_csv_report_has_one_row_per_check(tmp_path):
    path = write_csv_report(make_run(), tmp_path)

    with path.open(newline="", encoding="utf-8") as file:
        rows = list(csv.DictReader(file))

    assert path.suffix == ".csv"
    assert list(rows[0].keys()) == CSV_COLUMNS
    assert len(rows) == 10
    assert rows[5]["device_name"] == "app-01"
    assert rows[5]["status"] == "refused"
    assert rows[5]["port"] == "22"
    assert rows[5]["criticality"] == "medium"


def test_csv_report_neutralises_spreadsheet_formulas(tmp_path):
    device = make_device("web-01", Criticality.LOW)
    run = MonitoringRun(
        reports=(
            DeviceReport(
                device,
                (check(device, CheckType.TCP, CheckStatus.ERROR, 22, error="=HYPERLINK(1)"),),
                reachable=False,
            ),
        ),
        started_at=START,
        duration_seconds=1.0,
        workers=1,
    )

    path = write_csv_report(run, tmp_path)

    with path.open(newline="", encoding="utf-8") as file:
        [row] = list(csv.DictReader(file))
    assert row["error"] == "'=HYPERLINK(1)"
