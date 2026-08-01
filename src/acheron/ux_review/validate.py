"""Validate the docs/ux_review/ rubric against the schema and HEAD."""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

import yaml
from pydantic import ValidationError

from acheron.ux_review.discovery import _repository_head, artifact_path_for
from acheron.ux_review.schema import CURRENT_HEAD, Story

_STORY_PATTERN = re.compile(r"^## ([A-Z]+-\d+)\b", re.MULTILINE)
_YAML_PATTERN = re.compile(r"```yaml\n(.*?)```", re.DOTALL)
_THEME_FILES = ("deploy.md", "ops.md", "maint.md")


def _parse_story_blocks(md_text: str) -> list[tuple[str, dict[str, object] | str]]:
    """Parse a theme file into (story_id, frontmatter) tuples.

    Returns the frontmatter as a dict on success, or a string error message
    on YAML parse failure. Uses `safe_load_all` to accept the standard
    `---` ... `---` frontmatter format (which `safe_load` rejects as a
    multi-document stream).
    """
    blocks: list[tuple[str, dict[str, object] | str]] = []
    for match in _STORY_PATTERN.finditer(md_text):
        story_id = match.group(1)
        start = match.end()
        next_match = _YAML_PATTERN.search(md_text, start)
        if not next_match:
            continue
        yaml_text = next_match.group(1)
        try:
            documents = list(yaml.safe_load_all(yaml_text))
        except yaml.YAMLError as exc:
            blocks.append((story_id, f"YAML parse error: {exc}"))
            continue
        if not documents:
            blocks.append((story_id, "YAML produced no documents"))
            continue
        data = documents[0]
        if not isinstance(data, dict):
            blocks.append((story_id, "YAML did not produce a mapping"))
            continue
        blocks.append((story_id, data))
    return blocks


def _parse_line_range(spec: str) -> tuple[int, int | None]:
    """Parse 'N-M' or 'N' into (start, end)."""
    if "-" in spec:
        start_str, end_str = spec.split("-", 1)
        return int(start_str), int(end_str)
    return int(spec), None


def _check_file_ref(file_path: Path, lines: str) -> list[str]:
    """Validate a single file:lines reference. Return a list of error messages."""
    if not file_path.exists():
        return [f"file not found: {file_path}"]
    try:
        start, end = _parse_line_range(lines)
    except ValueError:
        return [f"invalid line range: {lines!r}"]
    if end is not None and start > end:
        return [f"line range start > end: {lines!r}"]
    if end is None:
        end = start
    total_lines = sum(1 for _ in file_path.open(encoding="utf-8"))
    if end > total_lines:
        return [f"line range exceeds file length: {lines!r} > {total_lines}"]
    return []


def _validate_story(story: Story, tag: str, root: Path, head_sha: str) -> list[str]:
    """Validate a single parsed story. Return a list of error messages."""
    errors: list[str] = []
    actual_head = _repository_head(root.parent.parent)
    marker_input = head_sha in {"HEAD", CURRENT_HEAD}
    requested_head = actual_head if marker_input and actual_head is not None else head_sha
    current_markers = [
        value
        for value in (*story.fixed_in, *story.verified_in, story.last_verified_at.get("commit", ""))
        if value == CURRENT_HEAD
    ]
    if current_markers and (actual_head is None or requested_head != actual_head):
        errors.append(f"{tag}: CURRENT_HEAD metadata does not match repository HEAD")
    for file_ref in story.files:
        file_path = Path(file_ref.path)
        errors.extend(f"{tag}: {err}" for err in _check_file_ref(file_path, file_ref.lines))
    if "on-call" in story.discovered_via and not story.incident_ref:
        errors.append(f"{tag}: discovered_via includes 'on-call' but incident_ref is missing")
    if "user-feedback" in story.discovered_via and not story.feedback_ref:
        errors.append(f"{tag}: discovered_via includes 'user-feedback' but feedback_ref is missing")
    if story.status == "wontfix" and not story.wontfix_reason:
        errors.append(f"{tag}: status is 'wontfix' but wontfix_reason is missing")
    if story.discovered_via and story.discovered_via[0] in {"simulation", "first-run"}:
        artifact = artifact_path_for(story, root, head_sha)
        if artifact is None:
            errors.append(
                f"{tag}: discovered_via={story.discovered_via[0]} but no harness artifact"
                f" references this story (STORY_REF missing)"
            )
    return errors


def validate(root: Path, head_sha: str) -> list[str]:
    """Walk the theme files and return a list of validation errors.

    The function returns the full error list but does not raise; callers
    decide whether to exit non-zero.
    """
    errors: list[str] = []
    for story_file in _THEME_FILES:
        path = root / story_file
        if not path.exists():
            errors.append(f"{path}: missing theme file")
            continue
        text = path.read_text(encoding="utf-8")
        for story_id, data in _parse_story_blocks(text):
            tag = f"{path}:{story_id}"
            if isinstance(data, str):
                errors.append(f"{tag}: {data}")
                continue
            try:
                story = Story.model_validate(data)
            except ValidationError as exc:
                errors.append(f"{tag}: schema error: {exc}")
                continue
            errors.extend(_validate_story(story, tag, root, head_sha))
    return errors


def main() -> int:
    """CLI entry point: validate the rubric and print errors."""
    parser = argparse.ArgumentParser(description="Validate the docs/ux_review/ rubric")
    parser.add_argument("--root", type=Path, default=Path("docs/ux_review"))
    parser.add_argument("--head", default="HEAD", help="Git commit SHA to validate against")
    parser.add_argument(
        "--strict",
        action="store_true",
        help="Exit non-zero on any validation error (CI mode)",
    )
    args = parser.parse_args()
    errors = validate(args.root, args.head)
    if errors:
        print(f"ux-validate: {len(errors)} error(s):", file=sys.stderr)  # noqa: T201
        for err in errors:
            print(f"  - {err}", file=sys.stderr)  # noqa: T201
        return 1 if args.strict else 0
    print("ux-validate: OK")  # noqa: T201
    return 0


if __name__ == "__main__":
    sys.exit(main())
