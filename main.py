"""Entry point for the Network Monitoring Toolkit.

Phase 1: proves that the virtual environment is active and that the
network_monitor package can be imported. In Phase 9 this file will hand
control to the command-line interface in network_monitor.cli.
"""

import sys

from network_monitor import __version__

MIN_PYTHON = (3, 12)


def main() -> int:
    """Run the environment check and return a process exit code (0 = success)."""
    running = sys.version.split()[0]

    if sys.version_info < MIN_PYTHON:
        print(f"ERROR: Python {MIN_PYTHON[0]}.{MIN_PYTHON[1]}+ is required, found {running}")
        return 1

    print(f"Network Monitoring Toolkit v{__version__}")
    print(f"Python {running} - environment OK")
    print(f"Interpreter: {sys.executable}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
