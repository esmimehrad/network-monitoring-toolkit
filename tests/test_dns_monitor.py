"""Tests for DNS checks.

Most tests replace socket.getaddrinfo with a fake ("monkeypatching"), so they
never depend on a real DNS server, the internet, or the speed of the network.
"""

import socket

import pytest

from network_monitor import dns_monitor
from network_monitor.dns_monitor import check_dns, is_ip_address, resolve_host
from network_monitor.models import CheckStatus, CheckType, Criticality, Device, Environment


def make_device(host: str) -> Device:
    """Build a valid Device with the given host."""
    return Device(
        name="web-server-01",
        host=host,
        device_type="server",
        environment=Environment.DEVELOPMENT,
        criticality=Criticality.MEDIUM,
        ports=(443,),
    )


def fake_answer(ip: str) -> tuple:
    """One getaddrinfo() answer in the same shape the real function returns."""
    family = socket.AF_INET6 if ":" in ip else socket.AF_INET
    return (family, socket.SOCK_STREAM, socket.IPPROTO_TCP, "", (ip, 0))


@pytest.mark.parametrize(
    ("host", "expected"),
    [
        ("192.168.1.1", True),
        ("::1", True),
        ("2001:db8::10", True),
        ("localhost", False),
        ("web-01.example.local", False),
    ],
)
def test_is_ip_address(host, expected):
    assert is_ip_address(host) is expected


def test_resolve_host_removes_duplicates_and_keeps_order(monkeypatch):
    answers = [fake_answer("10.0.0.2"), fake_answer("10.0.0.1"), fake_answer("10.0.0.2")]
    monkeypatch.setattr(dns_monitor.socket, "getaddrinfo", lambda *args, **kwargs: answers)

    assert resolve_host("web-01.example.local") == ["10.0.0.2", "10.0.0.1"]


def test_ip_address_host_skips_dns_lookup(monkeypatch):
    def fail_if_called(*args, **kwargs):
        raise AssertionError("getaddrinfo must not be called for an IP address")

    monkeypatch.setattr(dns_monitor.socket, "getaddrinfo", fail_if_called)

    result = check_dns(make_device("192.168.1.10"))

    assert result.status == CheckStatus.SKIPPED
    assert result.check_type == CheckType.DNS
    assert "no DNS lookup needed" in result.detail


def test_successful_lookup_records_addresses_and_time(monkeypatch):
    answers = [fake_answer("203.0.113.10"), fake_answer("2001:db8::10")]
    monkeypatch.setattr(dns_monitor.socket, "getaddrinfo", lambda *args, **kwargs: answers)

    result = check_dns(make_device("web-01.example.local"))

    assert result.is_success
    assert result.detail == "203.0.113.10, 2001:db8::10"
    assert result.response_time_ms is not None
    assert result.response_time_ms >= 0
    assert result.error is None


def test_failed_lookup_is_a_dns_failure(monkeypatch):
    def raise_gaierror(*args, **kwargs):
        raise socket.gaierror(socket.EAI_NONAME, "Name or service not known")

    monkeypatch.setattr(dns_monitor.socket, "getaddrinfo", raise_gaierror)

    result = check_dns(make_device("missing.example.invalid"))

    assert result.status == CheckStatus.DNS_FAILURE
    assert result.error == "DNS lookup failed: Name or service not known"
    assert result.response_time_ms is None


def test_unexpected_os_error_is_reported_as_error(monkeypatch):
    def raise_os_error(*args, **kwargs):
        raise OSError("resolver crashed")

    monkeypatch.setattr(dns_monitor.socket, "getaddrinfo", raise_os_error)

    result = check_dns(make_device("web-01.example.local"))

    assert result.status == CheckStatus.ERROR
    assert "resolver crashed" in result.error


def test_localhost_resolves_for_real():
    # 'localhost' is answered by the operating system itself, not the internet.
    result = check_dns(make_device("localhost"))

    assert result.is_success
    assert "127.0.0.1" in result.detail or "::1" in result.detail
