"""Deterministic BOOTING progress formatting."""

from __future__ import annotations

from math import floor
from typing import TypedDict


class BootingProgressUpdate(TypedDict):
    """Values rendered for one BOOTING progress update."""

    elapsed_seconds: int
    timeout_seconds: int
    label: str
    percentage: int


def _whole_seconds(value: float) -> int:
    return max(0, floor(value))


def _progress_values(elapsed_seconds: float, timeout_seconds: float) -> tuple[int, int]:
    timeout = _whole_seconds(timeout_seconds)
    elapsed = min(timeout, _whole_seconds(elapsed_seconds))
    return elapsed, timeout


def format_booting_elapsed(elapsed_seconds: float, timeout_seconds: float) -> str:
    """Format a clamped whole-second BOOTING elapsed/timeout label."""
    elapsed, timeout = _progress_values(elapsed_seconds, timeout_seconds)
    return f"{elapsed}s / {timeout}s"


def advance_booting_progress(
    elapsed_seconds: float,
    timeout_seconds: float,
    *,
    step_seconds: float = 1.0,
) -> BootingProgressUpdate:
    """Advance BOOTING progress and return values for the DOM."""
    elapsed, timeout = _progress_values(elapsed_seconds + step_seconds, timeout_seconds)
    percentage = floor(elapsed * 100 / timeout) if timeout else 0
    return {
        "elapsed_seconds": elapsed,
        "timeout_seconds": timeout,
        "label": f"{elapsed}s / {timeout}s",
        "percentage": percentage,
    }
