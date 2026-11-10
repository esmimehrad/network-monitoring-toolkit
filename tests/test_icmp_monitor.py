"""Tests for ICMP (ping) checks.

Most tests replace subprocess.run with a fake that returns recorded ping
output from Windows and Linux, so they run instantly, on any operating
system, without sending a single packet.
"""

import shutil
import subprocess

import pytest

from network_monitor import icmp_monitor
from network_monitor.icmp_monitor import build_ping_command, check_icmp, parse_round_trip_ms
from network_monitor.models import CheckStatus, CheckType, Criticality, Device, Environment

WINDOWS_REPLY = """
Pinging 127.0.0.1 with 32 bytes of data:
Reply from 127.0.0.1: bytes=32 time<1ms TTL=128
"""
WINDOWS_SLOW_REPLY = "Reply from 10.0.0.1: bytes=32 time=14ms TTL=64"
LINUX_REPLY = "64 bytes from 127.0.0.1: icmp_seq=1 ttl=64 time=0.045 ms"
WINDOWS_TIMEOUT = "Pinging 192.0.2.1 with 32 bytes of data:\nRequest timed out."
WINDOWS_UNREACHABLE = "Reply from 192.168.1.1: Destination host unreachable."
WINDOWS_UNKNOWN_HOST = "Ping request could not find host monitor-demo.invalid."
LINUX_NOT_PERMITTED = "ping: socket: Operation not permitted"


def make_device(host: str = "10.0.0.1") -> Device:
    """Build a valid Device with the given host."""
    return Device(
        name="core-router-01",
        host=host,
        device_type="router",
        environment=Environment.PRODUCTION,
        criticality=Criticality.HIGH,
    )


def fake_ping(monkeypatch, returncode: int, stdout: str = "", stderr: str = "") -> list:
    """Make ping 'available' and replace subprocess.run with a canned result.

    Returns a list that records every command the code tried to run.
    """
    calls = []

    def fake_run(command, **kwargs):
        calls.append((command, kwargs))
        return subprocess.CompletedProcess(command, returncode, stdout=stdout, stderr=stderr)

    monkeypatch.setattr(icmp_monitor.shutil, "which", lambda name: "ping")
    monkeypatch.setattr(icmp_monitor.subprocess, "run", fake_run)
    return calls


# --- Building the command ------------------------------------------------------


@pytest.mark.parametrize(
    ("system", "timeout", "expected"),
    [
        ("Windows", 2.0, ["ping", "-n", "1", "-w", "2000", "10.0.0.1"]),
        ("Linux", 2.0, ["ping", "-c", "1", "-W", "2", "10.0.0.1"]),
        ("Linux", 1.5, ["ping", "-c", "1", "-W", "2", "10.0.0.1"]),
        ("Linux", 0.2, ["ping", "-c", "1", "-W", "1", "10.0.0.1"]),
        ("Darwin", 2.0, ["ping", "-c", "1", "-W", "2000", "10.0.0.1"]),
    ],
)
def test_build_ping_command_for_each_operating_system(system, timeout, expected):
    assert build_ping_command("ping", "10.0.0.1", timeout, system) == expected


def test_host_that_looks_like_an_option_is_refused():
    with pytest.raises(ValueError, match="must not start with '-'"):
        build_ping_command("ping", "-f", 2.0, "Linux")


# --- Reading ping output -------------------------------------------------------


@pytest.mark.parametrize(
    ("output", "expected"),
    [
        (WINDOWS_REPLY, 1.0),
        (WINDOWS_SLOW_REPLY, 14.0),
        (LINUX_REPLY, 0.045),
        (WINDOWS_TIMEOUT, None),
        ("", None),
    ],
)
def test_parse_round_trip_ms(output, expected):
    assert parse_round_trip_ms(output) == expected


# --- Mapping ping results to statuses ------------------------------------------


@pytest.mark.parametrize(
    ("returncode", "stdout", "expected_status"),
    [
        (0, LINUX_REPLY, CheckStatus.SUCCESS),
        (0, WINDOWS_REPLY, CheckStatus.SUCCESS),
        (1, WINDOWS_TIMEOUT, CheckStatus.TIMEOUT),
        (0, WINDOWS_UNREACHABLE, CheckStatus.UNREACHABLE),
        (1, WINDOWS_UNKNOWN_HOST, CheckStatus.DNS_FAILURE),
        (2, LINUX_NOT_PERMITTED, CheckStatus.SKIPPED),
    ],
)
def test_ping_output_maps_to_the_right_status(monkeypatch, returncode, stdout, expected_status):
    fake_ping(monkeypatch, returncode, stdout=stdout)

    result = check_icmp(make_device())

    assert result.check_type == CheckType.ICMP
    assert result.status == expected_status


def test_successful_ping_records_round_trip_time(monkeypatch):
    fake_ping(monkeypatch, 0, stdout=WINDOWS_SLOW_REPLY)

    result = check_icmp(make_device())

    assert result.is_success
    assert result.response_time_ms == 14.0
    assert result.error is None


def test_windows_unreachable_reply_is_not_a_success(monkeypatch):
    # Windows ping exits with code 0 when a router answers "Destination host
    # unreachable", so the exit code alone cannot be trusted.
    fake_ping(monkeypatch, 0, stdout=WINDOWS_UNREACHABLE)

    result = check_icmp(make_device())

    assert not result.is_success
    assert result.response_time_ms is None


def test_timeout_message_warns_that_icmp_may_be_blocked(monkeypatch):
    fake_ping(monkeypatch, 1, stdout=WINDOWS_TIMEOUT)

    result = check_icmp(make_device(), timeout=1.5)

    assert "within 1.5 seconds" in result.error
    assert "blocked by a firewall" in result.error


def test_ping_is_run_safely_without_a_shell(monkeypatch):
    calls = fake_ping(monkeypatch, 0, stdout=LINUX_REPLY)

    check_icmp(make_device("10.0.0.1"), timeout=2.0)

    [(command, kwargs)] = calls
    assert isinstance(command, list)
    assert command[0] == "ping"
    assert command[-1] == "10.0.0.1"
    assert kwargs.get("shell", False) is False
    assert kwargs["timeout"] == 2.0 + icmp_monitor.PROCESS_GRACE_SECONDS


# --- When ping cannot run at all -----------------------------------------------


def test_missing_ping_command_is_skipped_without_running_anything(monkeypatch):
    def fail_if_called(*args, **kwargs):
        raise AssertionError("subprocess.run must not be called when ping is missing")

    monkeypatch.setattr(icmp_monitor.shutil, "which", lambda name: None)
    monkeypatch.setattr(icmp_monitor.subprocess, "run", fail_if_called)

    result = check_icmp(make_device())

    assert result.status == CheckStatus.SKIPPED
    assert "not available" in result.detail


def test_ping_process_that_hangs_is_a_timeout(monkeypatch):
    def hang(command, **kwargs):
        raise subprocess.TimeoutExpired(command, kwargs["timeout"])

    monkeypatch.setattr(icmp_monitor.shutil, "which", lambda name: "ping")
    monkeypatch.setattr(icmp_monitor.subprocess, "run", hang)

    result = check_icmp(make_device())

    assert result.status == CheckStatus.TIMEOUT


def test_ping_that_cannot_be_started_is_skipped(monkeypatch):
    def cannot_start(command, **kwargs):
        raise PermissionError("Access is denied")

    monkeypatch.setattr(icmp_monitor.shutil, "which", lambda name: "ping")
    monkeypatch.setattr(icmp_monitor.subprocess, "run", cannot_start)

    result = check_icmp(make_device())

    assert result.status == CheckStatus.SKIPPED
    assert "Access is denied" in result.detail


# --- One real ping, to this computer only --------------------------------------


@pytest.mark.skipif(shutil.which("ping") is None, reason="ping is not installed")
def test_real_ping_to_localhost():
    result = check_icmp(make_device("127.0.0.1"))

    # SKIPPED is acceptable: some containers forbid ICMP even to localhost.
    assert result.status in (CheckStatus.SUCCESS, CheckStatus.SKIPPED)
    if result.is_success:
        assert result.response_time_ms is not None
