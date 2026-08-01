from __future__ import annotations

from pathlib import Path

import pytest

import acheron.ux_review.validate as validate_module
from acheron.ux_review.validate import validate


def _write_story(root: Path) -> Path:
    docs = root / "docs" / "ux_review"
    docs.mkdir(parents=True)
    (docs / "ops.md").write_text(
        """# OPS

## OPS-999 — Test story

```yaml
---
id: OPS-999
title: Test story
status: verified
severity: medium
effort: S
discovered_via: [code-review]
user_facing_surface: cli
silent: false
journey_stage: t1
user_journey: Test journey
files:
  - path: src/test.py
    lines: "1"
fixed_in: [abc123]
verified_in: [CURRENT_HEAD]
last_verified_at:
  commit: CURRENT_HEAD
  date: '2026-07-31'
verified_by: focused-journey
---
```
""",
        encoding="utf-8",
    )
    (root / "src").mkdir()
    (root / "src" / "test.py").write_text("# test\n", encoding="utf-8")
    return docs


def test_current_head_metadata_rejects_stale_validate_head(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    docs = _write_story(tmp_path)
    monkeypatch.setattr(validate_module, "_repository_head", lambda _root: "actual-head")

    errors = validate(docs, "stale-head")

    assert any("CURRENT_HEAD metadata does not match repository HEAD" in error for error in errors)
