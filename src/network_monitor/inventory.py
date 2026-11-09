"""Load the device inventory from a YAML file and validate it.

All inventory validation lives in this module. Every other part of the toolkit
can trust that each Device it receives has a valid name, host, environment,
criticality and port list.
"""

import ipaddress
import re
from pathlib import Path
from typing import Any

import yaml

from network_monitor.models import Criticality, Device, Environment

REQUIRED_FIELDS = ("name", "host", "type", "environment", "criticality")
OPTIONAL_FIELDS = ("ports", "description")
ALLOWED_FIELDS = frozenset(REQUIRED_FIELDS + OPTIONAL_FIELDS)

# Device names end up in reports, logs and file names, so keep them simple:
# start with a letter or digit, then letters, digits, '.', '_' or '-' (max 63).
NAME_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,62}$")

# One DNS label (the parts between dots), per RFC 1123: 1-63 letters, digits
# or hyphens, and it must not start or end with a hyphen.
HOSTNAME_LABEL_PATTERN = re.compile(r"^[A-Za-z0-9]([A-Za-z0-9-]{0,61}[A-Za-z0-9])?$")
MAX_HOSTNAME_LENGTH = 253

MIN_PORT = 1
MAX_PORT = 65535


class InventoryError(Exception):
    """Raised when the inventory cannot be loaded or contains invalid entries.

    The ``errors`` attribute holds every problem found, one message per item.
    """

    def __init__(self, errors: list[str]) -> None:
        self.errors = errors
        super().__init__("\n".join(errors))


def load_inventory(path: Path | str) -> list[Device]:
    """Read a YAML inventory file and return the validated devices.

    Raises InventoryError if the file is missing, unreadable, not valid YAML,
    or contains invalid device entries.
    """
    path = Path(path)

    try:
        text = path.read_text(encoding="utf-8")
    except FileNotFoundError as exc:
        raise InventoryError([f"Inventory file not found: {path}"]) from exc
    except UnicodeDecodeError as exc:
        raise InventoryError([f"Inventory file is not valid UTF-8 text: {path}"]) from exc
    except OSError as exc:
        raise InventoryError([f"Cannot read inventory file {path}: {exc}"]) from exc

    try:
        data = yaml.safe_load(text)
    except yaml.YAMLError as exc:
        raise InventoryError([_describe_yaml_error(path, exc)]) from exc

    return parse_inventory(data)


def parse_inventory(data: Any) -> list[Device]:
    """Validate already-parsed YAML data and convert it into Device objects.

    Every problem is collected before raising, so the user can fix the whole
    file in one go instead of discovering errors one run at a time.
    """
    if data is None:
        raise InventoryError(["Inventory is empty: expected a top-level 'devices' list"])
    if not isinstance(data, dict) or "devices" not in data:
        raise InventoryError(["Inventory must contain a top-level 'devices' key"])

    entries = data["devices"]
    if not isinstance(entries, list) or not entries:
        raise InventoryError(["'devices' must be a non-empty list of devices"])

    errors: list[str] = []
    devices: list[Device] = []
    first_index_by_name: dict[str, int] = {}

    for index, entry in enumerate(entries):
        label = _entry_label(index, entry)

        if not isinstance(entry, dict):
            errors.append(f"{label}: each device must be a set of 'field: value' lines")
            continue

        entry_errors = _validate_entry(entry)
        errors.extend(f"{label}: {message}" for message in entry_errors)

        name = entry.get("name")
        if isinstance(name, str) and name:
            key = name.lower()
            if key in first_index_by_name:
                first = first_index_by_name[key]
                errors.append(f"{label}: duplicate device name (already used by devices[{first}])")
            else:
                first_index_by_name[key] = index

        if not entry_errors:
            devices.append(_build_device(entry))

    if errors:
        raise InventoryError(errors)

    return devices


def _validate_entry(entry: dict[Any, Any]) -> list[str]:
    """Return a list of problems with one device entry (empty list = valid)."""
    errors: list[str] = []

    for field_name in REQUIRED_FIELDS:
        if _is_missing(entry.get(field_name)):
            errors.append(f"missing required field '{field_name}'")

    for field_name in entry:
        if field_name not in ALLOWED_FIELDS:
            errors.append(f"unknown field {field_name!r}")

    name = entry.get("name")
    if not _is_missing(name) and (not isinstance(name, str) or not NAME_PATTERN.match(name)):
        errors.append(
            f"invalid name {name!r}: use up to 63 letters, digits, '.', '_' or '-', "
            "starting with a letter or digit"
        )

    host = entry.get("host")
    if not _is_missing(host):
        host_error = _check_host(host)
        if host_error is not None:
            errors.append(host_error)

    device_type = entry.get("type")
    if not _is_missing(device_type) and not isinstance(device_type, str):
        errors.append(f"type must be text, got {device_type!r}")

    for field_name, choices in (("environment", Environment), ("criticality", Criticality)):
        value = entry.get(field_name)
        allowed = [member.value for member in choices]
        if not _is_missing(value) and value not in allowed:
            errors.append(f"invalid {field_name} {value!r}: expected one of {', '.join(allowed)}")

    errors.extend(_check_ports(entry.get("ports")))

    description = entry.get("description")
    if description is not None and not isinstance(description, str):
        errors.append(f"description must be text, got {description!r}")

    return errors


def _check_host(host: Any) -> str | None:
    """Return an error message if host is not a valid IP address or hostname."""
    if not isinstance(host, str):
        return f"host must be text, got {host!r}"

    if _looks_like_ip_address(host):
        try:
            ipaddress.ip_address(host)
        except ValueError:
            return f"invalid IP address {host!r}"
        return None

    if len(host) > MAX_HOSTNAME_LENGTH:
        return f"invalid hostname {host!r}: longer than {MAX_HOSTNAME_LENGTH} characters"

    labels = host.split(".")
    if not all(HOSTNAME_LABEL_PATTERN.match(label) for label in labels):
        return (
            f"invalid hostname {host!r}: use letters, digits and hyphens between dots, "
            "with no spaces or underscores"
        )

    return None


def _looks_like_ip_address(host: str) -> bool:
    """True if host is written like an IP address (digits and dots, or contains ':')."""
    return ":" in host or host.replace(".", "").isdigit()


def _check_ports(ports: Any) -> list[str]:
    """Return a list of problems with the optional 'ports' field."""
    if ports is None:
        return []
    if not isinstance(ports, list):
        return [f"ports must be a list such as [22, 443], got {ports!r}"]

    errors: list[str] = []
    seen: set[int] = set()

    for port in ports:
        # bool is checked first because Python treats True/False as 1/0 integers.
        if isinstance(port, bool) or not isinstance(port, int):
            errors.append(f"port {port!r} must be a whole number")
        elif not MIN_PORT <= port <= MAX_PORT:
            errors.append(f"port {port} is outside the valid range {MIN_PORT}-{MAX_PORT}")
        elif port in seen:
            errors.append(f"port {port} is listed more than once")
        else:
            seen.add(port)

    return errors


def _is_missing(value: Any) -> bool:
    """True for values that count as 'not provided': None or blank text."""
    return value is None or (isinstance(value, str) and not value.strip())


def _entry_label(index: int, entry: Any) -> str:
    """Build a readable location for error messages, e.g. 'devices[2] (web-01)'."""
    if isinstance(entry, dict) and isinstance(entry.get("name"), str) and entry["name"]:
        return f"devices[{index}] ({entry['name']})"
    return f"devices[{index}]"


def _describe_yaml_error(path: Path, exc: yaml.YAMLError) -> str:
    """Turn a PyYAML error into a one-line message with the line and column."""
    mark = getattr(exc, "problem_mark", None)
    problem = getattr(exc, "problem", None) or "could not be parsed"
    if mark is None:
        return f"Malformed YAML in {path}: {problem}"
    return f"Malformed YAML in {path} at line {mark.line + 1}, column {mark.column + 1}: {problem}"


def _build_device(entry: dict[Any, Any]) -> Device:
    """Create a Device from an entry that has already passed validation."""
    return Device(
        name=entry["name"],
        host=entry["host"],
        device_type=entry["type"],
        environment=Environment(entry["environment"]),
        criticality=Criticality(entry["criticality"]),
        ports=tuple(entry.get("ports") or ()),
        description=entry.get("description") or "",
    )
