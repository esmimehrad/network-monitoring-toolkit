"""Core data models shared by every part of the toolkit.

These classes describe *what* we monitor (Device), *what we found*
(CheckResult), and how a whole run went (DeviceReport, MonitoringRun). They
only hold data: validation lives in inventory.py, network logic lives in the
monitor modules, and storage lives in database.py.
"""

from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum


class Environment(StrEnum):
    """Deployment environment a device belongs to."""

    DEVELOPMENT = "development"
    STAGING = "staging"
    PRODUCTION = "production"


class Criticality(StrEnum):
    """How important a device is to the business."""

    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


class CheckType(StrEnum):
    """The kind of monitoring check that produced a result."""

    DNS = "dns"
    TCP = "tcp"
    ICMP = "icmp"


class CheckStatus(StrEnum):
    """The outcome of a single check.

    Separate failure statuses tell an operator *why* a check failed:
    DNS_FAILURE points at name resolution, REFUSED means the host answered
    but nothing is listening on the port, and TIMEOUT suggests a firewall
    silently dropping traffic or a host that is down.
    """

    SUCCESS = "success"
    DNS_FAILURE = "dns_failure"
    TIMEOUT = "timeout"
    REFUSED = "refused"
    UNREACHABLE = "unreachable"
    ERROR = "error"
    SKIPPED = "skipped"


def utc_now() -> datetime:
    """Return the current time as a timezone-aware UTC datetime."""
    return datetime.now(UTC)


@dataclass(frozen=True)
class Device:
    """A network device or server loaded from the inventory."""

    name: str
    host: str
    device_type: str
    environment: Environment
    criticality: Criticality
    ports: tuple[int, ...] = ()
    description: str = ""


@dataclass(frozen=True)
class CheckResult:
    """The outcome of one check against one device (and optionally one port)."""

    device_name: str
    host: str
    check_type: CheckType
    status: CheckStatus
    port: int | None = None
    response_time_ms: float | None = None
    error: str | None = None
    detail: str | None = None
    timestamp: datetime = field(default_factory=utc_now)

    @property
    def is_success(self) -> bool:
        """True when the check succeeded."""
        return self.status == CheckStatus.SUCCESS

    def to_dict(self) -> dict[str, str | int | float | None]:
        """Return the result as plain Python values, ready for JSON, CSV or SQLite."""
        return {
            "device_name": self.device_name,
            "host": self.host,
            "check_type": self.check_type.value,
            "status": self.status.value,
            "port": self.port,
            "response_time_ms": self.response_time_ms,
            "error": self.error,
            "detail": self.detail,
            "timestamp": self.timestamp.isoformat(),
        }


@dataclass(frozen=True)
class DeviceReport:
    """Every check result for one device from one monitoring run."""

    device: Device
    results: tuple[CheckResult, ...]
    reachable: bool
    error: str | None = None


@dataclass(frozen=True)
class MonitoringRun:
    """The outcome of monitoring a whole inventory once."""

    reports: tuple[DeviceReport, ...]
    started_at: datetime
    duration_seconds: float
    workers: int

    @property
    def results(self) -> list[CheckResult]:
        """Every check result from every device, in inventory order."""
        return [result for report in self.reports for result in report.results]

    @property
    def total_checks(self) -> int:
        """Number of checks that produced a result."""
        return len(self.results)

    @property
    def succeeded_checks(self) -> int:
        """Number of checks that succeeded."""
        return sum(1 for result in self.results if result.is_success)

    @property
    def skipped_checks(self) -> int:
        """Number of checks that were skipped (not counted as failures)."""
        return sum(1 for result in self.results if result.status == CheckStatus.SKIPPED)

    @property
    def failed_checks(self) -> int:
        """Number of checks that ran and failed."""
        return self.total_checks - self.succeeded_checks - self.skipped_checks

    @property
    def reachable_devices(self) -> int:
        """Number of devices that answered at least one ICMP or TCP check."""
        return sum(1 for report in self.reports if report.reachable)

    @property
    def crashed_devices(self) -> int:
        """Number of devices whose checks stopped with an unexpected error."""
        return sum(1 for report in self.reports if report.error is not None)

    @property
    def has_failures(self) -> bool:
        """True if any check failed or any device's checks crashed."""
        return self.failed_checks > 0 or self.crashed_devices > 0
