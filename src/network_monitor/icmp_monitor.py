"""ICMP echo (ping) checks.

Sending raw ICMP packets from Python needs administrator rights, so this module
runs the operating system's own ``ping`` command instead. It does so safely:

* the command is a list of arguments, so no shell ever interprets it;
* the host was validated by inventory.py, and is checked again here;
* a hard timeout stops a ping process that hangs.

ICMP is often blocked by firewalls, so a failed ping is NOT proof that a device
is down. connectivity.py always runs the TCP checks as well.
"""

import math
import os
import platform
import re
import shutil
import subprocess
from typing import Any

from network_monitor.models import CheckResult, CheckStatus, CheckType, Device

DEFAULT_PING_TIMEOUT_SECONDS = 2.0

# Extra time allowed for the ping process itself to start and exit.
PROCESS_GRACE_SECONDS = 2.0

# Matches "time=0.045 ms" (Linux, macOS), "time=14ms" and "time<1ms" (Windows).
ROUND_TRIP_PATTERN = re.compile(r"time[=<]\s*([\d.]+)\s*ms", re.IGNORECASE)

NOT_PERMITTED_HINTS = ("operation not permitted", "permission denied")
NAME_NOT_FOUND_HINTS = (
    "could not find host",
    "name or service not known",
    "unknown host",
    "cannot resolve",
)
UNREACHABLE_HINTS = ("unreachable",)


def build_ping_command(ping_path: str, host: str, timeout: float, system: str) -> list[str]:
    """Return the arguments for sending one ping with a timeout on the given operating system."""
    if host.startswith("-"):
        raise ValueError(f"Refusing to ping {host!r}: a host must not start with '-'")

    if system == "Windows":
        # -n count, -w timeout in milliseconds
        return [ping_path, "-n", "1", "-w", str(int(timeout * 1000)), host]
    if system == "Darwin":
        # macOS: -c count, -W timeout in milliseconds
        return [ping_path, "-c", "1", "-W", str(int(timeout * 1000)), host]
    # Linux: -c count, -W timeout in whole seconds (at least 1)
    return [ping_path, "-c", "1", "-W", str(max(1, math.ceil(timeout))), host]


def parse_round_trip_ms(output: str) -> float | None:
    """Return the round-trip time in milliseconds from ping output, or None if there is none.

    Windows prints "time<1ms" for very fast replies; that is reported as 1.0.
    """
    match = ROUND_TRIP_PATTERN.search(output)
    if match is None:
        return None
    return float(match.group(1))


def check_icmp(device: Device, timeout: float = DEFAULT_PING_TIMEOUT_SECONDS) -> CheckResult:
    """Send one ping to the device and return the outcome as a CheckResult."""
    ping_path = shutil.which("ping")
    if ping_path is None:
        return _icmp_result(
            device,
            CheckStatus.SKIPPED,
            detail="ping command is not available in this environment",
        )

    command = build_ping_command(ping_path, device.host, timeout, platform.system())

    try:
        completed = subprocess.run(
            command,
            capture_output=True,
            text=True,
            errors="replace",
            timeout=timeout + PROCESS_GRACE_SECONDS,
            check=False,
            env={**os.environ, "LC_ALL": "C"},
        )
    except subprocess.TimeoutExpired:
        return _icmp_result(
            device,
            CheckStatus.TIMEOUT,
            error=f"ping did not finish within {timeout + PROCESS_GRACE_SECONDS:g} seconds",
        )
    except OSError as exc:
        return _icmp_result(device, CheckStatus.SKIPPED, detail=f"Could not run ping: {exc}")

    output = f"{completed.stdout}\n{completed.stderr}"
    lowered = output.lower()
    round_trip_ms = parse_round_trip_ms(output)

    if completed.returncode == 0 and round_trip_ms is not None:
        return _icmp_result(
            device,
            CheckStatus.SUCCESS,
            response_time_ms=round_trip_ms,
            detail="ICMP echo reply received",
        )
    if _contains_any(lowered, NOT_PERMITTED_HINTS):
        return _icmp_result(
            device,
            CheckStatus.SKIPPED,
            detail="ICMP is not permitted in this environment",
        )
    if _contains_any(lowered, NAME_NOT_FOUND_HINTS):
        return _icmp_result(
            device,
            CheckStatus.DNS_FAILURE,
            error="ping could not resolve the host name",
        )
    if _contains_any(lowered, UNREACHABLE_HINTS):
        return _icmp_result(
            device,
            CheckStatus.UNREACHABLE,
            error="Destination unreachable - reported by a router on the path",
        )
    return _icmp_result(
        device,
        CheckStatus.TIMEOUT,
        error=f"No ICMP reply within {timeout:g} seconds - ICMP may be blocked by a firewall",
    )


def _contains_any(text: str, hints: tuple[str, ...]) -> bool:
    """True if any of the hint phrases appears in the text."""
    return any(hint in text for hint in hints)


def _icmp_result(device: Device, status: CheckStatus, **details: Any) -> CheckResult:
    """Build an ICMP CheckResult for this device; extra fields are passed through."""
    return CheckResult(
        device_name=device.name,
        host=device.host,
        check_type=CheckType.ICMP,
        status=status,
        **details,
    )
