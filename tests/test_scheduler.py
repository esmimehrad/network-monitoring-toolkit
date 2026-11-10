"""Tests for concurrent monitoring.

check_device is replaced with fakes so these tests control exactly how long
each device takes, which device crashes, and how many run at the same time.
"""

import threading
import time
from datetime import UTC

import pytest

from network_monitor import scheduler
from network_monitor.models import (
    CheckResult,
    CheckStatus,
    CheckType,
    Criticality,
    Device,
    DeviceReport,
    Environment,
    MonitoringRun,
    utc_now,
)
from network_monitor.scheduler import MAX_WORKERS_LIMIT, run_checks


def make_devices(count: int) -> list[Device]:
    """Build `count` valid devices named device-0, device-1, ..."""
    return [
        Device(
            name=f"device-{number}",
            host=f"10.0.0.{number + 1}",
            device_type="server",
            environment=Environment.DEVELOPMENT,
            criticality=Criticality.LOW,
            ports=(443,),
        )
        for number in range(count)
    ]


def tcp_result(device: Device, status: CheckStatus = CheckStatus.SUCCESS) -> CheckResult:
    """Build a TCP result for the device on port 443."""
    return CheckResult(device.name, device.host, CheckType.TCP, status, port=443)


# --- Concurrency -----------------------------------------------------------------


def test_devices_really_run_at_the_same_time(monkeypatch):
    # A Barrier only opens when 4 threads are waiting at it together. If the
    # devices ran one after another, the first wait would time out.
    barrier = threading.Barrier(4, timeout=5)

    def fake_check_device(device, timeout, ping_timeout):
        barrier.wait()
        return [tcp_result(device)]

    monkeypatch.setattr(scheduler, "check_device", fake_check_device)

    run = run_checks(make_devices(4), max_workers=4)

    assert run.crashed_devices == 0
    assert run.succeeded_checks == 4


def test_never_more_threads_than_max_workers(monkeypatch):
    lock = threading.Lock()
    running = 0
    peak = 0

    def fake_check_device(device, timeout, ping_timeout):
        nonlocal running, peak
        with lock:
            running += 1
            peak = max(peak, running)
        time.sleep(0.05)
        with lock:
            running -= 1
        return [tcp_result(device)]

    monkeypatch.setattr(scheduler, "check_device", fake_check_device)

    run = run_checks(make_devices(8), max_workers=2)

    assert peak <= 2
    assert run.workers == 2
    assert len(run.reports) == 8


def test_reports_keep_inventory_order_even_when_finishing_out_of_order(monkeypatch):
    def fake_check_device(device, timeout, ping_timeout):
        # device-0 is the slowest, device-4 the fastest.
        number = int(device.name.split("-")[1])
        time.sleep(0.05 * (5 - number))
        return [tcp_result(device)]

    finished_order = []
    monkeypatch.setattr(scheduler, "check_device", fake_check_device)

    run = run_checks(
        make_devices(5),
        max_workers=5,
        on_device_done=lambda report: finished_order.append(report.device.name),
    )

    assert [report.device.name for report in run.reports] == [
        "device-0",
        "device-1",
        "device-2",
        "device-3",
        "device-4",
    ]
    assert finished_order[0] == "device-4"
    assert sorted(finished_order) == [report.device.name for report in run.reports]


def test_one_crashing_device_does_not_stop_the_others(monkeypatch):
    def fake_check_device(device, timeout, ping_timeout):
        if device.name == "device-1":
            raise RuntimeError("simulated bug")
        return [tcp_result(device)]

    monkeypatch.setattr(scheduler, "check_device", fake_check_device)

    run = run_checks(make_devices(3))

    crashed = run.reports[1]
    assert crashed.error == "RuntimeError: simulated bug"
    assert crashed.results == ()
    assert crashed.reachable is False
    assert run.reports[0].error is None
    assert run.reports[2].error is None
    assert run.crashed_devices == 1
    assert run.has_failures


def test_timeouts_are_passed_to_every_device_check(monkeypatch):
    received = []

    def fake_check_device(device, timeout, ping_timeout):
        received.append((timeout, ping_timeout))
        return [tcp_result(device)]

    monkeypatch.setattr(scheduler, "check_device", fake_check_device)

    run_checks(make_devices(3), timeout=1.5, ping_timeout=0.5)

    assert received == [(1.5, 0.5)] * 3


def test_reachability_is_judged_for_each_device(monkeypatch):
    def fake_check_device(device, timeout, ping_timeout):
        status = CheckStatus.SUCCESS if device.name == "device-0" else CheckStatus.TIMEOUT
        return [tcp_result(device, status)]

    monkeypatch.setattr(scheduler, "check_device", fake_check_device)

    run = run_checks(make_devices(2))

    assert [report.reachable for report in run.reports] == [True, False]
    assert run.reachable_devices == 1


# --- Settings and run details ---------------------------------------------------


@pytest.mark.parametrize("max_workers", [0, -1, MAX_WORKERS_LIMIT + 1])
def test_invalid_max_workers_is_rejected(max_workers):
    with pytest.raises(ValueError, match="max_workers"):
        run_checks(make_devices(1), max_workers=max_workers)


def test_workers_never_exceed_number_of_devices(monkeypatch):
    monkeypatch.setattr(scheduler, "check_device", lambda d, t, p: [tcp_result(d)])

    run = run_checks(make_devices(3), max_workers=10)

    assert run.workers == 3


def test_run_records_start_time_and_duration(monkeypatch):
    monkeypatch.setattr(scheduler, "check_device", lambda d, t, p: [tcp_result(d)])

    run = run_checks(make_devices(2))

    assert run.started_at.tzinfo is UTC
    assert run.duration_seconds >= 0


def test_empty_device_list_returns_an_empty_run():
    run = run_checks([])

    assert run.reports == ()
    assert run.total_checks == 0
    assert not run.has_failures


# --- MonitoringRun counting -----------------------------------------------------


def test_monitoring_run_counts_checks_and_devices():
    up_device, down_device, broken_device = make_devices(3)
    run = MonitoringRun(
        reports=(
            DeviceReport(
                up_device,
                (
                    tcp_result(up_device, CheckStatus.SUCCESS),
                    CheckResult(up_device.name, up_device.host, CheckType.DNS, CheckStatus.SKIPPED),
                ),
                reachable=True,
            ),
            DeviceReport(
                down_device,
                (tcp_result(down_device, CheckStatus.TIMEOUT),),
                reachable=False,
            ),
            DeviceReport(broken_device, (), reachable=False, error="RuntimeError: boom"),
        ),
        started_at=utc_now(),
        duration_seconds=1.0,
        workers=3,
    )

    assert run.total_checks == 3
    assert run.succeeded_checks == 1
    assert run.skipped_checks == 1
    assert run.failed_checks == 1
    assert run.reachable_devices == 1
    assert run.crashed_devices == 1
    assert run.has_failures
    assert [result.device_name for result in run.results] == ["device-0", "device-0", "device-1"]
