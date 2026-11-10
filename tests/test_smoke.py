"""Smoke tests: prove the package imports and the entry point runs end to end.

These tests only touch 127.0.0.1 (this computer), never real external servers.
"""

import re
import subprocess
import sys
from pathlib import Path

import network_monitor

PROJECT_ROOT = Path(__file__).resolve().parent.parent


def run_main(*args: str) -> subprocess.CompletedProcess[str]:
    """Run 'python main.py <args>' from the project root and capture its output."""
    return subprocess.run(
        [sys.executable, "main.py", *args],
        cwd=PROJECT_ROOT,
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )


def write_local_inventory(tmp_path: Path, port: int) -> Path:
    """Write an inventory with one device: this computer on the given TCP port."""
    path = tmp_path / "inventory.yaml"
    path.write_text(
        f"""
devices:
  - name: localhost-test
    host: 127.0.0.1
    type: server
    environment: development
    criticality: low
    ports: [{port}]
""",
        encoding="utf-8",
    )
    return path


def test_version_uses_semantic_versioning():
    assert re.fullmatch(r"\d+\.\d+\.\d+", network_monitor.__version__)


def test_main_exits_0_when_all_checks_succeed(tmp_path, listening_port):
    inventory = write_local_inventory(tmp_path, listening_port)

    result = run_main(str(inventory))

    assert result.returncode == 0, result.stdout + result.stderr
    assert "environment OK" in result.stdout
    assert "Inventory OK" in result.stdout
    assert "0 failed" in result.stdout
    assert "Devices: 1/1 reachable" in result.stdout


def test_main_exits_2_when_a_check_fails(tmp_path, closed_port):
    inventory = write_local_inventory(tmp_path, closed_port)

    result = run_main(str(inventory))

    assert result.returncode == 2, result.stdout + result.stderr
    assert "refused" in result.stdout


def test_main_exits_1_for_invalid_inventory(tmp_path):
    bad_inventory = tmp_path / "inventory.yaml"
    bad_inventory.write_text("devices: []\n", encoding="utf-8")

    result = run_main(str(bad_inventory))

    assert result.returncode == 1
    assert "Inventory INVALID" in result.stdout
