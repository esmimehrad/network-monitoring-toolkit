"""Smoke tests: prove the package imports and the entry point runs."""

import re
import subprocess
import sys
from pathlib import Path

import network_monitor

PROJECT_ROOT = Path(__file__).resolve().parent.parent


def test_version_uses_semantic_versioning():
    assert re.fullmatch(r"\d+\.\d+\.\d+", network_monitor.__version__)


def test_main_exits_successfully():
    result = subprocess.run(
        [sys.executable, "main.py"],
        cwd=PROJECT_ROOT,
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    assert "environment OK" in result.stdout
