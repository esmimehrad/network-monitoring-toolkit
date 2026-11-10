"""Tests for running all checks against one device, and judging reachability.

check_dns, check_icmp and check_tcp_port are replaced with simple fakes here,
because these tests are about the *order and rules* in connectivity.py, not
about networking (the monitor test files already cover that).
"""

import pytest

from network_monitor import connectivity
from network_monitor.connectivity import check_device, is_device_reachable
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


def result(check_type: CheckType, status: CheckStatus, port: int | None = None) -> CheckResult:
    """Build a CheckResult for web-01 with the given type, status and port."""
    return CheckResult("web-01", "web-01.example.local", check_type, status, port=port)


def fake_dns(status: CheckStatus):
    """Return a replacement for check_dns that always reports the given status."""
    return lambda device: result(CheckType.DNS, status)


def fake_icmp(status: CheckStatus):
    """Return a replacement for check_icmp that always reports the given status."""
    return lambda device, timeout: result(CheckType.ICMP, status)


def test_checks_run_in_order_dns_icmp_then_tcp_per_port(monkeypatch):
    tcp_calls = []

    def fake_tcp(device, port, timeout):
        tcp_calls.append((port, timeout))
        return result(CheckType.TCP, CheckStatus.SUCCESS, port)

    monkeypatch.setattr(connectivity, "check_dns", fake_dns(CheckStatus.SUCCESS))
    monkeypatch.setattr(connectivity, "check_icmp", fake_icmp(CheckStatus.SUCCESS))
    monkeypatch.setattr(connectivity, "check_tcp_port", fake_tcp)

    results = check_device(make_device(), timeout=2.0)

    assert [r.check_type for r in results] == [
        CheckType.DNS,
        CheckType.ICMP,
        CheckType.TCP,
        CheckType.TCP,
    ]
    assert [r.port for r in results] == [None, None, 22, 443]
    assert tcp_calls == [(22, 2.0), (443, 2.0)]


def test_ping_timeout_is_passed_to_icmp_check(monkeypatch):
    ping_timeouts = []

    def recording_icmp(device, timeout):
        ping_timeouts.append(timeout)
        return result(CheckType.ICMP, CheckStatus.SUCCESS)

    monkeypatch.setattr(connectivity, "check_dns", fake_dns(CheckStatus.SUCCESS))
    monkeypatch.setattr(connectivity, "check_icmp", recording_icmp)

    check_device(make_device(ports=()), ping_timeout=0.5)

    assert ping_timeouts == [0.5]


def test_failed_ping_does_not_stop_tcp_checks(monkeypatch):
    monkeypatch.setattr(connectivity, "check_dns", fake_dns(CheckStatus.SUCCESS))
    monkeypatch.setattr(connectivity, "check_icmp", fake_icmp(CheckStatus.TIMEOUT))
    monkeypatch.setattr(
        connectivity,
        "check_tcp_port",
        lambda device, port, timeout: result(CheckType.TCP, CheckStatus.SUCCESS, port),
    )

    results = check_device(make_device())

    assert [r.status for r in results] == [
        CheckStatus.SUCCESS,
        CheckStatus.TIMEOUT,
        CheckStatus.SUCCESS,
        CheckStatus.SUCCESS,
    ]


def test_dns_failure_skips_icmp_and_every_tcp_check(monkeypatch):
    def fail_if_called(*args, **kwargs):
        raise AssertionError("ICMP and TCP checks must not run when DNS fails")

    monkeypatch.setattr(connectivity, "check_dns", fake_dns(CheckStatus.DNS_FAILURE))
    monkeypatch.setattr(connectivity, "check_icmp", fail_if_called)
    monkeypatch.setattr(connectivity, "check_tcp_port", fail_if_called)

    results = check_device(make_device())

    assert results[0].status == CheckStatus.DNS_FAILURE
    assert [(r.check_type, r.port) for r in results[1:]] == [
        (CheckType.ICMP, None),
        (CheckType.TCP, 22),
        (CheckType.TCP, 443),
    ]
    assert all(r.status == CheckStatus.SKIPPED for r in results[1:])
    assert all("DNS resolution failed" in r.detail for r in results[1:])


def test_device_without_ports_gets_dns_and_icmp_only(monkeypatch):
    monkeypatch.setattr(connectivity, "check_dns", fake_dns(CheckStatus.SUCCESS))
    monkeypatch.setattr(connectivity, "check_icmp", fake_icmp(CheckStatus.SUCCESS))

    results = check_device(make_device(ports=()))

    assert [r.check_type for r in results] == [CheckType.DNS, CheckType.ICMP]


@pytest.mark.parametrize(
    ("results", "expected"),
    [
        ([result(CheckType.ICMP, CheckStatus.SUCCESS)], True),
        (
            [
                result(CheckType.ICMP, CheckStatus.TIMEOUT),
                result(CheckType.TCP, CheckStatus.SUCCESS, 443),
            ],
            True,
        ),
        (
            [
                result(CheckType.ICMP, CheckStatus.TIMEOUT),
                result(CheckType.TCP, CheckStatus.REFUSED, 22),
            ],
            True,
        ),
        (
            [
                result(CheckType.ICMP, CheckStatus.TIMEOUT),
                result(CheckType.TCP, CheckStatus.TIMEOUT, 443),
            ],
            False,
        ),
        (
            [
                result(CheckType.DNS, CheckStatus.SUCCESS),
                result(CheckType.ICMP, CheckStatus.SKIPPED),
            ],
            False,
        ),
        ([], False),
    ],
)
def test_is_device_reachable(results, expected):
    assert is_device_reachable(results) is expected


def test_real_checks_against_local_server(monkeypatch, listening_port):
    # Real DNS and TCP; ping is faked so the test does not depend on ICMP rules.
    monkeypatch.setattr(connectivity, "check_icmp", fake_icmp(CheckStatus.SKIPPED))

    results = check_device(make_device(host="127.0.0.1", ports=(listening_port,)), timeout=2)

    assert [r.status for r in results] == [
        CheckStatus.SKIPPED,
        CheckStatus.SKIPPED,
        CheckStatus.SUCCESS,
    ]
    assert is_device_reachable(results)
