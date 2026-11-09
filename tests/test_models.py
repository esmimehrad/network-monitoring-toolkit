"""Tests for the core data models."""

import json
from dataclasses import FrozenInstanceError
from datetime import UTC, datetime

import pytest

from network_monitor.models import (
    CheckResult,
    CheckStatus,
    CheckType,
    Criticality,
    Device,
    Environment,
)


def make_device() -> Device:
    """Build a valid Device for tests that need one."""
    return Device(
        name="core-router-01",
        host="192.168.1.1",
        device_type="router",
        environment=Environment.PRODUCTION,
        criticality=Criticality.HIGH,
        ports=(22, 443),
    )


def test_device_stores_fields_and_defaults():
    device = make_device()

    assert device.name == "core-router-01"
    assert device.ports == (22, 443)
    assert device.description == ""


def test_device_is_immutable():
    device = make_device()

    with pytest.raises(FrozenInstanceError):
        device.host = "10.0.0.1"


def test_enum_is_created_from_yaml_string():
    assert Environment("production") is Environment.PRODUCTION
    assert Criticality("high") == "high"


def test_enum_rejects_unknown_value():
    with pytest.raises(ValueError):
        Environment("prod")


def test_successful_tcp_result():
    result = CheckResult(
        device_name="core-router-01",
        host="192.168.1.1",
        check_type=CheckType.TCP,
        status=CheckStatus.SUCCESS,
        port=22,
        response_time_ms=4.2,
    )

    assert result.is_success
    assert result.error is None


@pytest.mark.parametrize(
    "status",
    [status for status in CheckStatus if status != CheckStatus.SUCCESS],
)
def test_only_success_status_counts_as_success(status):
    result = CheckResult(
        device_name="web-server-01",
        host="example.local",
        check_type=CheckType.TCP,
        status=status,
        port=443,
    )

    assert not result.is_success


def test_timestamp_is_set_automatically_in_utc():
    before = datetime.now(UTC)
    result = CheckResult(
        device_name="web-server-01",
        host="example.local",
        check_type=CheckType.DNS,
        status=CheckStatus.SUCCESS,
    )
    after = datetime.now(UTC)

    assert result.timestamp.tzinfo is UTC
    assert before <= result.timestamp <= after


def test_to_dict_returns_plain_json_serializable_values():
    result = CheckResult(
        device_name="web-server-01",
        host="example.local",
        check_type=CheckType.TCP,
        status=CheckStatus.TIMEOUT,
        port=443,
        error="Connection timed out after 3.0 seconds",
    )

    data = result.to_dict()

    assert data["check_type"] == "tcp"
    assert data["status"] == "timeout"
    assert type(data["status"]) is str
    assert data["port"] == 443
    assert data["response_time_ms"] is None
    assert data["timestamp"].endswith("+00:00")
    json.dumps(data)


def test_refused_result_keeps_error_message():
    result = CheckResult(
        device_name="lab-switch-01",
        host="10.0.0.2",
        check_type=CheckType.TCP,
        status=CheckStatus.REFUSED,
        port=22,
        error="Connection refused by remote host",
    )

    assert result.error == "Connection refused"
    assert result.to_dict()["error"] == "Connection refused"
