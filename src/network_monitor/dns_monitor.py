"""DNS resolution checks.

A DNS check answers one question: "can this hostname be turned into IP
addresses, and how long does that take?" Hosts written as IP addresses need
no lookup, so their DNS check is recorded as SKIPPED.
"""

import ipaddress
import socket
import time
from typing import Any

from network_monitor.models import CheckResult, CheckStatus, CheckType, Device


def is_ip_address(host: str) -> bool:
    """True if host is a literal IPv4 or IPv6 address rather than a hostname."""
    try:
        ipaddress.ip_address(host)
    except ValueError:
        return False
    return True


def resolve_host(host: str) -> list[str]:
    """Return the unique IP addresses for a hostname, in the order the resolver gave them.

    Raises socket.gaierror if the name cannot be resolved.
    """
    answers = socket.getaddrinfo(host, None, type=socket.SOCK_STREAM)
    addresses = [str(sockaddr[0]) for _family, _type, _proto, _name, sockaddr in answers]
    return list(dict.fromkeys(addresses))


def check_dns(device: Device) -> CheckResult:
    """Resolve the device's hostname and return the outcome as a CheckResult."""
    if is_ip_address(device.host):
        return _dns_result(
            device,
            CheckStatus.SKIPPED,
            detail="Host is an IP address - no DNS lookup needed",
        )

    start = time.perf_counter()
    try:
        addresses = resolve_host(device.host)
    except socket.gaierror as exc:
        return _dns_result(
            device,
            CheckStatus.DNS_FAILURE,
            error=f"DNS lookup failed: {exc.strerror or exc}",
        )
    except OSError as exc:
        return _dns_result(device, CheckStatus.ERROR, error=f"Unexpected DNS error: {exc}")
    elapsed_ms = (time.perf_counter() - start) * 1000

    return _dns_result(
        device,
        CheckStatus.SUCCESS,
        response_time_ms=round(elapsed_ms, 2),
        detail=", ".join(addresses),
    )


def _dns_result(device: Device, status: CheckStatus, **details: Any) -> CheckResult:
    """Build a DNS CheckResult for this device; extra fields are passed through."""
    return CheckResult(
        device_name=device.name,
        host=device.host,
        check_type=CheckType.DNS,
        status=status,
        **details,
    )
