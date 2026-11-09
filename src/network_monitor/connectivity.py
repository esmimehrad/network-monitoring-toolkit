"""Run every connectivity check for one device.

Order matters: DNS runs first. If the hostname cannot be resolved, every TCP
check would fail for the same reason, so those checks are recorded as SKIPPED
with a clear explanation. The report then shows one root cause instead of a
wall of duplicate failures.
"""

from network_monitor.dns_monitor import check_dns
from network_monitor.models import CheckResult, CheckStatus, CheckType, Device
from network_monitor.tcp_monitor import DEFAULT_TIMEOUT_SECONDS, check_tcp_port


def check_device(device: Device, timeout: float = DEFAULT_TIMEOUT_SECONDS) -> list[CheckResult]:
    """Run the DNS check, then one TCP check per configured port, and return all results."""
    dns_result = check_dns(device)
    results = [dns_result]

    if dns_result.status == CheckStatus.DNS_FAILURE:
        for port in device.ports:
            results.append(
                CheckResult(
                    device_name=device.name,
                    host=device.host,
                    check_type=CheckType.TCP,
                    status=CheckStatus.SKIPPED,
                    port=port,
                    detail="Skipped because DNS resolution failed",
                )
            )
        return results

    for port in device.ports:
        results.append(check_tcp_port(device, port, timeout))

    return results
