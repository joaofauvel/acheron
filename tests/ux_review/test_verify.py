from __future__ import annotations

from pathlib import Path

from acheron.ux_review.verify import verify

_HEAD = "head-sha"


def _write_story(root: Path, *, discovered_via: str = "code-review", metadata: str = "") -> None:
    docs = root / "docs" / "ux_review"
    docs.mkdir(parents=True)
    (docs / "ops.md").write_text(
        f"""# OPS

## OPS-999 — Test story

```yaml
---
id: OPS-999
title: Test story
status: verified
severity: medium
effort: S
discovered_via: [{discovered_via}]
user_facing_surface: cli
silent: false
journey_stage: t1
user_journey: Test journey
files:
  - path: src/test.py
    lines: "1"
{metadata}---
```
""",
        encoding="utf-8",
    )


def test_current_head_marker_resolves_to_supplied_head(tmp_path: Path) -> None:
    _write_story(
        tmp_path,
        metadata=(
            "verified_in: [CURRENT_HEAD]\n"
            "last_verified_at:\n"
            "  commit: CURRENT_HEAD\n"
            "  date: '2026-07-31'\n"
            "verified_by: focused-journey\n"
        ),
    )

    status, message = verify(tmp_path / "docs" / "ux_review", "OPS-999", _HEAD)

    assert status == "PASS"
    assert message == "verified_by=focused-journey"


def test_matching_metadata_passes(tmp_path: Path) -> None:
    _write_story(
        tmp_path,
        metadata=(
            "verified_in: [head-sha]\n"
            "last_verified_at:\n"
            "  commit: head-sha\n"
            "  date: '2026-07-31'\n"
            "verified_by: focused-journey\n"
        ),
    )

    status, message = verify(tmp_path / "docs" / "ux_review", "OPS-999", _HEAD)

    assert status == "PASS"
    assert message == "verified_by=focused-journey"


def test_stale_last_verified_commit_is_partial(tmp_path: Path) -> None:
    _write_story(
        tmp_path,
        metadata=("verified_in: [old-sha]\nlast_verified_at:\n  commit: old-sha\n  date: '2026-07-30'\n"),
    )

    status, message = verify(tmp_path / "docs" / "ux_review", "OPS-999", _HEAD)

    assert status == "PARTIAL"
    assert "last_verified_at.commit" in message


def test_missing_metadata_is_partial(tmp_path: Path) -> None:
    _write_story(tmp_path)

    status, message = verify(tmp_path / "docs" / "ux_review", "OPS-999", _HEAD)

    assert status == "PARTIAL"
    assert "metadata" in message


def test_missing_verified_in_head_is_partial(tmp_path: Path) -> None:
    _write_story(
        tmp_path,
        metadata=("verified_in: []\nlast_verified_at:\n  commit: head-sha\n  date: '2026-07-31'\n"),
    )

    status, message = verify(tmp_path / "docs" / "ux_review", "OPS-999", _HEAD)

    assert status == "PARTIAL"
    assert "verified_in" in message


def test_harness_artifact_does_not_bypass_metadata(tmp_path: Path) -> None:
    _write_story(tmp_path, discovered_via="simulation")
    scenarios = tmp_path / "sim" / "scenarios"
    scenarios.mkdir(parents=True)
    (scenarios / "pricing.py").write_text("# STORY_REF: OPS-999\n", encoding="utf-8")

    status, message = verify(tmp_path / "docs" / "ux_review", "OPS-999", _HEAD)

    assert status == "PARTIAL"
    assert "metadata" in message


def test_matching_metadata_requires_harness_artifact(tmp_path: Path) -> None:
    _write_story(
        tmp_path,
        discovered_via="simulation",
        metadata=("verified_in: [head-sha]\nlast_verified_at:\n  commit: head-sha\n  date: '2026-07-31'\n"),
    )

    status, message = verify(tmp_path / "docs" / "ux_review", "OPS-999", _HEAD)

    assert status == "FAIL"
    assert "harness artifact" in message


def test_matching_metadata_and_harness_artifact_pass(tmp_path: Path) -> None:
    _write_story(
        tmp_path,
        discovered_via="simulation",
        metadata=("verified_in: [head-sha]\nlast_verified_at:\n  commit: head-sha\n  date: '2026-07-31'\n"),
    )
    scenarios = tmp_path / "sim" / "scenarios"
    scenarios.mkdir(parents=True)
    artifact = scenarios / "pricing.py"
    artifact.write_text("# STORY_REF: OPS-999\n", encoding="utf-8")

    status, message = verify(tmp_path / "docs" / "ux_review", "OPS-999", _HEAD)

    assert status == "PASS"
    assert message == f"artifact={artifact}"
