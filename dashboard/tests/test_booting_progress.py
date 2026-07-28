"""Tests for deterministic BOOTING progress calculations."""

import pytest

from dashboard.booting_progress import advance_booting_progress, format_booting_elapsed


@pytest.mark.parametrize(
    ("elapsed", "expected"),
    [
        (-1.5, "0s / 600s"),
        (0.0, "0s / 600s"),
        (182.9, "182s / 600s"),
        (600.0, "600s / 600s"),
        (900.0, "600s / 600s"),
    ],
)
def test_format_booting_elapsed_clamps_and_floors(elapsed: float, expected: str) -> None:
    assert format_booting_elapsed(elapsed, 600.0) == expected


@pytest.mark.parametrize(
    ("elapsed", "expected_elapsed", "expected_percentage"),
    [
        (-1.5, 0, 0),
        (0.0, 0, 0),
        (182.0, 182, 30),
        (600.0, 600, 100),
        (900.0, 600, 100),
    ],
)
def test_advance_booting_progress_returns_deterministic_update(
    elapsed: float,
    expected_elapsed: int,
    expected_percentage: int,
) -> None:
    update = advance_booting_progress(elapsed, 600.0, step_seconds=0.0)
    assert update == {
        "elapsed_seconds": expected_elapsed,
        "timeout_seconds": 600,
        "label": format_booting_elapsed(expected_elapsed, 600.0),
        "percentage": expected_percentage,
    }


def test_advance_booting_progress_advances_one_second() -> None:
    update = advance_booting_progress(182.9, 600.0)
    assert update["elapsed_seconds"] == 183
    assert update["label"] == "183s / 600s"
