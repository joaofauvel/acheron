"""Verify a single UX story is mechanically verified."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from pydantic import ValidationError

from acheron.ux_review.discovery import artifact_path_for
from acheron.ux_review.schema import Story
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


def verify(root: Path, story_id: str, head_sha: str) -> tuple[str, str]:
    """Verify a single story. Returns (status, message).

    status is one of: PASS, PARTIAL, FAIL.
    """
    found = find_story(root, story_id)
    if found is None:
        result = ("FAIL", f"story {story_id} not found in any theme file")
    else:
        _path, story = found
        verified_commit = story.last_verified_at.get("commit")
        if verified_commit != head_sha:
            if verified_commit is None:
                result = ("PARTIAL", "verification metadata is missing last_verified_at.commit")
            else:
                result = (
                    "PARTIAL",
                    f"last_verified_at.commit={verified_commit} does not match head={head_sha}",
                )
        elif head_sha not in story.verified_in:
            result = ("PARTIAL", f"head={head_sha} is missing from verified_in")
        elif story.discovered_via and story.discovered_via[0] in {"simulation", "first-run"}:
            artifact = artifact_path_for(story, root, head_sha)
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
    return 0 if status != "FAIL" else 1


if __name__ == "__main__":
    sys.exit(main())
