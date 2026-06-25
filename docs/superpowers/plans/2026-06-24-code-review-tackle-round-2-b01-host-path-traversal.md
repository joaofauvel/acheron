---
bundle: B1
name: Host path traversal
severity: HIGH
stories: 1
m_effort: 1
main_plan: 2026-06-24-code-review-tackle-round-2.md
---

# B1 — Host path traversal (SEC-007)

> **For agentic workers:** Use the **Common Workflow** from the main plan. Each task below is a single story with full TDD detail (M-effort).

**Bundle summary:** Fix the host path traversal vulnerability in `ExtractionHandler`. The current code accepts a user-supplied path and passes it directly to `epub.read_epub` without bounds checking — an attacker who can submit jobs can read any file the orchestrator user can see (e.g. `/etc/passwd`, secrets, configs).

**Expected commits:** 1 (single story, single commit).

---

## Task 1: SEC-007 — Host path traversal & arbitrary local file read in ExtractionHandler

**Story:** `docs/code_review/operations.md` § SEC-007 (HIGH, M effort).

**Files:**
- Modify: `src/acheron/shell/local_handlers.py` (the `ExtractionHandler.extract_epub` method)
- Modify: `src/acheron/core/errors.py` (add `PathNotAllowedError`)
- Test: `tests/shell/test_local_handlers.py` (create if absent; add new test class `TestExtractionHandlerPathSecurity`)

### Step 1: Write the failing tests

Add a new test class to `tests/shell/test_local_handlers.py`:

```python
import pytest
from acheron.shell.local_handlers import ExtractionHandler
from acheron.core.errors import PathNotAllowedError

class TestExtractionHandlerPathSecurity:
    """SEC-007: host path traversal protection."""

    def _handler(self, allowlist_root):
        # Construct handler with a tight allowlist
        return ExtractionHandler(allowlist_root=allowlist_root)

    def test_resolved_path_inside_allowlist_passes(self, tmp_path):
        # Create a fake epub inside tmp_path; the handler should accept it
        # (real epub.read_epub is mocked because we don't have a real file).
        ...
```

The tests below are the full set. Skip the `# ...` placeholders — implement them concretely.

```python
import pytest
from unittest.mock import patch, MagicMock
from pathlib import Path
from acheron.shell.local_handlers import ExtractionHandler
from acheron.core.errors import PathNotAllowedError


class TestExtractionHandlerPathSecurity:
    """SEC-007: host path traversal protection."""

    def test_resolved_path_inside_allowlist_passes(self, tmp_path):
        good = tmp_path / "book.epub"
        good.write_bytes(b"PK\x03\x04fake-epub")
        handler = ExtractionHandler(allowlist_root=tmp_path)
        with patch("acheron.shell.local_handlers.epub.read_epub") as mock_read:
            mock_read.return_value = MagicMock()
            handler.extract_epub(good)
            mock_read.assert_called_once()

    def test_path_traversal_raises(self, tmp_path):
        bad = tmp_path / ".." / "etc" / "passwd"
        handler = ExtractionHandler(allowlist_root=tmp_path)
        with pytest.raises(PathNotAllowedError, match="escapes allowlist"):
            handler.extract_epub(bad)

    def test_absolute_path_outside_allowlist_raises(self, tmp_path):
        handler = ExtractionHandler(allowlist_root=tmp_path)
        with pytest.raises(PathNotAllowedError, match="not under allowlist"):
            handler.extract_epub(Path("/etc/passwd"))

    def test_symlink_pointing_outside_allowlist_raises(self, tmp_path):
        outside = tmp_path.parent / "outside_file"
        outside.write_text("secret")
        symlink = tmp_path / "book.epub"
        symlink.symlink_to(outside)
        handler = ExtractionHandler(allowlist_root=tmp_path)
        with pytest.raises(PathNotAllowedError, match="escapes allowlist"):
            handler.extract_epub(symlink)
```

The exact `ExtractionHandler` constructor signature may differ; inspect the current code first and adjust the test setup. The 4 tests above are the contract: paths inside the allowlist pass, paths outside (traversal, absolute, symlink) raise `PathNotAllowedError`.

### Step 2: Run tests to verify they fail

```bash
uv run pytest tests/shell/test_local_handlers.py::TestExtractionHandlerPathSecurity -xvs
```

Expected: all 4 tests FAIL (no `PathNotAllowedError` defined, no `allowlist_root` constructor arg, no bounds check).

### Step 3: Add `PathNotAllowedError` to `core/errors.py`

Edit `src/acheron/core/errors.py`. Find the existing exception hierarchy. Add a new typed error:

```python
class PathNotAllowedError(AcheronError):
    """A path submitted for filesystem access resolves outside the configured allowlist."""
```

### Step 4: Implement the bounds check in `ExtractionHandler`

In `src/acheron/shell/local_handlers.py`:

1. Add `allowlist_root: Path` to `ExtractionHandler.__init__` (or read from `Settings` if there's a settings field).
2. In `extract_epub(ebook_path: Path)`:
   - Resolve: `resolved = ebook_path.resolve()`.
   - Check: `resolved.is_relative_to(self._allowlist_root)`. If not, raise `PathNotAllowedError(f"path {resolved} is not under allowlist {self._allowlist_root}")`.
   - Then proceed with the existing `epub.read_epub(resolved)` call.

If `ExtractionHandler` currently takes no constructor args, the cleanest fix is to add `allowlist_root: Path` and update the call site in `Orchestrator` to pass `settings.orchestrator.data_dir` (or a new `Settings.orchestrator.extraction_allowlist` field — add it if needed).

### Step 5: Run tests to verify they pass

```bash
uv run pytest tests/shell/test_local_handlers.py::TestExtractionHandlerPathSecurity -xvs
```

Expected: 4 tests PASS.

### Step 6: Run the full verify gate

```bash
just validate
```

Expected: all checks pass.

### Step 7 & 8: Subagent passes

Dispatch the two subagent passes per the main plan §"Per-story cycle" §6 and §7. Inline SEC-007's story text + your diff.

For doc-staleness: touched files are `src/acheron/shell/local_handlers.py` and `src/acheron/core/errors.py`. Other stories citing these files (if any) need `last_verified_at` updated.

### Step 9: Atomic commit

```bash
git add src/ tests/ docs/code_review/
git commit -m "fix(SEC-007): enforce allowlist on ExtractionHandler input path

extracted_path = Path(ebook_path).resolve() and require it to live
under the configured allowlist_root (Settings.orchestrator.data_dir
by default). Traversal, absolute, and symlink-pointing-outside inputs
raise a new typed PathNotAllowedError.

4 tests cover: inside-allowlist pass, ../-traversal, /etc/passwd
absolute, symlink to outside file."
```

### Step 10: Bump story status

In `docs/code_review/operations.md`:
- `status: fixed` → `verified`
- `last_verified_at: {commit: <sha>, date: 2026-06-24}`
- `fixed_in: [<sha>]`
- Update `lines:` to the new post-fix locations.

---

## Bundle summary

- **Stories tackled:** 1 (SEC-007).
- **Commits expected:** 1.
- **M-effort iteration risk:** the constructor signature for `ExtractionHandler` may differ from the test sketch — adjust if so. If the `allowlist_root` source isn't `Settings.orchestrator.data_dir`, add a new `Settings.orchestrator.extraction_allowlist: Path` field and pass it.
- **Surface to user if:** the design needs >1 commit (e.g. multiple call sites of `ExtractionHandler`), or if `just validate` fails on a non-flaky test.
