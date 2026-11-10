"""Run every connectivity check for one device, and judge whether it is reachable.

Order matters:

1. DNS runs first. If the hostname cannot be resolved, every other check would
   fail for the same reason, so they are recorded as SKIPPED. The report then
   shows one root cause instead of a wall of duplicate failures.
2. ICMP (ping) runs next. Its result is recorded, but it never stops the TCP
   checks: many networks block ICMP, so a failed ping alone proves nothing.
3. One TCP check runs for every configured port.
"""

from network_monitor.dns_monitor import check_dns
from network_monitor.icmp_monitor import DEFAULT_PING_TIMEOUT_SECONDS, check_icmp
from network_monitor.models import CheckResult, CheckStatus, CheckType, Device
from network_monitor.tcp_monitor import DEFAULT_TIMEOUT_SECONDS, check_tcp_port

# Statuses that prove the host itself answered. REFUSED counts: the host sent a
# TCP reset, so it is up even though nothing listens on that port.
HOST_ANSWERED_STATUSES = frozenset({CheckStatus.SUCCESS, CheckStatus.REFUSED})


def check_device(
    device: Device,
    timeout: float = DEFAULT_TIMEOUT_SECONDS,
    ping_timeout: float = DEFAULT_PING_TIMEOUT_SECONDS,
) -> list[CheckResult]:
    """Run the DNS, ICMP and TCP checks for one device and return all results."""
    dns_result = check_dns(device)
    results = [dns_result]

    if dns_result.status == CheckStatus.DNS_FAILURE:
        results.append(_skipped_after_dns_failure(device, CheckType.ICMP))
        for port in device.ports:
            results.append(_skipped_after_dns_failure(device, CheckType.TCP, port))
        return results

    results.append(check_icmp(device, ping_timeout))

    for port in device.ports:
        results.append(check_tcp_port(device, port, timeout))

    return results


def is_device_reachable(results: list[CheckResult]) -> bool:
    """True if the host answered any ICMP or TCP check.

    A blocked ping on its own never marks a device as unreachable: if any TCP
    port answered, the device is up.
    """
    return any(
        result.check_type in (CheckType.ICMP, CheckType.TCP)
        and result.status in HOST_ANSWERED_STATUSES
        for result in results
    )


def _skipped_after_dns_failure(
    device: Device,
    check_type: CheckType,
    port: int | None = None,
) -> CheckResult:
    """Build a SKIPPED result for a check that could not run because DNS failed."""
    return CheckResult(
        device_name=device.name,
        host=device.host,
        check_type=check_type,
        status=CheckStatus.SKIPPED,
        port=port,
        detail="Skipped because DNS resolution failed",
    )
