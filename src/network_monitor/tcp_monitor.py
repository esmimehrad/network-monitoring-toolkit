"""TCP port checks.

A TCP check answers one question: "can we complete a TCP three-way handshake
with this host on this port, and how long does it take?" Each kind of failure
is mapped to its own CheckStatus so an operator can see *why* it failed.
"""

import socket
import time
from typing import Any

from network_monitor.models import CheckResult, CheckStatus, CheckType, Device

DEFAULT_TIMEOUT_SECONDS = 3.0


def check_tcp_port(
    device: Device,
    port: int,
    timeout: float = DEFAULT_TIMEOUT_SECONDS,
) -> CheckResult:
    """Try to open a TCP connection to device.host:port and return the outcome."""
    start = time.perf_counter()
    try:
        with socket.create_connection((device.host, port), timeout=timeout):
            elapsed_ms = (time.perf_counter() - start) * 1000
    except TimeoutError:
        return _tcp_result(
            device,
            port,
            CheckStatus.TIMEOUT,
            error=f"No response within {timeout:g} seconds",
        )
    except ConnectionRefusedError:
        return _tcp_result(
            device,
            port,
            CheckStatus.REFUSED,
            error="Connection refused - the host answered but nothing is listening on this port",
        )
    except socket.gaierror as exc:
        return _tcp_result(
            device,
            port,
            CheckStatus.DNS_FAILURE,
            error=f"DNS lookup failed: {exc.strerror or exc}",
        )
    except OSError as exc:
        return _tcp_result(
            device,
            port,
            CheckStatus.UNREACHABLE,
            error=f"Network error: {exc.strerror or exc}",
        )

    return _tcp_result(device, port, CheckStatus.SUCCESS, response_time_ms=round(elapsed_ms, 2))


def _tcp_result(device: Device, port: int, status: CheckStatus, **details: Any) -> CheckResult:
    """Build a TCP CheckResult for this device and port; extra fields are passed through."""
    return CheckResult(
        device_name=device.name,
        host=device.host,
        check_type=CheckType.TCP,
        status=status,
        port=port,
        **details,
    )
