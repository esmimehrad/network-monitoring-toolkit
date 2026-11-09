"""Entry point for the Network Monitoring Toolkit.

Phase 4: checks the Python environment, validates the device inventory, then
runs DNS and TCP checks against every device. Usage:

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
from network_monitor.connectivity import check_device
from network_monitor.inventory import InventoryError, load_inventory
from network_monitor.models import CheckResult, CheckStatus
from network_monitor.tcp_monitor import DEFAULT_TIMEOUT_SECONDS

MIN_PYTHON = (3, 12)
DEFAULT_INVENTORY = Path(__file__).resolve().parent / "config" / "inventory.yaml"

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


def main() -> int:
    """Validate the inventory, run all checks and return an exit code."""
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
    print(f"Running DNS and TCP checks (TCP timeout {DEFAULT_TIMEOUT_SECONDS:g} s)...")

    results: list[CheckResult] = []
    for device in devices:
        for result in check_device(device):
            print(format_result(result))
            results.append(result)

    succeeded = sum(1 for result in results if result.is_success)
    skipped = sum(1 for result in results if result.status == CheckStatus.SKIPPED)
    total = len(results)
    failed = total - succeeded - skipped

    print()
    print(f"Checks: {total} total, {succeeded} succeeded, {failed} failed, {skipped} skipped")

    return EXIT_CHECKS_FAILED if failed else EXIT_OK


if __name__ == "__main__":
    sys.exit(main())
