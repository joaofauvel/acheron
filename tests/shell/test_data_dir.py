"""Tests for data dir handling."""

from pathlib import Path

import pytest

from acheron.shell.cache import PlanCache


def test_data_dir_is_public_attribute(tmp_path: Path) -> None:
    """PlanCache exposes its data_dir for startup checks."""
    cache = PlanCache(data_dir=tmp_path)
    assert cache.data_dir == tmp_path
