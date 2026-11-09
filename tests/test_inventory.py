"""Tests for inventory loading and validation."""

from pathlib import Path

import pytest

from network_monitor.inventory import InventoryError, load_inventory, parse_inventory
from network_monitor.models import Criticality, Device, Environment

PROJECT_ROOT = Path(__file__).resolve().parent.parent


def valid_entry(**overrides):
    """Return one valid device entry as a dict; keyword arguments replace fields."""
    entry = {
        "name": "core-router-01",
        "host": "192.168.1.1",
        "type": "router",
        "environment": "production",
        "criticality": "high",
        "ports": [22, 443],
    }
    entry.update(overrides)
    return entry


def errors_for(*entries):
    """Validate the given entries and return the list of error messages."""
    with pytest.raises(InventoryError) as exc_info:
        parse_inventory({"devices": list(entries)})
    return exc_info.value.errors


def write_file(tmp_path, text):
    """Write text to a temporary inventory file and return its path."""
    path = tmp_path / "inventory.yaml"
    path.write_text(text, encoding="utf-8")
    return path


# --- Loading valid inventories -------------------------------------------------


def test_load_valid_inventory_file(tmp_path):
    path = write_file(
        tmp_path,
        """
devices:
  - name: web-server-01
    host: example.local
    type: server
    environment: development
    criticality: medium
    ports: [80, 443]
""",
    )

    devices = load_inventory(path)

    assert devices == [
        Device(
            name="web-server-01",
            host="example.local",
            device_type="server",
            environment=Environment.DEVELOPMENT,
            criticality=Criticality.MEDIUM,
            ports=(80, 443),
            description="",
        )
    ]


def test_project_inventory_file_is_valid():
    devices = load_inventory(PROJECT_ROOT / "config" / "inventory.yaml")

    assert len(devices) >= 1


def test_ports_and_description_are_optional():
    entry = valid_entry()
    del entry["ports"]

    [device] = parse_inventory({"devices": [entry]})

    assert device.ports == ()
    assert device.description == ""


@pytest.mark.parametrize(
    "host",
    ["192.168.1.1", "10.0.0.254", "::1", "2001:db8::10", "localhost", "web-01.example.local"],
)
def test_valid_hosts_are_accepted(host):
    [device] = parse_inventory({"devices": [valid_entry(host=host)]})

    assert device.host == host


# --- File and YAML problems ----------------------------------------------------


def test_missing_file_is_reported(tmp_path):
    with pytest.raises(InventoryError, match="not found"):
        load_inventory(tmp_path / "does-not-exist.yaml")


def test_malformed_yaml_is_reported_with_line_number(tmp_path):
    path = write_file(tmp_path, "devices:\n  - name: router-01\n    host: [unclosed\n")

    with pytest.raises(InventoryError, match=r"Malformed YAML .* line \d+"):
        load_inventory(path)


def test_empty_file_is_reported(tmp_path):
    path = write_file(tmp_path, "# only a comment\n")

    with pytest.raises(InventoryError, match="empty"):
        load_inventory(path)


@pytest.mark.parametrize(
    "data",
    [
        ["not", "a", "mapping"],
        {"routers": []},
        {"devices": []},
        {"devices": "core-router-01"},
    ],
)
def test_wrong_top_level_structure_is_rejected(data):
    with pytest.raises(InventoryError):
        parse_inventory(data)


def test_device_entry_must_be_a_mapping():
    errors = errors_for("core-router-01")

    assert "devices[0]: each device must be a set of 'field: value' lines" in errors


# --- Field validation ----------------------------------------------------------


@pytest.mark.parametrize("field", ["name", "host", "type", "environment", "criticality"])
def test_missing_required_field_is_reported(field):
    entry = valid_entry()
    del entry[field]

    errors = errors_for(entry)

    assert any(f"missing required field '{field}'" in error for error in errors)


def test_blank_required_field_counts_as_missing():
    errors = errors_for(valid_entry(type="   "))

    assert any("missing required field 'type'" in error for error in errors)


def test_unknown_field_is_reported():
    errors = errors_for(valid_entry(critcality="high"))

    assert any("unknown field 'critcality'" in error for error in errors)


@pytest.mark.parametrize("host", ["192.168.1.300", "256.1.1.1", "10.0.0", "fe80::zz"])
def test_invalid_ip_addresses_are_rejected(host):
    errors = errors_for(valid_entry(host=host))

    assert any("invalid IP address" in error for error in errors)


@pytest.mark.parametrize(
    "host",
    ["-router.example", "web_server.local", "my server", "a..b", "x" * 64 + ".com"],
)
def test_invalid_hostnames_are_rejected(host):
    errors = errors_for(valid_entry(host=host))

    assert any("invalid hostname" in error for error in errors)


def test_host_must_be_text():
    errors = errors_for(valid_entry(host=10))

    assert any("host must be text" in error for error in errors)


@pytest.mark.parametrize(
    ("port", "expected"),
    [
        (0, "outside the valid range"),
        (65536, "outside the valid range"),
        (-22, "outside the valid range"),
        ("443", "must be a whole number"),
        (22.5, "must be a whole number"),
        (True, "must be a whole number"),
    ],
)
def test_invalid_ports_are_rejected(port, expected):
    errors = errors_for(valid_entry(ports=[port]))

    assert any(expected in error for error in errors)


def test_duplicate_port_is_rejected():
    errors = errors_for(valid_entry(ports=[443, 443]))

    assert any("listed more than once" in error for error in errors)


def test_ports_must_be_a_list():
    errors = errors_for(valid_entry(ports=443))

    assert any("ports must be a list" in error for error in errors)


@pytest.mark.parametrize(
    ("field", "value"),
    [("environment", "prodution"), ("environment", "Production"), ("criticality", "critical")],
)
def test_unknown_environment_or_criticality_is_rejected(field, value):
    errors = errors_for(valid_entry(**{field: value}))

    assert any(f"invalid {field} '{value}'" in error for error in errors)


def test_invalid_device_name_is_rejected():
    errors = errors_for(valid_entry(name="core router 01"))

    assert any("invalid name" in error for error in errors)


def test_yaml_boolean_trap_is_caught(tmp_path):
    # YAML 1.1 turns unquoted yes/no/on/off into True/False, so 'name: yes'
    # is not the text "yes". Validation must catch it instead of crashing.
    path = write_file(
        tmp_path,
        """
devices:
  - name: yes
    host: 10.0.0.5
    type: switch
    environment: staging
    criticality: low
""",
    )

    with pytest.raises(InventoryError, match="invalid name True"):
        load_inventory(path)


def test_duplicate_names_are_rejected_ignoring_case():
    errors = errors_for(valid_entry(name="Core-Router-01"), valid_entry(name="core-router-01"))

    assert any("duplicate device name" in error for error in errors)


def test_all_problems_are_reported_together():
    errors = errors_for(
        valid_entry(name="router-a", host="192.168.1.300"),
        valid_entry(name="router-b", environment="prodution", ports=[70000]),
    )

    assert len(errors) == 3
    assert any(error.startswith("devices[0] (router-a)") for error in errors)
    assert any(error.startswith("devices[1] (router-b)") for error in errors)


def test_description_must_be_text():
    errors = errors_for(valid_entry(description=12345))

    assert any("description must be text" in error for error in errors)
