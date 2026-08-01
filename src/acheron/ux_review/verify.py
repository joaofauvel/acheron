"""Verify a single UX story is mechanically verified."""

from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
from pathlib import Path

from pydantic import ValidationError

from acheron.ux_review.discovery import artifact_path_for, repository_tree_fingerprint
from acheron.ux_review.schema import CURRENT_HEAD, Story
from acheron.ux_review.validate import _parse_story_blocks


def find_story(root: Path, story_id: str) -> tuple[Path, Story] | None:
    """Locate a story by ID across the theme files."""
    for story_file in ("deploy.md", "ops.md", "maint.md"):
        path = root / story_file
        if not path.exists():
            continue
        for sid, data in _parse_story_blocks(path.read_text(encoding="utf-8")):
            if sid != story_id or isinstance(data, str):
                continue
            try:
                return path, Story.model_validate(data)
            except ValidationError:
                return None
    return None


def _repository_head(root: Path) -> str | None:
    """Return the actual repository HEAD containing ``root`` when available."""
    try:
        result = subprocess.run(  # noqa: S603 - executable and arguments are fixed
            ["git", "-C", str(root), "rev-parse", "HEAD"],  # noqa: S607 - fixed executable
            check=True,
            capture_output=True,
            text=True,
        )
    except OSError, subprocess.CalledProcessError:
        return None
    return result.stdout.strip() or None


def _commit_exists(repo_root: Path, commit: str) -> bool:
    """Return whether ``commit`` names a commit reachable in ``repo_root``."""
    git = shutil.which("git")
    if git is None:
        return False
    try:
        subprocess.run(  # noqa: S603 - executable and arguments are fixed
            [git, "-C", str(repo_root), "rev-parse", "--verify", f"{commit}^{{commit}}"],
            check=True,
            capture_output=True,
            text=True,
        )
    except OSError, subprocess.CalledProcessError:
        return False
    return True


def _is_ancestor(repo_root: Path, ancestor: str, descendant: str) -> bool:
    """Return whether ``ancestor`` is reachable from ``descendant``."""
    git = shutil.which("git")
    if git is None:
        return False
    try:
        subprocess.run(  # noqa: S603 - executable and arguments are fixed
            [git, "-C", str(repo_root), "merge-base", "--is-ancestor", ancestor, descendant],
            check=True,
            capture_output=True,
            text=True,
        )
    except OSError, subprocess.CalledProcessError:
        return False
    return True


def _validate_evidence_commits(
    repo_root: Path,
    story: Story,
    verified_commit: str | None,
    requested_head: str,
    *,
    marker_matches: bool,
) -> tuple[str, str] | None:
    """Validate commit existence and ancestry recorded by a story."""
    metadata_commits = [*story.fixed_in, *story.verified_in]
    if verified_commit is not None:
        metadata_commits.append(verified_commit)
    for commit in metadata_commits:
        if commit != CURRENT_HEAD and not _commit_exists(repo_root, commit):
            return ("FAIL", f"verification metadata names invalid Git commit: {commit}")
    for commit in story.fixed_in:
        if commit == CURRENT_HEAD:
            if not marker_matches:
                return ("PARTIAL", f"fixed_in=CURRENT_HEAD does not match head={requested_head}")
            continue
        if not _is_ancestor(repo_root, commit, requested_head):
            return ("FAIL", f"fixed_in commit {commit} is not an ancestor of head={requested_head}")
    return None


def verify(root: Path, story_id: str, head_sha: str) -> tuple[str, str]:  # noqa: C901, PLR0911, PLR0912
    """Verify a single story. Returns (status, message).

    status is one of: PASS, PARTIAL, FAIL.
    """
    found = find_story(root, story_id)
    if found is None:
        result = ("FAIL", f"story {story_id} not found in any theme file")
    else:
        _path, story = found
        if story.status != "verified":
            return ("PARTIAL", f"story status={story.status} is not currently verified")
        actual_head = _repository_head(root)
        marker_input = head_sha in {"HEAD", CURRENT_HEAD}
        if marker_input:
            if actual_head is None:
                return ("FAIL", "repository HEAD is unavailable for CURRENT_HEAD verification")
            requested_head = actual_head
        else:
            requested_head = head_sha
        repo_root = root.parent.parent
        if not _commit_exists(repo_root, requested_head):
            return ("FAIL", f"head={requested_head} is not a valid Git commit")
        marker_matches = actual_head is not None and requested_head == actual_head
        verified_commit = story.last_verified_at.get("commit")
        if not story.fixed_in:
            return ("PARTIAL", "verified story is missing fixed_in evidence")
        evidence_error = _validate_evidence_commits(
            repo_root,
            story,
            verified_commit,
            requested_head,
            marker_matches=marker_matches,
        )
        if evidence_error is not None:
            return evidence_error
        if verified_commit == CURRENT_HEAD:
            if not marker_matches:
                return ("PARTIAL", f"last_verified_at.commit=CURRENT_HEAD does not match head={requested_head}")
            if actual_head is None:
                return ("FAIL", "repository HEAD is unavailable for CURRENT_HEAD attestation")
            expected_tree = repository_tree_fingerprint(root.parent.parent, actual_head)
            attested_tree = story.last_verified_at.get("tree")
            if expected_tree is None or attested_tree != expected_tree:
                return ("FAIL", "CURRENT_HEAD attestation does not match the committed tree")
        resolved_commit = requested_head if verified_commit == CURRENT_HEAD else verified_commit
        if resolved_commit != requested_head:
            if verified_commit is None:
                result = ("PARTIAL", "verification metadata is missing last_verified_at.commit")
            else:
                result = (
                    "PARTIAL",
                    f"last_verified_at.commit={verified_commit} does not match head={requested_head}",
                )
        elif not any(
            commit == requested_head or (commit == CURRENT_HEAD and marker_matches) for commit in story.verified_in
        ):
            result = ("PARTIAL", f"head={requested_head} is missing from verified_in")
        elif story.discovered_via and story.discovered_via[0] in {"simulation", "first-run"}:
            artifact = artifact_path_for(story, root, requested_head)
            if artifact is None:
                result = (
                    "FAIL",
                    f"no harness artifact for discovered_via={story.discovered_via[0]}",
                )
            else:
                result = ("PASS", f"artifact={artifact}")
        elif story.verified_by:
            result = ("PASS", f"verified_by={story.verified_by}")
        else:
            result = ("PASS", f"verified at {verified_commit}")
    return result


def main() -> int:
    """CLI entry point: verify a single story and print the result."""
    parser = argparse.ArgumentParser(description="Verify a single UX story")
    parser.add_argument("--root", type=Path, default=Path("docs/ux_review"))
    parser.add_argument("--id", required=True, help="Story ID (e.g., DEPLOY-001)")
    parser.add_argument("--head", default="HEAD", help="Git commit SHA")
    args = parser.parse_args()
    status, msg = verify(args.root, args.id, args.head)
    print(f"ux-verify {args.id}: {status} - {msg}")  # noqa: T201
    return 0 if status == "PASS" else 1


if __name__ == "__main__":
    sys.exit(main())
