"""Smoke tests: prove the package imports and the entry point runs end to end."""

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
        timeout=30,
        check=False,
    )


def test_version_uses_semantic_versioning():
    assert re.fullmatch(r"\d+\.\d+\.\d+", network_monitor.__version__)


def test_main_exits_successfully_with_default_inventory():
    result = run_main()

    assert result.returncode == 0, result.stdout + result.stderr
    assert "environment OK" in result.stdout
    assert "Inventory OK" in result.stdout


def test_main_exits_with_error_for_invalid_inventory(tmp_path):
    bad_inventory = tmp_path / "inventory.yaml"
    bad_inventory.write_text("devices: []\n", encoding="utf-8")

    result = run_main(str(bad_inventory))

    assert result.returncode == 1
    assert "Inventory INVALID" in result.stdout
