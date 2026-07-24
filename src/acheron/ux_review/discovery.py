"""Map a story's `discovered_via` channels to the harness artifacts that ground them."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from pathlib import Path

    from acheron.ux_review.schema import Story


def artifact_path_for(story: Story, root: Path, head_sha: str) -> Path | None:
    """Return the harness artifact path for the strongest evidence channel.

    Returns None if the strongest channel does not require a harness artifact
    (e.g., code-review, on-call, audit, user-feedback) or if the artifact is
    not yet present on disk.
    """
    del head_sha
    if not story.discovered_via:
        return None
    strongest = story.discovered_via[0]
    repo_root = root.parent.parent
    if strongest == "simulation":
        for path in (repo_root / "sim" / "scenarios").glob("*.py"):
            if f"STORY_REF: {story.id}" in path.read_text(encoding="utf-8"):
                return path
    elif strongest == "first-run":
        for path in (repo_root / "tests" / "first_run").glob("test_*.py"):
            if f"STORY_REF: {story.id}" in path.read_text(encoding="utf-8"):
                return path
    return None
