"""Entry point for the Network Monitoring Toolkit.

Phase 3: checks the Python environment, then loads and validates the device
inventory. Usage:

    python main.py                         # validates config/inventory.yaml
    python main.py path/to/inventory.yaml  # validates another inventory file

In Phase 9 this file will hand control to the command-line interface in
network_monitor.cli.
"""

import sys
from pathlib import Path

from network_monitor import __version__
from network_monitor.inventory import InventoryError, load_inventory

MIN_PYTHON = (3, 12)
DEFAULT_INVENTORY = Path(__file__).resolve().parent / "config" / "inventory.yaml"


def main() -> int:
    """Run the environment and inventory checks and return an exit code (0 = success)."""
    running = sys.version.split()[0]

    if sys.version_info < MIN_PYTHON:
        print(f"ERROR: Python {MIN_PYTHON[0]}.{MIN_PYTHON[1]}+ is required, found {running}")
        return 1

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
        return 1

    print(f"Inventory OK: {len(devices)} device(s) loaded from {inventory_path}")
    for device in devices:
        ports = ", ".join(str(port) for port in device.ports) or "none"
        print(
            f"  {device.name:<20} {device.host:<22} {device.environment:<12} "
            f"{device.criticality:<7} ports: {ports}"
        )

    return 0


if __name__ == "__main__":
    sys.exit(main())
