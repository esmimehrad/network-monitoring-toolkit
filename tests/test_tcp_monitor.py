"""Tests for TCP port checks.

The success test uses a real TCP server on 127.0.0.1 (see conftest.py). The
failure tests replace socket.create_connection with a fake that raises each
kind of network error, so they are fast and never touch a real network.
"""

import socket

import pytest

from network_monitor import tcp_monitor
from network_monitor.models import CheckStatus, CheckType, Criticality, Device, Environment
from network_monitor.tcp_monitor import DEFAULT_TIMEOUT_SECONDS, check_tcp_port


def make_device(host: str = "127.0.0.1") -> Device:
    """Build a valid Device with the given host."""
    return Device(
        name="local-test",
        host=host,
        device_type="server",
        environment=Environment.DEVELOPMENT,
        criticality=Criticality.LOW,
    )


def test_successful_connection_to_local_server(listening_port):
    result = check_tcp_port(make_device(), listening_port, timeout=2)

    assert result.is_success
    assert result.check_type == CheckType.TCP
    assert result.port == listening_port
    assert result.response_time_ms is not None
    assert result.response_time_ms >= 0
    assert result.error is None


@pytest.mark.parametrize(
    ("error", "expected_status", "expected_message"),
    [
        (TimeoutError("timed out"), CheckStatus.TIMEOUT, "No response within 1.5 seconds"),
        (ConnectionRefusedError(), CheckStatus.REFUSED, "Connection refused"),
        (socket.gaierror(socket.EAI_NONAME, "Name unknown"), CheckStatus.DNS_FAILURE, "DNS"),
        (OSError(101, "Network is unreachable"), CheckStatus.UNREACHABLE, "unreachable"),
        (ConnectionResetError(), CheckStatus.UNREACHABLE, "Network error"),
    ],
)
def test_network_errors_map_to_the_right_status(
    monkeypatch, error, expected_status, expected_message
):
    def fake_create_connection(*args, **kwargs):
        raise error

    monkeypatch.setattr(tcp_monitor.socket, "create_connection", fake_create_connection)

    result = check_tcp_port(make_device(), 443, timeout=1.5)

    assert result.status == expected_status
    assert expected_message in result.error
    assert result.response_time_ms is None
    assert result.port == 443


def test_host_port_and_timeout_are_passed_to_socket(monkeypatch, listening_port):
    calls = []
    real_create_connection = socket.create_connection

    def recording_create_connection(address, timeout):
        calls.append((address, timeout))
        return real_create_connection(("127.0.0.1", listening_port), timeout=timeout)

    monkeypatch.setattr(tcp_monitor.socket, "create_connection", recording_create_connection)

    check_tcp_port(make_device("web-01.example.local"), 8443, timeout=4.5)

    assert calls == [(("web-01.example.local", 8443), 4.5)]


def test_default_timeout_is_used_when_not_given(monkeypatch, listening_port):
    used_timeouts = []
    real_create_connection = socket.create_connection

    def recording_create_connection(address, timeout):
        used_timeouts.append(timeout)
        return real_create_connection(("127.0.0.1", listening_port), timeout=timeout)

    monkeypatch.setattr(tcp_monitor.socket, "create_connection", recording_create_connection)

    check_tcp_port(make_device(), 22)

    assert used_timeouts == [DEFAULT_TIMEOUT_SECONDS]
