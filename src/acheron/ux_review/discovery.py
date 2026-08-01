"""Map UX review stories to harness artifacts and commit evidence."""

from __future__ import annotations

import ast
import re
import shutil
import subprocess
from typing import TYPE_CHECKING

from acheron.ux_review.schema import CURRENT_HEAD, Story

if TYPE_CHECKING:
    from pathlib import Path


def _repository_head(repo_root: Path) -> str | None:
    """Return the repository HEAD when ``repo_root`` is inside a Git checkout."""
    git = shutil.which("git")
    if git is None:
        return None
    try:
        result = subprocess.run(  # noqa: S603 - executable and arguments are fixed
            [git, "-C", str(repo_root), "rev-parse", "HEAD"],
            check=True,
            capture_output=True,
            text=True,
        )
    except OSError, subprocess.CalledProcessError:
        return None
    return result.stdout.strip() or None


def _post_fixed_commit(repo_root: Path, story: Story, head_sha: str) -> bool:
    """Check that the requested head descends from the story's first fix commit."""
    actual_head = _repository_head(repo_root)
    if actual_head is None or not story.fixed_in:
        return False
    requested_head = actual_head if head_sha in {"HEAD", CURRENT_HEAD} else head_sha
    fixed_sha = story.fixed_in[0]
    if fixed_sha == CURRENT_HEAD:
        fixed_sha = actual_head
    git = shutil.which("git")
    if git is None:
        return False
    try:
        subprocess.run(  # noqa: S603 - executable and arguments are fixed
            [git, "-C", str(repo_root), "merge-base", "--is-ancestor", fixed_sha, requested_head],
            check=True,
            capture_output=True,
            text=True,
        )
    except OSError, subprocess.CalledProcessError:
        return False
    return True


def _normalized(text: str) -> str:
    return " ".join(text.split())


def _artifact_matches(path: Path, story: Story) -> bool:
    try:
        module = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    except OSError, SyntaxError, UnicodeError:
        return False
    docstring = ast.get_docstring(module, clean=False)
    if not docstring:
        return False
    marker = re.compile(rf"^\s*STORY_REF:\s*{re.escape(story.id)}\s*$", re.MULTILINE)
    if marker.search(docstring) is None:
        return False
    return _normalized(story.user_journey) in _normalized(docstring)


def artifact_path_for(story: Story, root: Path, head_sha: str) -> Path | None:
    """Return a valid harness artifact for the story's strongest channel."""
    if not story.discovered_via:
        return None
    strongest = story.discovered_via[0]
    repo_root = root.parent.parent
    if not _post_fixed_commit(repo_root, story, head_sha):
        return None
    if strongest == "simulation":
        candidates = (repo_root / "sim" / "scenarios").glob("*.py")
    elif strongest == "first-run":
        candidates = (repo_root / "tests" / "first_run").glob("test_*.py")
    else:
        return None
    return next((path for path in candidates if _artifact_matches(path, story)), None)
