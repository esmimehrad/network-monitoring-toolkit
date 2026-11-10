"""Monitor many devices at the same time with a thread pool.

Network checks spend almost all their time *waiting*: for a DNS answer, a TCP
handshake, or a ping reply. While one thread waits, others can run, so checking
devices concurrently finishes in roughly the time of the slowest device instead
of the sum of all devices.

Thread safety: worker threads never share or modify common data. Each worker
checks one device and *returns* its results; only the main thread collects
them, prints them and (from Phase 7) saves them. No locks are needed.
"""

import time
from collections.abc import Callable
from concurrent.futures import Future, ThreadPoolExecutor, as_completed

from network_monitor.connectivity import check_device, is_device_reachable
from network_monitor.icmp_monitor import DEFAULT_PING_TIMEOUT_SECONDS
from network_monitor.models import CheckResult, Device, DeviceReport, MonitoringRun, utc_now
from network_monitor.tcp_monitor import DEFAULT_TIMEOUT_SECONDS

DEFAULT_MAX_WORKERS = 10
MAX_WORKERS_LIMIT = 50


def run_checks(
    devices: list[Device],
    max_workers: int = DEFAULT_MAX_WORKERS,
    timeout: float = DEFAULT_TIMEOUT_SECONDS,
    ping_timeout: float = DEFAULT_PING_TIMEOUT_SECONDS,
    on_device_done: Callable[[DeviceReport], None] | None = None,
) -> MonitoringRun:
    """Check every device concurrently and return the reports in inventory order.

    on_device_done, if given, is called in the main thread as soon as each
    device finishes, so progress can be shown while other devices still run.
    """
    if not 1 <= max_workers <= MAX_WORKERS_LIMIT:
        raise ValueError(
            f"max_workers must be between 1 and {MAX_WORKERS_LIMIT}, got {max_workers}"
        )

    workers = max(1, min(max_workers, len(devices)))
    started_at = utc_now()
    start = time.perf_counter()
    reports_by_index: dict[int, DeviceReport] = {}

    with ThreadPoolExecutor(max_workers=workers, thread_name_prefix="monitor") as executor:
        future_to_index = {
            executor.submit(check_device, device, timeout, ping_timeout): index
            for index, device in enumerate(devices)
        }

        for future in as_completed(future_to_index):
            index = future_to_index[future]
            report = _build_report(devices[index], future)
            reports_by_index[index] = report
            if on_device_done is not None:
                on_device_done(report)

    reports = tuple(reports_by_index[index] for index in range(len(devices)))

    return MonitoringRun(
        reports=reports,
        started_at=started_at,
        duration_seconds=round(time.perf_counter() - start, 2),
        workers=workers,
    )


def _build_report(device: Device, future: Future[list[CheckResult]]) -> DeviceReport:
    """Turn a finished future into a DeviceReport, capturing any unexpected crash."""
    try:
        results = future.result()
    except Exception as exc:
        # A bug while checking one device must never stop the other devices.
        return DeviceReport(
            device=device,
            results=(),
            reachable=False,
            error=f"{type(exc).__name__}: {exc}",
        )

    return DeviceReport(
        device=device,
        results=tuple(results),
        reachable=is_device_reachable(results),
    )
