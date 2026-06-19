"""Shared pytest fixtures for the tests/ tree."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest


@pytest.fixture
def dev_certs(tmp_path: Path) -> Path:
    """Run the dev cert generator and return the cert dir."""
    script = Path(__file__).resolve().parents[1] / "scripts" / "generate_dev_certs.py"
    subprocess.run(
        [sys.executable, str(script), "--out-dir", str(tmp_path)],
        check=True,
        capture_output=True,
    )
    return tmp_path
