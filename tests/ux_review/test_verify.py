from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

import acheron.ux_review.discovery as discovery_module
import acheron.ux_review.verify as verify_module
from acheron.ux_review.verify import main, verify

_HEAD = "head-sha"


def _write_story(
    root: Path,
    *,
    discovered_via: str = "code-review",
    status: str = "verified",
    metadata: str = "",
) -> None:
    docs = root / "docs" / "ux_review"
    docs.mkdir(parents=True)
    (docs / "ops.md").write_text(
        f"""# OPS

## OPS-999 — Test story

```yaml
---
id: OPS-999
title: Test story
status: {status}
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


def test_current_head_marker_resolves_only_to_actual_head(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
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

    monkeypatch.setattr(verify_module, "_repository_head", lambda _root: _HEAD)
    status, message = verify(tmp_path / "docs" / "ux_review", "OPS-999", _HEAD)

    assert status == "PASS"
    assert message == "verified_by=focused-journey"


def test_current_head_marker_rejects_stale_head(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _write_story(
        tmp_path,
        metadata=("verified_in: [CURRENT_HEAD]\nlast_verified_at:\n  commit: CURRENT_HEAD\n  date: '2026-07-31'\n"),
    )
    monkeypatch.setattr(verify_module, "_repository_head", lambda _root: _HEAD)

    status, message = verify(tmp_path / "docs" / "ux_review", "OPS-999", "stale-sha")

    assert status == "PARTIAL"
    assert "last_verified_at.commit" in message


def test_stale_head_main_exits_nonzero(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _write_story(
        tmp_path,
        metadata=("verified_in: [CURRENT_HEAD]\nlast_verified_at:\n  commit: CURRENT_HEAD\n  date: '2026-07-31'\n"),
    )
    monkeypatch.setattr(verify_module, "_repository_head", lambda _root: _HEAD)
    monkeypatch.setattr(
        sys,
        "argv",
        ["ux-verify", "--root", str(tmp_path / "docs" / "ux_review"), "--id", "OPS-999", "--head", "stale-sha"],
    )

    assert main() == 1


def test_literal_current_head_resolves_actual_head(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _write_story(
        tmp_path,
        metadata=("verified_in: [CURRENT_HEAD]\nlast_verified_at:\n  commit: CURRENT_HEAD\n  date: '2026-07-31'\n"),
    )
    monkeypatch.setattr(verify_module, "_repository_head", lambda _root: _HEAD)

    status, _message = verify(tmp_path / "docs" / "ux_review", "OPS-999", "CURRENT_HEAD")

    assert status == "PASS"


def test_default_head_resolves_actual_head(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _write_story(
        tmp_path,
        metadata=("verified_in: [CURRENT_HEAD]\nlast_verified_at:\n  commit: CURRENT_HEAD\n  date: '2026-07-31'\n"),
    )
    monkeypatch.setattr(verify_module, "_repository_head", lambda _root: _HEAD)

    status, _message = verify(tmp_path / "docs" / "ux_review", "OPS-999", "HEAD")

    assert status == "PASS"


def test_obsolete_story_is_not_currently_verified(tmp_path: Path) -> None:
    _write_story(
        tmp_path,
        status="obsolete",
        metadata=("verified_in: [CURRENT_HEAD]\nlast_verified_at:\n  commit: CURRENT_HEAD\n  date: '2026-07-31'\n"),
    )

    status, message = verify(tmp_path / "docs" / "ux_review", "OPS-999", _HEAD)

    assert status == "PARTIAL"
    assert "obsolete" in message


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
    (scenarios / "pricing.py").write_text('''"""STORY_REF: OPS-999\n\nTest journey\n"""\n''', encoding="utf-8")

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


def test_matching_metadata_and_harness_artifact_pass(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _write_story(
        tmp_path,
        discovered_via="simulation",
        metadata=("verified_in: [head-sha]\nlast_verified_at:\n  commit: head-sha\n  date: '2026-07-31'\n"),
    )
    scenarios = tmp_path / "sim" / "scenarios"
    scenarios.mkdir(parents=True)
    artifact = scenarios / "pricing.py"
    artifact.write_text('''"""STORY_REF: OPS-999\n\nTest journey\n"""\n''', encoding="utf-8")

    monkeypatch.setattr(discovery_module, "_post_fixed_commit", lambda *_args: True)
    status, message = verify(tmp_path / "docs" / "ux_review", "OPS-999", _HEAD)

    assert status == "PASS"
    assert message == f"artifact={artifact}"


def test_harness_marker_requires_exact_story_id(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _write_story(
        tmp_path,
        discovered_via="simulation",
        metadata=(
            "fixed_in: [head-sha]\n"
            "verified_in: [head-sha]\n"
            "last_verified_at:\n"
            "  commit: head-sha\n"
            "  date: '2026-07-31'\n"
        ),
    )
    scenarios = tmp_path / "sim" / "scenarios"
    scenarios.mkdir(parents=True)
    (scenarios / "pricing.py").write_text('''"""STORY_REF: OPS-9999\n\nTest journey\n"""\n''', encoding="utf-8")
    monkeypatch.setattr(discovery_module, "_post_fixed_commit", lambda *_args: True)

    status, message = verify(tmp_path / "docs" / "ux_review", "OPS-999", _HEAD)

    assert status == "FAIL"
    assert "harness artifact" in message


def test_historical_harness_head_accepts_post_fixed_commit(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _write_story(
        tmp_path,
        discovered_via="simulation",
        metadata=(
            "fixed_in: [fixed-sha]\n"
            "verified_in: [historical-sha]\n"
            "last_verified_at:\n"
            "  commit: historical-sha\n"
            "  date: '2026-07-31'\n"
        ),
    )
    scenarios = tmp_path / "sim" / "scenarios"
    scenarios.mkdir(parents=True)
    artifact = scenarios / "pricing.py"
    artifact.write_text('''"""STORY_REF: OPS-999\n\nTest journey\n"""\n''', encoding="utf-8")
    monkeypatch.setattr(verify_module, "_repository_head", lambda _root: "current-sha")
    monkeypatch.setattr(discovery_module, "_repository_head", lambda _root: "current-sha")
    import shutil

    monkeypatch.setattr(shutil, "which", lambda _name: "/usr/bin/git")
    monkeypatch.setattr(
        subprocess,
        "run",
        lambda args, **_kwargs: subprocess.CompletedProcess(args, 0, "", ""),
    )

    status, message = verify(tmp_path / "docs" / "ux_review", "OPS-999", "historical-sha")

    assert status == "PASS"
    assert message == f"artifact={artifact}"


def test_harness_artifact_requires_git_fixed_commit_evidence(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _write_story(
        tmp_path,
        discovered_via="simulation",
        metadata=(
            "fixed_in: [head-sha]\n"
            "verified_in: [head-sha]\n"
            "last_verified_at:\n"
            "  commit: head-sha\n"
            "  date: '2026-07-31'\n"
        ),
    )
    scenarios = tmp_path / "sim" / "scenarios"
    scenarios.mkdir(parents=True)
    (scenarios / "pricing.py").write_text('''"""STORY_REF: OPS-999\n\nTest journey\n"""\n''', encoding="utf-8")
    monkeypatch.setattr(discovery_module, "_repository_head", lambda _root: None)

    status, message = verify(tmp_path / "docs" / "ux_review", "OPS-999", _HEAD)

    assert status == "FAIL"
    assert "harness artifact" in message
