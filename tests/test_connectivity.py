"""Tests for running all checks against one device.

check_dns and check_tcp_port are replaced with simple fakes here, because these
tests are about the *order and rules* in connectivity.py, not about networking
(which test_dns_monitor.py and test_tcp_monitor.py already cover).
"""

from network_monitor import connectivity
from network_monitor.connectivity import check_device
from network_monitor.models import (
    CheckResult,
    CheckStatus,
    CheckType,
    Criticality,
    Device,
    Environment,
)


def make_device(host: str = "web-01.example.local", ports: tuple[int, ...] = (22, 443)) -> Device:
    """Build a valid Device with the given host and ports."""
    return Device(
        name="web-01",
        host=host,
        device_type="server",
        environment=Environment.PRODUCTION,
        criticality=Criticality.HIGH,
        ports=ports,
    )


def fake_dns(status: CheckStatus):
    """Return a replacement for check_dns that always reports the given status."""

    def check_dns(device: Device) -> CheckResult:
        return CheckResult(device.name, device.host, CheckType.DNS, status)

    return check_dns


def test_dns_success_runs_one_tcp_check_per_port(monkeypatch):
    tcp_calls = []

    def fake_tcp(device, port, timeout):
        tcp_calls.append((port, timeout))
        return CheckResult(device.name, device.host, CheckType.TCP, CheckStatus.SUCCESS, port=port)

    monkeypatch.setattr(connectivity, "check_dns", fake_dns(CheckStatus.SUCCESS))
    monkeypatch.setattr(connectivity, "check_tcp_port", fake_tcp)

    results = check_device(make_device(), timeout=2.0)

    assert [result.check_type for result in results] == [
        CheckType.DNS,
        CheckType.TCP,
        CheckType.TCP,
    ]
    assert [result.port for result in results] == [None, 22, 443]
    assert tcp_calls == [(22, 2.0), (443, 2.0)]


def test_dns_failure_skips_every_tcp_check(monkeypatch):
    def fail_if_called(*args, **kwargs):
        raise AssertionError("TCP checks must not run when DNS fails")

    monkeypatch.setattr(connectivity, "check_dns", fake_dns(CheckStatus.DNS_FAILURE))
    monkeypatch.setattr(connectivity, "check_tcp_port", fail_if_called)

    results = check_device(make_device())

    assert results[0].status == CheckStatus.DNS_FAILURE
    assert [result.status for result in results[1:]] == [CheckStatus.SKIPPED, CheckStatus.SKIPPED]
    assert [result.port for result in results[1:]] == [22, 443]
    assert all("DNS resolution failed" in result.detail for result in results[1:])


def test_device_without_ports_only_gets_a_dns_check(monkeypatch):
    monkeypatch.setattr(connectivity, "check_dns", fake_dns(CheckStatus.SUCCESS))

    results = check_device(make_device(ports=()))

    assert len(results) == 1
    assert results[0].check_type == CheckType.DNS


def test_real_checks_against_local_server(listening_port):
    results = check_device(make_device(host="127.0.0.1", ports=(listening_port,)), timeout=2)

    assert [result.status for result in results] == [CheckStatus.SKIPPED, CheckStatus.SUCCESS]
