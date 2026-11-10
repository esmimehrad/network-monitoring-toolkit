"""Entry point for the Network Monitoring Toolkit.

Phase 6: checks the Python environment, validates the device inventory, then
runs DNS, ICMP (ping) and TCP checks on many devices at the same time and
reports which devices are reachable. Usage:

    python main.py                         # uses config/inventory.yaml
    python main.py path/to/inventory.yaml  # uses another inventory file

Exit codes:
    0  every check succeeded (or was skipped)
    1  the inventory is invalid, so nothing was checked
    2  monitoring ran, but at least one check failed

In Phase 9 this file will hand control to the command-line interface in
network_monitor.cli.
"""

import sys
from pathlib import Path

from network_monitor import __version__
from network_monitor.icmp_monitor import DEFAULT_PING_TIMEOUT_SECONDS
from network_monitor.inventory import InventoryError, load_inventory
from network_monitor.models import CheckResult, CheckStatus, DeviceReport
from network_monitor.scheduler import DEFAULT_MAX_WORKERS, run_checks
from network_monitor.tcp_monitor import DEFAULT_TIMEOUT_SECONDS

MIN_PYTHON = (3, 12)
DEFAULT_INVENTORY = Path(__file__).resolve().parent / "config" / "inventory.yaml"
MAX_WORKERS = DEFAULT_MAX_WORKERS

EXIT_OK = 0
EXIT_INVALID_INVENTORY = 1
EXIT_CHECKS_FAILED = 2


def format_result(result: CheckResult) -> str:
    """Return one aligned line describing a check result for the terminal."""
    if result.is_success:
        tag = "OK"
    elif result.status == CheckStatus.SKIPPED:
        tag = "SKIP"
    else:
        tag = "FAIL"

    port = str(result.port) if result.port is not None else "-"
    timing = f"{result.response_time_ms:.1f} ms" if result.response_time_ms is not None else "-"
    message = result.error or result.detail or ""

    line = (
        f"  [{tag:<4}] {result.device_name:<20} {result.check_type:<4} {port:<6} "
        f"{result.status:<12} {timing:>9}  {message}"
    )
    return line.rstrip()


def print_device_report(report: DeviceReport) -> None:
    """Print one device's results as soon as that device has finished."""
    if report.error is not None:
        print(f"  [ERR ] {report.device.name:<20} checks crashed: {report.error}")
        return
    for result in report.results:
        print(format_result(result))


def main() -> int:
    """Validate the inventory, run all checks concurrently and return an exit code."""
    running = sys.version.split()[0]

    if sys.version_info < MIN_PYTHON:
        print(f"ERROR: Python {MIN_PYTHON[0]}.{MIN_PYTHON[1]}+ is required, found {running}")
        return EXIT_INVALID_INVENTORY

    print(f"Network Monitoring Toolkit v{__version__}")
    print(f"Python {running} - environment OK")
    print(f"Interpreter: {sys.executable}")
    print()

    inventory_path = Path(sys.argv[1]) if len(sys.argv) > 1 else DEFAULT_INVENTORY

    try:
        devices = load_inventory(inventory_path)
    except InventoryError as exc:
        print(f"Inventory INVALID: {inventory_path}")
        for message in exc.errors:
            print(f"  - {message}")
        print(f"{len(exc.errors)} problem(s) found. Fix them and run again.")
        return EXIT_INVALID_INVENTORY

    print(f"Inventory OK: {len(devices)} device(s) loaded from {inventory_path}")
    print()
    print(
        f"Checking {len(devices)} device(s) with up to {MAX_WORKERS} worker thread(s) "
        f"(ping timeout {DEFAULT_PING_TIMEOUT_SECONDS:g} s, "
        f"TCP timeout {DEFAULT_TIMEOUT_SECONDS:g} s)..."
    )
    print("Results appear as each device finishes:")

    run = run_checks(devices, max_workers=MAX_WORKERS, on_device_done=print_device_report)

    print()
    print("Device reachability (any ICMP reply or TCP answer counts as reachable):")
    for report in run.reports:
        if report.error is not None:
            state = "ERROR"
        elif report.reachable:
            state = "UP"
        else:
            state = "DOWN"
        print(f"  {report.device.name:<20} {state}")

    print()
    print(f"Devices: {run.reachable_devices}/{len(run.reports)} reachable")
    print(
        f"Checks: {run.total_checks} total, {run.succeeded_checks} succeeded, "
        f"{run.failed_checks} failed, {run.skipped_checks} skipped"
    )
    print(f"Completed in {run.duration_seconds:.2f} s using {run.workers} worker thread(s)")

    return EXIT_CHECKS_FAILED if run.has_failures else EXIT_OK


if __name__ == "__main__":
    sys.exit(main())
