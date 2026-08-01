from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

import pytest

import acheron.ux_review.discovery as discovery_module
import acheron.ux_review.verify as verify_module
from acheron.ux_review.schema import Story
from acheron.ux_review.verify import main, verify

_HEAD = "head-sha"


@pytest.fixture(autouse=True)
def _fake_tree_fingerprint(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(verify_module, "repository_tree_fingerprint", lambda *_args: "tree-sha")
    monkeypatch.setattr(verify_module, "_commit_exists", lambda *_args: True)
    monkeypatch.setattr(verify_module, "_is_ancestor", lambda *_args: True)


def _write_story(
    root: Path,
    *,
    discovered_via: str = "code-review",
    status: str = "verified",
    metadata: str = "",
) -> None:
    docs = root / "docs" / "ux_review"
    docs.mkdir(parents=True)
    if "commit: CURRENT_HEAD" in metadata and "tree:" not in metadata:
        metadata = metadata.replace("  date:", "  tree: tree-sha\n  date:", 1)
    fixed_in = "" if "fixed_in:" in metadata else "fixed_in: [head-sha]\n"
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
{fixed_in}{metadata}---
```
""",
        encoding="utf-8",
    )


def test_tree_fingerprint_changes_for_git_mode_changes(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(shutil, "which", lambda _name: "/usr/bin/git")
    tree = [b"100644 blob abc\ttracked.py\0"]
    monkeypatch.setattr(
        subprocess,
        "run",
        lambda *_args, **_kwargs: subprocess.CompletedProcess([], 0, tree[0], b""),
    )

    first = discovery_module.repository_tree_fingerprint(tmp_path)
    tree[0] = b"100755 blob abc\ttracked.py\0"
    mode_changed = discovery_module.repository_tree_fingerprint(tmp_path)
    tree[0] = b"100644 tree abc\ttracked.py\0"
    type_changed = discovery_module.repository_tree_fingerprint(tmp_path)

    assert first is not None
    assert mode_changed is not None
    assert type_changed is not None
    assert first != mode_changed
    assert mode_changed != type_changed


def test_abbreviated_current_head_is_canonicalized(tmp_path: Path) -> None:
    _write_story(
        tmp_path,
        metadata=(
            "fixed_in: [CURRENT_HEAD]\n"
            "verified_in: [CURRENT_HEAD]\n"
            "last_verified_at:\n"
            "  commit: CURRENT_HEAD\n"
            "  date: '2026-07-31'\n"
        ),
    )
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
    subprocess.run(["git", "-C", str(tmp_path), "config", "user.email", "test@example.com"], check=True)
    subprocess.run(["git", "-C", str(tmp_path), "config", "user.name", "Test"], check=True)
    subprocess.run(["git", "-C", str(tmp_path), "add", "."], check=True)
    subprocess.run(["git", "-C", str(tmp_path), "commit", "-qm", "test"], check=True)
    head = subprocess.run(
        ["git", "-C", str(tmp_path), "rev-parse", "HEAD"], check=True, capture_output=True, text=True
    ).stdout.strip()

    status, message = verify(tmp_path / "docs" / "ux_review", "OPS-999", head[:8])

    assert status == "PASS"
    assert message == "verified at CURRENT_HEAD"


def test_explicit_head_rejects_non_commit(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _write_story(tmp_path, metadata="verified_in: [not-a-commit]\nlast_verified_at:\n  commit: not-a-commit\n")
    monkeypatch.setattr(verify_module, "_commit_exists", lambda *_args: False)

    status, message = verify(tmp_path / "docs" / "ux_review", "OPS-999", "not-a-commit")

    assert status == "FAIL"
    assert "valid Git commit" in message


def test_verified_story_requires_fixed_commit_evidence(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _write_story(
        tmp_path,
        metadata=("fixed_in: []\nverified_in: [head-sha]\nlast_verified_at:\n  commit: head-sha\n"),
    )

    status, message = verify(tmp_path / "docs" / "ux_review", "OPS-999", _HEAD)

    assert status == "PARTIAL"
    assert "fixed_in" in message


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


def test_missing_metadata_main_exits_nonzero(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _write_story(tmp_path)
    monkeypatch.setattr(
        sys,
        "argv",
        ["ux-verify", "--root", str(tmp_path / "docs" / "ux_review"), "--id", "OPS-999", "--head", _HEAD],
    )

    assert main() == 1


def test_missing_verified_in_main_exits_nonzero(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _write_story(
        tmp_path,
        metadata=("verified_in: []\nlast_verified_at:\n  commit: head-sha\n  date: '2026-07-31'\n"),
    )
    monkeypatch.setattr(
        sys,
        "argv",
        ["ux-verify", "--root", str(tmp_path / "docs" / "ux_review"), "--id", "OPS-999", "--head", _HEAD],
    )

    assert main() == 1


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
    monkeypatch.setattr(discovery_module, "_artifact_paths", lambda *_args: (artifact,))
    monkeypatch.setattr(discovery_module, "_artifact_source", lambda *_args: artifact.read_text(encoding="utf-8"))
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
    monkeypatch.setattr(discovery_module, "_artifact_paths", lambda *_args: (scenarios / "pricing.py",))
    monkeypatch.setattr(discovery_module, "_artifact_source", lambda *_args: (scenarios / "pricing.py").read_text())

    status, message = verify(tmp_path / "docs" / "ux_review", "OPS-999", _HEAD)

    assert status == "FAIL"
    assert "harness artifact" in message


def test_historical_artifact_reads_requested_revision(tmp_path: Path) -> None:
    def git(*args: str) -> str:
        result = subprocess.run(
            ["git", "-C", str(tmp_path), *args],
            check=True,
            capture_output=True,
            text=True,
        )
        return result.stdout.strip()

    git("init", "-q")
    git("config", "user.email", "test@example.invalid")
    git("config", "user.name", "Test")
    scenarios = tmp_path / "sim" / "scenarios"
    scenarios.mkdir(parents=True)
    artifact = scenarios / "pricing.py"
    artifact.write_text('''"""No story marker yet."""\n''', encoding="utf-8")
    git("add", ".")
    git("commit", "-qm", "initial")
    historical_head = git("rev-parse", "HEAD")
    artifact.write_text('''"""STORY_REF: OPS-999\\n\\nTest journey\\n"""\n''', encoding="utf-8")
    git("add", ".")
    git("commit", "-qm", "add marker")

    story = Story.model_validate(
        {
            "id": "OPS-999",
            "title": "Test story",
            "status": "verified",
            "severity": "medium",
            "effort": "S",
            "discovered_via": ["simulation"],
            "user_facing_surface": "cli",
            "silent": False,
            "journey_stage": "t1",
            "user_journey": "Test journey",
            "files": [{"path": "sim/scenarios/pricing.py", "lines": "1"}],
            "fixed_in": [historical_head],
        }
    )
    assert discovery_module.artifact_path_for(story, tmp_path / "docs" / "ux_review", historical_head) is None
    current_head = git("rev-parse", "HEAD")
    assert discovery_module.artifact_path_for(story, tmp_path / "docs" / "ux_review", current_head) == artifact


def test_invalid_trailing_fixed_commit_is_rejected(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _write_story(
        tmp_path,
        metadata=(
            "fixed_in: [head-sha, invalid-trailing-sha]\n"
            "verified_in: [head-sha]\n"
            "last_verified_at:\n"
            "  commit: head-sha\n"
            "  date: '2026-07-31'\n"
        ),
    )
    monkeypatch.setattr(verify_module, "_commit_exists", lambda _root, commit: commit != "invalid-trailing-sha")

    status, message = verify(tmp_path / "docs" / "ux_review", "OPS-999", _HEAD)

    assert status == "FAIL"
    assert "invalid-trailing-sha" in message


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
    monkeypatch.setattr(discovery_module, "_artifact_paths", lambda *_args: (artifact,))
    monkeypatch.setattr(discovery_module, "_artifact_source", lambda *_args: artifact.read_text(encoding="utf-8"))

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


def test_current_head_requires_repository_head(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _write_story(
        tmp_path,
        metadata=("verified_in: [CURRENT_HEAD]\nlast_verified_at:\n  commit: CURRENT_HEAD\n  date: '2026-07-31'\n"),
    )
    monkeypatch.setattr(verify_module, "_repository_head", lambda _root: None)

    status, message = verify(tmp_path / "docs" / "ux_review", "OPS-999", "CURRENT_HEAD")

    assert status == "FAIL"
    assert "repository HEAD" in message


def test_current_head_attestation_rejects_later_tree_change(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _write_story(
        tmp_path,
        metadata=("verified_in: [CURRENT_HEAD]\nlast_verified_at:\n  commit: CURRENT_HEAD\n  date: '2026-07-31'\n"),
    )
    monkeypatch.setattr(verify_module, "_repository_head", lambda _root: _HEAD)
    monkeypatch.setattr(verify_module, "repository_tree_fingerprint", lambda *_args: "changed-tree")

    status, message = verify(tmp_path / "docs" / "ux_review", "OPS-999", _HEAD)

    assert status == "FAIL"
    assert "attestation" in message
