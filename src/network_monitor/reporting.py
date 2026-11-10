"""Summarise a monitoring run and write it to JSON and CSV report files.

* summarize_run() calculates the numbers an operator cares about: availability,
  failures, average response times, the most problematic devices and failed ports.
* format_summary() turns that summary into text for the terminal.
* write_json_report() and write_csv_report() save the run to the reports folder.

File names are built only from the run's timestamp, never from inventory data,
so a strange device name can never create a file outside the reports folder.
"""

import csv
import json
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from network_monitor.models import CheckStatus, CheckType, Criticality, MonitoringRun, utc_now

REPORT_FORMAT_VERSION = 1
MAX_PROBLEM_DEVICES = 5

CRITICALITY_RANK = {Criticality.HIGH: 3, Criticality.MEDIUM: 2, Criticality.LOW: 1}

CSV_COLUMNS = [
    "run_started_at",
    "device_name",
    "host",
    "device_type",
    "environment",
    "criticality",
    "check_type",
    "status",
    "port",
    "response_time_ms",
    "error",
    "detail",
    "timestamp",
]

# Spreadsheet programs run cells starting with these characters as formulas.
FORMULA_PREFIXES = ("=", "+", "-", "@")


@dataclass(frozen=True)
class ProblemDevice:
    """A device that had at least one failed check, or could not be reached."""

    name: str
    host: str
    criticality: str
    reachable: bool
    failed_checks: int
    error: str | None


@dataclass(frozen=True)
class FailedPort:
    """A TCP port that did not accept a connection."""

    device_name: str
    host: str
    port: int
    status: str
    error: str | None


@dataclass(frozen=True)
class RunSummary:
    """The key numbers from one monitoring run."""

    total_devices: int
    reachable_devices: int
    availability_percent: float
    total_checks: int
    succeeded_checks: int
    failed_checks: int
    skipped_checks: int
    average_response_time_ms: float | None
    average_response_time_ms_by_type: dict[str, float]
    problematic_devices: tuple[ProblemDevice, ...]
    failed_ports: tuple[FailedPort, ...]


def summarize_run(run: MonitoringRun) -> RunSummary:
    """Calculate the summary numbers for a monitoring run."""
    total_devices = len(run.reports)
    availability = run.reachable_devices / total_devices * 100 if total_devices else 0.0

    timed_results = [
        result
        for result in run.results
        if result.is_success and result.response_time_ms is not None
    ]
    times_by_type: dict[str, list[float]] = {}
    for result in timed_results:
        times_by_type.setdefault(result.check_type.value, []).append(result.response_time_ms)

    problem_devices = []
    for report in run.reports:
        failed = sum(
            1
            for result in report.results
            if not result.is_success and result.status != CheckStatus.SKIPPED
        )
        if failed or not report.reachable or report.error:
            problem_devices.append(
                ProblemDevice(
                    name=report.device.name,
                    host=report.device.host,
                    criticality=report.device.criticality.value,
                    reachable=report.reachable,
                    failed_checks=failed,
                    error=report.error,
                )
            )

    # Unreachable first, then most critical, then most failures, then by name.
    problem_devices.sort(
        key=lambda device: (
            device.reachable,
            -CRITICALITY_RANK[Criticality(device.criticality)],
            -device.failed_checks,
            device.name,
        )
    )

    failed_ports = tuple(
        FailedPort(
            device_name=result.device_name,
            host=result.host,
            port=result.port,
            status=result.status.value,
            error=result.error,
        )
        for result in run.results
        if result.check_type == CheckType.TCP
        and result.port is not None
        and not result.is_success
        and result.status != CheckStatus.SKIPPED
    )

    return RunSummary(
        total_devices=total_devices,
        reachable_devices=run.reachable_devices,
        availability_percent=round(availability, 1),
        total_checks=run.total_checks,
        succeeded_checks=run.succeeded_checks,
        failed_checks=run.failed_checks,
        skipped_checks=run.skipped_checks,
        average_response_time_ms=_average([r.response_time_ms for r in timed_results]),
        average_response_time_ms_by_type={
            check_type: _average(times) for check_type, times in sorted(times_by_type.items())
        },
        problematic_devices=tuple(problem_devices[:MAX_PROBLEM_DEVICES]),
        failed_ports=failed_ports,
    )


def format_summary(run: MonitoringRun, summary: RunSummary) -> str:
    """Return a multi-line, human-readable summary for the terminal."""
    started = run.started_at.strftime("%Y-%m-%d %H:%M:%S UTC")
    lines = [
        "=" * 60,
        "Monitoring summary",
        "=" * 60,
        f"Run started:        {started} ({run.duration_seconds:.2f} s, {run.workers} workers)",
        f"Devices monitored:  {summary.total_devices}",
        f"Devices reachable:  {summary.reachable_devices}/{summary.total_devices} "
        f"({summary.availability_percent:.1f}% availability)",
        f"Checks:             {summary.total_checks} total, {summary.succeeded_checks} succeeded, "
        f"{summary.failed_checks} failed, {summary.skipped_checks} skipped",
        f"Avg response time:  {_format_averages(summary)}",
        "",
        "Most problematic devices:",
    ]

    if summary.problematic_devices:
        for number, device in enumerate(summary.problematic_devices, start=1):
            state = "UP" if device.reachable else "DOWN"
            reason = device.error or f"{device.failed_checks} failed check(s)"
            lines.append(
                f"  {number}. {device.name:<20} {state:<5} {device.criticality:<7} {reason}"
            )
    else:
        lines.append("  none - every device passed every check")

    lines.append("")
    lines.append("Failed ports:")
    if summary.failed_ports:
        for failed in summary.failed_ports:
            lines.append(
                f"  {failed.device_name:<20} {failed.host:<22} {failed.port:<6} {failed.status}"
            )
    else:
        lines.append("  none")

    return "\n".join(lines)


def build_json_report(run: MonitoringRun, summary: RunSummary) -> dict[str, Any]:
    """Return the full report as a dictionary that can be saved as JSON."""
    return {
        "report_format_version": REPORT_FORMAT_VERSION,
        "generated_at": utc_now().isoformat(),
        "run": {
            "started_at": run.started_at.isoformat(),
            "duration_seconds": run.duration_seconds,
            "workers": run.workers,
        },
        "summary": asdict(summary),
        "devices": [
            {
                "name": report.device.name,
                "host": report.device.host,
                "type": report.device.device_type,
                "environment": report.device.environment.value,
                "criticality": report.device.criticality.value,
                "reachable": report.reachable,
                "error": report.error,
                "checks": [result.to_dict() for result in report.results],
            }
            for report in run.reports
        ],
    }


def write_json_report(run: MonitoringRun, directory: Path) -> Path:
    """Write the run as a JSON report into directory and return the file path."""
    report = build_json_report(run, summarize_run(run))
    path = _new_report_path(run, directory, ".json")
    path.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    return path


def write_csv_report(run: MonitoringRun, directory: Path) -> Path:
    """Write every check result as one CSV row into directory and return the file path."""
    path = _new_report_path(run, directory, ".csv")
    run_started_at = run.started_at.isoformat()

    with path.open("w", newline="", encoding="utf-8") as file:
        writer = csv.DictWriter(file, fieldnames=CSV_COLUMNS)
        writer.writeheader()
        for report in run.reports:
            for result in report.results:
                row = {
                    "run_started_at": run_started_at,
                    "device_type": report.device.device_type,
                    "environment": report.device.environment.value,
                    "criticality": report.device.criticality.value,
                    **result.to_dict(),
                }
                writer.writerow({column: _safe_csv_value(row[column]) for column in CSV_COLUMNS})

    return path


def _new_report_path(run: MonitoringRun, directory: Path, suffix: str) -> Path:
    """Return a path like reports/report-20261110-011542.json that does not exist yet."""
    directory.mkdir(parents=True, exist_ok=True)
    stem = f"report-{run.started_at.strftime('%Y%m%d-%H%M%S')}"
    path = directory / f"{stem}{suffix}"
    counter = 1
    while path.exists():
        path = directory / f"{stem}-{counter}{suffix}"
        counter += 1
    return path


def _safe_csv_value(value: Any) -> Any:
    """Stop spreadsheet programs from running text values as formulas (CSV injection)."""
    if isinstance(value, str) and value.startswith(FORMULA_PREFIXES):
        return "'" + value
    return value


def _average(values: list[float]) -> float | None:
    """Return the average rounded to 2 decimals, or None for an empty list."""
    if not values:
        return None
    return round(sum(values) / len(values), 2)


def _format_averages(summary: RunSummary) -> str:
    """Return text like '1.1 ms overall (dns 0.8 ms, icmp 1.0 ms, tcp 1.3 ms)'."""
    if summary.average_response_time_ms is None:
        return "no successful timed checks"
    by_type = ", ".join(
        f"{check_type} {average:.1f} ms"
        for check_type, average in summary.average_response_time_ms_by_type.items()
    )
    return f"{summary.average_response_time_ms:.1f} ms overall ({by_type})"
