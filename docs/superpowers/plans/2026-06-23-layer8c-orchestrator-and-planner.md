# Layer 8c Sub-plan 1 — Orchestrator + Planner + Qwen3TTS Patch

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add the cross-cutting `max_input_tokens` capability field and the `validate_chunking_fits_workers` planner function; refactor `HttpWorker.execute()` from `if job.job_type == WorkerType.ASR:` to a `match` with arms for `ASR | TRANSLATION | TTS`; retrofit the Qwen3-TTS handler to read chunks from the `Input` parameter (8b's `BytesInput`) and update its tests.

**Architecture:** Three layers. (1) Domain: `WorkerCapabilities.max_input_tokens` (default `None` = unbounded); new `ChunkingTooLongForWorkerError` (subclass of `InvalidLanguagePathError`); pure function `validate_chunking_fits_workers(capabilities, chunking_max_length, chars_per_token=4)` in `acheron.core.planner` that the orchestrator calls separately from `compile_plan` so each function has one responsibility. (2) Orchestrator wiring: `Settings.chars_per_token` (default 4); `submit_job` calls both `compile_plan` and `validate_chunking_fits_workers` (caller-side composition, not planner-side). (3) Transport: `HttpWorker.execute()` becomes `match job.job_type` with three arms — `ASR` reads the upstream extract step's audio, `TRANSLATION | TTS` reads the upstream chunking step's `chunks.json` — sharing a new `_execute_with_upstream_input` helper. ASR regression: existing 8b tests still pass (same wire shape, refactored). Qwen3-TTS: `handle()` requires `Input` (no `payload["chunks"]` fallback); existing test fixtures update to construct a `BytesInput` with `chunks.json` bytes.

**Tech Stack:** Python 3.14, pydantic v2, the existing `StepCache` from `acheron.shell.cache`, pytest + pytest-asyncio, mypy + basedpyright, ruff, import-linter.

**Reference spec:** `docs/superpowers/specs/2026-06-23-layer8c-translategemma-worker-design.md` (this sub-plan covers the "WorkerCapabilities.max_input_tokens", "ChunkingTooLongForWorkerError", "validate_chunking_fits_workers", "Orchestrator submit-path wiring", "HttpWorker.execute() — match-based dispatch", and "The Qwen3TTS Patch" sections).

**Final gate:** `just validate` green (lint-strict, lint-imports, mypy, basedpyright, pytest — all clean, coverage ≥ 80%).

**Depends on:** Sub-plan 2 (TranslateGemma worker) — not strictly required at code-level (no new types are introduced here that the worker needs), but the cross-cutting fix must be in `main` before the worker is registered in the registry, otherwise registration would fail or `max_input_tokens` would silently be `None`.

---

## Spec Adjustments (refinements from writing the plan)

These deltas are not new design decisions; they are clarifications of decisions already made in the parent spec, surfaced by writing the implementation steps. The parent spec remains the single source of truth for the design.

1. **`_execute_with_upstream_input` accepts a `content_type_predicate: Callable[[str], bool]` keyword**. The new helper is parameterised on the upstream step name and a predicate; ASR uses `upstream_step="extract"` and a `lambda c: c.startswith("audio/")` predicate; TRANSLATION and TTS use `upstream_step="chunk"` and a `lambda c: c == "application/json"` predicate. This keeps the wire-shaping code (StepCache lookup, file read, multipart construction, response parsing) in one place.

2. **The match refactor: `case _:` fallthrough preserves the existing JSON / multipart-mixed response path verbatim**. No behaviour change for `EXTRACTION`, `CHUNKING`, `PACKAGING` worker types.

3. **`Qwen3TTSRunpodHandler.handle()` rejects `input is None` with `WorkerError("Qwen3-TTS requires a chunks.json input (multipart part)")`**. No fallback to `job.payload["chunks"]` — AGENTS.md forbids legacy fallbacks. The previous test-only `payload["chunks"]` path is removed; the new tests construct a `BytesInput` with `chunks.json` bytes.

4. **`Settings.chars_per_token` lives at the top level of `Settings`** (not under `workers` or `orchestrator`). It's a single integer that the planner's `validate_chunking_fits_workers` reads. The orchestrator's `submit_job` reads `self._settings.chars_per_token`.

5. **`compile_plan`'s signature is unchanged**. The chunking length check is the caller's responsibility. The new `validate_chunking_fits_workers` is a sibling function in the same module, not a parameter on `compile_plan`.

---

## Adversarial Review Rubric

After this sub-plan is implemented, dispatch a fresh-context reviewer subagent with this rubric:

### Correctness

- [ ] `WorkerCapabilities(max_input_tokens=None)` constructs and round-trips; the field is `int | None` with default `None`.
- [ ] `WorkerCapabilities(max_input_tokens=2048)` constructs; the field is included in `model_dump_json()` output.
- [ ] `validate_chunking_fits_workers` passes when `chunking_max_length=250, chars_per_token=4, worker.max_input_tokens=2048` (250/4=62 ≤ 2048).
- [ ] `validate_chunking_fits_workers` raises `ChunkingTooLongForWorkerError` when `chunking_max_length=2000, chars_per_token=4, worker.max_input_tokens=2048` (2000/4=500 ≤ 2048, no error) — wait, 500 ≤ 2048, so it should pass. Test boundary: `chunking_max_length=9000, chars_per_token=4` (9000/4=2250 > 2048, error).
- [ ] Error message includes the failing values: `chunking_max_length`, `max_input_tokens`, `chars_per_token`, step type.
- [ ] Workers with `max_input_tokens=None` are ignored (no length check).
- [ ] ASR workers are skipped (only `WorkerType.TRANSLATION` and `WorkerType.TTS` are checked).
- [ ] Smaller `chars_per_token` triggers errors earlier.
- [ ] `HttpWorker.execute` for `WorkerType.ASR` reads upstream `extract` outputs from `StepCache`, finds the first `audio/*` content_type, POSTs multipart (one `application/json` part for envelope, one binary part for the audio). Response is parsed the same way (multipart/mixed → OutputFiles, application/json → JobResult).
- [ ] `HttpWorker.execute` for `WorkerType.TRANSLATION` reads upstream `chunk` outputs, finds the `application/json` content_type, POSTs multipart with `chunks.json` bytes.
- [ ] `HttpWorker.execute` for `WorkerType.TTS` does the same as TRANSLATION.
- [ ] `HttpWorker.execute` for `WorkerType.EXTRACTION` / `CHUNKING` / `PACKAGING` uses the existing JSON path (no behaviour change).
- [ ] The Qwen3-TTS handler requires `Input is not None`; raises `WorkerError` if absent.
- [ ] The Qwen3-TTS handler parses `chunks.json` from `Input`; rejects malformed JSON / non-list with `WorkerError`.
- [ ] All existing 8a / 8b tests still pass (no regressions in planner, HttpWorker, Qwen3-TTS).

### Code quality

- [ ] The match refactor uses `match` statement, not `if` chain (no `if job.job_type == WorkerType.ASR` left).
- [ ] `validate_chunking_fits_workers` is pure (no I/O, no global state, no env-var reads).
- [ ] `_execute_with_upstream_input` is the single source of truth for "load upstream step + POST multipart + parse response"; no duplication.
- [ ] No `Any` abuse in the new code; the helper is fully typed.
- [ ] No legacy fallback in Qwen3-TTS handler (no `if input is None: fall back to payload["chunks"]`).
- [ ] AGENTS.md compliance: no `legacy` comments, no `compat` shims.

### Spec compliance

- [ ] `WorkerCapabilities.max_input_tokens` is the new field; default `None`; ASR / packaging omit it; TRANSLATION + TTS set it.
- [ ] `ChunkingTooLongForWorkerError(InvalidLanguagePathError)` — subclass of existing error so existing handling still works.
- [ ] `validate_chunking_fits_workers` signature: `(capabilities: tuple[WorkerCapabilities, ...], chunking_max_length: int, chars_per_token: int = 4) -> None`.
- [ ] `compile_plan` signature is unchanged.
- [ ] `Settings.chars_per_token: int = 4` at the top level of `Settings`.
- [ ] `Orchestrator.submit_job` calls both `compile_plan` and `validate_chunking_fits_workers` in that order; failures bubble as `AcheronError` (the existing `submit_job` contract).
- [ ] `HttpWorker.execute` arms: `WorkerType.ASR | TRANSLATION | TTS` go through `_execute_with_upstream_input`; everything else uses the existing JSON / multipart-mixed path.
- [ ] The Qwen3-TTS patch removes the `payload["chunks"]` read; `handle()` reads from `Input` only.

---

## File Structure

| File | Responsibility |
|------|---------------|
| `src/acheron/core/models.py` (EXTENDED) | `WorkerCapabilities.max_input_tokens: int \| None = None`. |
| `src/acheron/core/errors.py` (EXTENDED) | New `ChunkingTooLongForWorkerError(InvalidLanguagePathError)`. |
| `src/acheron/core/planner.py` (EXTENDED) | New `validate_chunking_fits_workers(capabilities, chunking_max_length, chars_per_token=4)`. |
| `src/acheron/shell/config.py` (EXTENDED) | `Settings.chars_per_token: int = 4`. |
| `src/acheron/shell/orchestrator.py` (EXTENDED) | `submit_job` calls both `compile_plan` and `validate_chunking_fits_workers`. |
| `src/acheron/shell/transports/http.py` (EXTENDED) | `execute()` becomes `match`; new `_execute_with_upstream_input` helper. |
| `src/acheron/shell/transports/_multipart.py` (EXTENDED) | No new functions; existing `_parse_multipart` / `_materialize_artifact` / `_build_result` reused. |
| `tests/core/test_models.py` (EXTENDED) | `max_input_tokens` field shape. |
| `tests/core/test_planner.py` (EXTENDED) | `validate_chunking_fits_workers` cases. |
| `tests/shell/test_orchestrator.py` (EXTENDED) | `submit_job` calls both. |
| `tests/shell/transports/test_http_worker.py` (EXTENDED) | TRANSLATION + TTS + ASR arms; legacy fallthrough. |
| `workers/qwen3tts/handler.py` (EXTENDED) | `handle()` reads from `Input`; `capabilities()` adds `max_input_tokens=2048`. |
| `workers/qwen3tts/tests/test_handler.py` (EXTENDED) | Fixtures build a `BytesInput`; new tests for None / malformed input. |
| `workers/qwen3tts/tests/test_capabilities.py` (EXTENDED) | Assert `max_input_tokens == 2048`. |

---

### Task 1: Extend `WorkerCapabilities` with `max_input_tokens`

**Files:**
- Modify: `src/acheron/core/models.py:77-89`

- [ ] **Step 1: Add the field to the dataclass**

Modify `src/acheron/core/models.py`. Add the new field after `model_source`:

```python
    model_source: str | None
    max_input_tokens: int | None = None  # per-chunk input token limit; None = unbounded
    metadata: dict[str, JsonValue] = field(default_factory=dict)
```

The field is positioned between `model_source` and `metadata` so the existing `metadata: dict[str, JsonValue] = field(default_factory=dict)` line stays the last field. Frozen dataclass with `default=None` works because all fields after a defaulted field must also be defaulted (or have a `field(default=...)`).

- [ ] **Step 2: Run existing test_models.py to confirm no regression**

```bash
uv run pytest tests/core/test_models.py -v
```

Expected: PASS. The new field defaults to `None`; existing constructions that don't pass it are unchanged.

- [ ] **Step 3: Add a focused test for the new field**

Append to `tests/core/test_models.py`:

```python
class TestWorkerCapabilitiesMaxInputTokens:
    def test_default_is_none(self) -> None:
        caps = WorkerCapabilities(
            worker_type=WorkerType.TRANSLATION,
            supported_languages_in=frozenset({"en"}),
            supported_languages_out=frozenset({"es"}),
            supported_formats_in=frozenset({"text"}),
            supported_formats_out=frozenset({"text"}),
            max_payload_bytes=None,
            batch_capable=False,
            model_source=None,
        )
        assert caps.max_input_tokens is None

    def test_explicit_int(self) -> None:
        caps = WorkerCapabilities(
            worker_type=WorkerType.TRANSLATION,
            supported_languages_in=frozenset({"en"}),
            supported_languages_out=frozenset({"es"}),
            supported_formats_in=frozenset({"text"}),
            supported_formats_out=frozenset({"text"}),
            max_payload_bytes=None,
            batch_capable=False,
            model_source=None,
            max_input_tokens=2048,
        )
        assert caps.max_input_tokens == 2048
```

- [ ] **Step 4: Run the new tests**

```bash
uv run pytest tests/core/test_models.py::TestWorkerCapabilitiesMaxInputTokens -v
```

Expected: PASS (2 tests).

- [ ] **Step 5: Lint + type-check**

```bash
uv run ruff check src/acheron/core/models.py tests/core/test_models.py
uv run mypy src/acheron/core/models.py
```

Expected: clean.

- [ ] **Step 6: Commit**

```bash
git add src/acheron/core/models.py tests/core/test_models.py
git commit -m "feat(core): WorkerCapabilities.max_input_tokens field"
```

---

### Task 2: Add `ChunkingTooLongForWorkerError`

**Files:**
- Modify: `src/acheron/core/errors.py`

- [ ] **Step 1: Add the new exception class**

Modify `src/acheron/core/errors.py`. Append after the existing `InvalidLanguagePathError` class:

```python
class ChunkingTooLongForWorkerError(InvalidLanguagePathError):
    """Chunking step's max_chunk_length exceeds a text-input worker's max_input_tokens.

    Raised at plan compile time so misconfigurations fail fast, before any GPU time.
    Subclass of ``InvalidLanguagePathError`` so existing handling (job rejection,
    dashboard) still works.
    """
```

- [ ] **Step 2: Verify the import / class shape**

```bash
uv run python -c "from acheron.core.errors import ChunkingTooLongForWorkerError, InvalidLanguagePathError; assert issubclass(ChunkingTooLongForWorkerError, InvalidLanguagePathError); print('ok')"
```

Expected: `ok`.

- [ ] **Step 3: Lint + type-check**

```bash
uv run ruff check src/acheron/core/errors.py
uv run mypy src/acheron/core/errors.py
```

Expected: clean.

- [ ] **Step 4: Commit**

```bash
git add src/acheron/core/errors.py
git commit -m "feat(core): ChunkingTooLongForWorkerError for plan-time misconfig"
```

---

### Task 3: Add `validate_chunking_fits_workers` to planner

**Files:**
- Modify: `src/acheron/core/planner.py`
- Modify: `tests/core/test_planner.py`

- [ ] **Step 1: Write the failing tests**

Append to `tests/core/test_planner.py`:

```python
from acheron.core.errors import ChunkingTooLongForWorkerError
from acheron.core.planner import validate_chunking_fits_workers


def _text_input_tts_caps(max_input_tokens: int | None = 2048) -> WorkerCapabilities:
    return WorkerCapabilities(
        worker_type=WorkerType.TTS,
        supported_languages_in=frozenset({"en"}),
        supported_languages_out=frozenset({"en"}),
        supported_formats_in=frozenset({"text"}),
        supported_formats_out=frozenset({"wav"}),
        max_payload_bytes=None,
        batch_capable=True,
        model_source=None,
        max_input_tokens=max_input_tokens,
    )


def _text_input_translation_caps(max_input_tokens: int | None = 2048) -> WorkerCapabilities:
    return WorkerCapabilities(
        worker_type=WorkerType.TRANSLATION,
        supported_languages_in=frozenset({"en"}),
        supported_languages_out=frozenset({"es"}),
        supported_formats_in=frozenset({"text"}),
        supported_formats_out=frozenset({"text"}),
        max_payload_bytes=None,
        batch_capable=False,
        model_source=None,
        max_input_tokens=max_input_tokens,
    )


class TestValidateChunkingFitsWorkers:
    def test_passes_when_chunking_within_limit(self) -> None:
        caps = (_text_input_tts_caps(max_input_tokens=2048),)
        # 250 chars / 4 chars-per-token = 62 tokens, well under 2048.
        validate_chunking_fits_workers(caps, chunking_max_length=250)

    def test_raises_when_chunking_exceeds_tts_limit(self) -> None:
        caps = (_text_input_tts_caps(max_input_tokens=2048),)
        # 9000 chars / 4 = 2250 tokens, exceeds 2048.
        with pytest.raises(ChunkingTooLongForWorkerError, match="max_input_tokens=2048"):
            validate_chunking_fits_workers(caps, chunking_max_length=9000)

    def test_raises_when_chunking_exceeds_translation_limit(self) -> None:
        caps = (_text_input_translation_caps(max_input_tokens=2048),)
        with pytest.raises(ChunkingTooLongForWorkerError, match="tts" if False else "translation"):
            validate_chunking_fits_workers(caps, chunking_max_length=9000)

    def test_ignores_workers_with_unlimited_tokens(self) -> None:
        caps = (_text_input_tts_caps(max_input_tokens=None),)
        # Even with a huge chunk length, an unbounded worker is fine.
        validate_chunking_fits_workers(caps, chunking_max_length=10_000_000)

    def test_ignores_non_text_input_worker_types(self) -> None:
        # ASR caps don't carry max_input_tokens (and the function should skip ASR entirely).
        caps = (
            WorkerCapabilities(
                worker_type=WorkerType.ASR,
                supported_languages_in=frozenset({"en"}),
                supported_languages_out=frozenset({"en"}),
                supported_formats_in=frozenset({"wav"}),
                supported_formats_out=frozenset({"text"}),
                max_payload_bytes=None,
                batch_capable=False,
                model_source=None,
                max_input_tokens=10,  # tiny, but should be ignored
            ),
        )
        # ASR is not in the text-input list; should not raise.
        validate_chunking_fits_workers(caps, chunking_max_length=10_000_000)

    def test_smaller_chars_per_token_triggers_earlier(self) -> None:
        caps = (_text_input_tts_caps(max_input_tokens=2048),)
        # 1000 chars / 2 chars-per-token = 500 tokens, OK.
        validate_chunking_fits_workers(caps, chunking_max_length=1000, chars_per_token=2)
        # 1000 chars / 1 char-per-token = 1000 tokens, OK.
        validate_chunking_fits_workers(caps, chunking_max_length=1000, chars_per_token=1)
        # 1000 chars / 0 (clamped to 1) — 1000 tokens, OK; use a clearly-bigger input.
        with pytest.raises(ChunkingTooLongForWorkerError):
            validate_chunking_fits_workers(caps, chunking_max_length=10_000, chars_per_token=1)

    def test_error_message_includes_all_values(self) -> None:
        caps = (_text_input_tts_caps(max_input_tokens=512),)
        with pytest.raises(ChunkingTooLongForWorkerError) as excinfo:
            validate_chunking_fits_workers(caps, chunking_max_length=3000, chars_per_token=4)
        msg = str(excinfo.value)
        assert "max_chunk_length=3000" in msg
        assert "max_input_tokens=512" in msg
        assert "chars_per_token=4" in msg
        assert "tts" in msg
```

- [ ] **Step 2: Run the new tests; verify they fail**

```bash
uv run pytest tests/core/test_planner.py::TestValidateChunkingFitsWorkers -v
```

Expected: FAIL with `ImportError` or `ModuleNotFoundError` (`validate_chunking_fits_workers` not exported).

- [ ] **Step 3: Implement `validate_chunking_fits_workers`**

Modify `src/acheron/core/planner.py`. Add the new function after `compile_plan` (and after `_has_worker`):

```python
def validate_chunking_fits_workers(
    capabilities: tuple[WorkerCapabilities, ...],
    chunking_max_length: int,
    chars_per_token: int = 4,
) -> None:
    """Verify the chunking step's max_chunk_length fits each text-input worker's limit.

    A text-input worker is one whose ``max_input_tokens`` is set on its capabilities
    (TRANSLATION, TTS in v1). If any such worker has a lower per-chunk token limit
    than the chunking step's max_chunk_length allows (estimated at ``chars_per_token``
    per token), raises ``ChunkingTooLongForWorkerError`` so the caller fails the job
    at plan compile time, before any GPU time is spent.

    Conservative ``chars_per_token`` default (4) overestimates tokens for CJK languages;
    this is acceptable as a hard ceiling. A future sub-project could swap in a
    tokenizer-based estimator.

    Pure: no I/O, no global state. The caller is responsible for passing fresh
    ``capabilities`` and the chunking step's config.
    """
    from acheron.core.errors import ChunkingTooLongForWorkerError  # noqa: PLC0415

    if chars_per_token <= 0:
        msg = f"chars_per_token must be > 0, got {chars_per_token}"
        raise ValueError(msg)
    text_input_types = (WorkerType.TRANSLATION, WorkerType.TTS)
    for step_type in text_input_types:
        for c in capabilities:
            if c.worker_type != step_type or c.max_input_tokens is None:
                continue
            estimated_tokens = chunking_max_length // chars_per_token
            if estimated_tokens > c.max_input_tokens:
                msg = (
                    f"Chunking max_chunk_length={chunking_max_length} chars "
                    f"exceeds {step_type.value} worker max_input_tokens="
                    f"{c.max_input_tokens} (estimated {estimated_tokens} tokens "
                    f"at chars_per_token={chars_per_token})"
                )
                raise ChunkingTooLongForWorkerError(msg)
            break
```

- [ ] **Step 4: Run the new tests; verify they pass**

```bash
uv run pytest tests/core/test_planner.py::TestValidateChunkingFitsWorkers -v
```

Expected: PASS (7 tests).

- [ ] **Step 5: Run the full test_planner.py to confirm no regression**

```bash
uv run pytest tests/core/test_planner.py -v
```

Expected: PASS.

- [ ] **Step 6: Lint + type-check**

```bash
uv run ruff check src/acheron/core/planner.py tests/core/test_planner.py
uv run mypy src/acheron/core/planner.py
```

Expected: clean.

- [ ] **Step 7: Commit**

```bash
git add src/acheron/core/planner.py tests/core/test_planner.py
git commit -m "feat(planner): validate_chunking_fits_workers for plan-time input-budget check"
```

---

### Task 4: Add `chars_per_token` to `Settings`

**Files:**
- Modify: `src/acheron/shell/config.py`
- Modify: `tests/shell/test_config.py` (if it exists; otherwise add a focused test file)

- [ ] **Step 1: Find the existing tests for Settings**

```bash
ls tests/shell/test_config.py 2>/dev/null || echo "no existing tests/shell/test_config.py"
```

If `tests/shell/test_config.py` does not exist, create it; otherwise add to it.

- [ ] **Step 2: Add the field to `Settings`**

Modify `src/acheron/shell/config.py`. In the `Settings` class, add a new field after `providers`:

```python
    providers: ProvidersSettings = Field(default_factory=ProvidersSettings)
    chars_per_token: int = Field(default=4)
```

- [ ] **Step 3: Write the failing test**

Create or append to `tests/shell/test_config.py`:

```python
"""Tests for Settings defaults."""


def test_chars_per_token_default() -> None:
    from acheron.shell.config import Settings

    s = Settings()
    assert s.chars_per_token == 4


def test_chars_per_token_env_override() -> None:
    import os

    from acheron.shell.config import Settings

    prev = os.environ.pop("ACHERON_CHARS_PER_TOKEN", None)
    try:
        os.environ["ACHERON_CHARS_PER_TOKEN"] = "3"
        s = Settings()
        assert s.chars_per_token == 3
    finally:
        if prev is None:
            os.environ.pop("ACHERON_CHARS_PER_TOKEN", None)
        else:
            os.environ["ACHERON_CHARS_PER_TOKEN"] = prev
```

- [ ] **Step 4: Run the new tests**

```bash
uv run pytest tests/shell/test_config.py -v
```

Expected: PASS (2 tests).

- [ ] **Step 5: Lint + type-check**

```bash
uv run ruff check src/acheron/shell/config.py tests/shell/test_config.py
uv run mypy src/acheron/shell/config.py
```

Expected: clean.

- [ ] **Step 6: Commit**

```bash
git add src/acheron/shell/config.py tests/shell/test_config.py
git commit -m "feat(config): Settings.chars_per_token default 4"
```

---

### Task 5: Wire orchestrator submit-job path to call both

**Files:**
- Modify: `src/acheron/shell/orchestrator.py:243-244`
- Modify: `tests/shell/test_orchestrator.py` (extend)

- [ ] **Step 1: Read the current `submit_job` shape**

The current code (lines 243-245):
```python
        capabilities = tuple(w.capabilities for w in await self._registry.list_all())
        plan = compile_plan(request, strategy, capabilities, job_id=job_id)
        self._cache.save_plan(plan)
```

- [ ] **Step 2: Add the chunking-length check after `compile_plan`**

Modify `src/acheron/shell/orchestrator.py`. Add the import at the top alongside `compile_plan`:

```python
from acheron.core.planner import compile_plan, validate_chunking_fits_workers
```

Modify `submit_job` to call both:

```python
        capabilities = tuple(w.capabilities for w in await self._registry.list_all())
        plan = compile_plan(request, strategy, capabilities, job_id=job_id)
        validate_chunking_fits_workers(
            capabilities,
            chunking_max_length=self._settings.workers.chunking.max_chunk_length,
            chars_per_token=self._settings.chars_per_token,
        )
        self._cache.save_plan(plan)
```

- [ ] **Step 3: Add a test that the orchestrator fails-fast on misconfig**

Find the existing test file (`ls tests/shell/test_orchestrator.py`); if it exists, add to it. Otherwise create a new test file that exercises `Orchestrator.submit_job` with a stub registry + stub job store.

The test pattern (pseudocode; adapt to existing fixtures):

```python
import pytest

from acheron.core.errors import ChunkingTooLongForWorkerError
from acheron.core.models import (
    EpubRequest,
    ExecutorStrategy,
    WorkerCapabilities,
    WorkerType,
)
from acheron.shell.config import Settings
from acheron.shell.orchestrator import Orchestrator


class _StubRegistry:
    def __init__(self, caps: tuple[WorkerCapabilities, ...]) -> None:
        self._caps = caps

    async def list_all(self):
        from acheron.shell.registry import RegisteredWorker

        return tuple(RegisteredWorker(worker_id=f"w{i}", endpoint="local", transport="local", capabilities=c) for i, c in enumerate(self._caps))

    async def find_by_type(self, worker_type: WorkerType):
        for c in self._caps:
            if c.worker_type == worker_type:
                return object()
        return None

    async def register(self, *args, **kwargs):  # noqa: ANN002, ANN003
        return None

    async def connect(self) -> None:
        return None

    async def close(self) -> None:
        return None


class _StubJobStore:
    async def put(self, *args, **kwargs):  # noqa: ANN002, ANN003
        return None

    async def get(self, *args, **kwargs):  # noqa: ANN002, ANN003
        return None

    async def list_all(self):
        return ()

    async def connect(self) -> None:
        return None

    async def close(self) -> None:
        return None


@pytest.mark.asyncio
async def test_submit_job_fails_when_chunking_exceeds_worker_limit(tmp_path) -> None:  # noqa: ANN001
    """A chunking step configured longer than a TTS worker's max_input_tokens fails fast at plan compile."""
    caps = (
        WorkerCapabilities(
            worker_type=WorkerType.TRANSLATION,
            supported_languages_in=frozenset({"en"}),
            supported_languages_out=frozenset({"es"}),
            supported_formats_in=frozenset({"text"}),
            supported_formats_out=frozenset({"text"}),
            max_payload_bytes=None,
            batch_capable=False,
            model_source=None,
            max_input_tokens=2048,
        ),
        WorkerCapabilities(
            worker_type=WorkerType.TTS,
            supported_languages_in=frozenset({"es"}),
            supported_languages_out=frozenset({"es"}),
            supported_formats_in=frozenset({"text"}),
            supported_formats_out=frozenset({"wav"}),
            max_payload_bytes=None,
            batch_capable=True,
            model_source=None,
            max_input_tokens=2048,
        ),
    )
    settings = Settings()
    settings.workers.chunking.max_chunk_length = 9000  # 9000/4=2250 tokens > 2048
    orch = Orchestrator(
        registry=_StubRegistry(caps),  # type: ignore[arg-type]
        cache=...,  # use a PlanCache stub; see existing tests
        job_store=_StubJobStore(),  # type: ignore[arg-type]
        settings=settings,
    )
    await orch.start()
    request = EpubRequest(source_path="/x.epub", source_language="en", target_language="es")
    with pytest.raises(ChunkingTooLongForWorkerError, match="max_chunk_length=9000"):
        await orch.submit_job(request, ExecutorStrategy.STREAMING)
```

> **Note on stub setup:** the `_StubRegistry` and `_StubJobStore` shapes mirror the abstract methods used by `Orchestrator.submit_job`; adapt field names to match the actual `PlanCache` / `WorkerStore` / `JobStore` constructor signatures in your codebase. Existing orchestrator tests (if any) provide a worked example.

- [ ] **Step 4: Run the new test**

```bash
uv run pytest tests/shell/test_orchestrator.py -v -k "submit_job"
```

Expected: PASS.

- [ ] **Step 5: Lint + type-check**

```bash
uv run ruff check src/acheron/shell/orchestrator.py tests/shell/test_orchestrator.py
uv run mypy src/acheron/shell/orchestrator.py
```

Expected: clean.

- [ ] **Step 6: Commit**

```bash
git add src/acheron/shell/orchestrator.py tests/shell/test_orchestrator.py
git commit -m "feat(orchestrator): submit-job calls validate_chunking_fits_workers after compile_plan"
```

---

### Task 6: Refactor `HttpWorker.execute` to `match` on `WorkerType`

**Files:**
- Modify: `src/acheron/shell/transports/http.py:90-141`
- Modify: `tests/shell/transports/test_http_worker.py` (extend)

- [ ] **Step 1: Read the current shape of `HttpWorker.execute` and `_execute_asr_multipart`**

```bash
uv run sed -n '85,145p' src/acheron/shell/transports/http.py
```

The current code has `execute` branching on `if job.job_type == WorkerType.ASR:` and a `_execute_asr_multipart` helper. We'll refactor both.

- [ ] **Step 2: Write the failing test for the TRANSLATION branch**

Find the existing test file (look in `tests/shell/transports/`); add a focused test that drives the new TRANSLATION branch with a stubbed upstream `chunks.json`.

Add to `tests/shell/transports/test_http_worker.py`:

```python
@pytest.mark.asyncio
async def test_execute_translation_loads_upstream_chunks_and_posts_multipart(
    tmp_path, monkeypatch
) -> None:
    """TRANSLATION step: HttpWorker reads upstream chunking step's chunks.json and POSTs multipart."""
    import json
    from acheron.core.models import Job, WorkerType
    from acheron.shell.cache import StepCache
    from acheron.shell.transports.http import HttpWorker
    from acheron.worker_sdk.schemas import ExecuteRequest  # type: ignore[import-not-found]

    # Materialise a chunks.json in the step cache (chunking step's output).
    cache = StepCache(tmp_path)
    plan_job_id = "job-abc123"
    chunks = [{"chapter_id": "ch1", "sequence_id": 0, "text": "Hello"}]
    chunks_bytes = json.dumps(chunks).encode("utf-8")
    chunks_path = tmp_path / plan_job_id / "chunk" / "chunks.json"
    chunks_path.parent.mkdir(parents=True, exist_ok=True)
    chunks_path.write_bytes(chunks_bytes)
    await cache.save_outputs(plan_job_id, "chunk", [
        OutputFile(path=str(chunks_path), filename="chunks.json",
                   size_bytes=len(chunks_bytes), checksum="x",
                   content_type="application/json"),
    ])

    # Capture the multipart body the worker POSTs.
    captured: dict = {}

    async def fake_post(self, url, files=None, **kwargs):  # noqa: ANN001, ARG002
        captured["url"] = url
        captured["files"] = files
        return _fake_json_response({"job_id": "x", "status": "success", "outputs": [], "metrics": {"duration_seconds": 0.1}})

    monkeypatch.setattr("httpx.AsyncClient.post", fake_post)

    w = HttpWorker("http://worker:8001", data_dir=tmp_path, step_cache=cache)
    job = Job(job_id=f"{plan_job_id}-translate", job_type=WorkerType.TRANSLATION,
              payload={"source_language": "en", "target_language": "es"}, chapter_id="ch1")
    await w.execute(job)
    assert "files" in captured
    assert "request" in captured["files"]
    assert "input" in captured["files"]
    # The chunks.json bytes are in the "input" part with application/json content-type.
    name, body, ctype = captured["files"]["input"]
    assert ctype == "application/json"
    assert json.loads(body) == chunks
```

- [ ] **Step 3: Run the new test; verify it fails**

```bash
uv run pytest tests/shell/transports/test_http_worker.py -v -k "translation_loads_upstream"
```

Expected: FAIL — the current `execute` only branches on ASR.

- [ ] **Step 4: Refactor `HttpWorker.execute` and add the helper**

Modify `src/acheron/shell/transports/http.py`. Add `from collections.abc import Callable` if not already present, then replace the `execute` and `_execute_asr_multipart` methods with the new match-based dispatch + the new helper:

```python
    async def execute(self, job: Job) -> JobResult:
        match job.job_type:
            case WorkerType.ASR:
                return await self._execute_with_upstream_input(
                    job,
                    upstream_step="extract",
                    content_type_predicate=lambda c: c.startswith("audio/"),
                )
            case WorkerType.TRANSLATION | WorkerType.TTS:
                return await self._execute_with_upstream_input(
                    job,
                    upstream_step="chunk",
                    content_type_predicate=lambda c: c == "application/json",
                )
            case _:
                # Existing JSON / multipart-mixed response path (unchanged).
                resp = await self._request("POST", "/execute", json=_job_to_dict(job))
                ctype = resp.headers.get("content-type", "")
                if ctype.startswith("multipart/mixed"):
                    return await self._parse_multipart(resp, job.job_id)
                return _result_adapter.validate_json(resp.content)
```

Replace `_execute_asr_multipart` (the entire method) with the new shared helper:

```python
    async def _execute_with_upstream_input(
        self,
        job: Job,
        *,
        upstream_step: str,
        content_type_predicate: Callable[[str], bool],
    ) -> JobResult:
        """Load the upstream step's outputs from StepCache and POST multipart to the worker.

        The orchestrator's ``StepCache`` holds the manifest of the previous step's
        outputs. We find the first ``OutputFile`` whose ``content_type`` matches
        ``content_type_predicate`` and POST ``multipart/form-data`` with one
        ``application/json`` part (the ``ExecuteRequest`` envelope) + one binary
        part (the upstream artifact). The worker's response is ``multipart/mixed``
        and is parsed the same way as the legacy JSON path.
        """
        plan_job_id = job.job_id.rsplit("-", 1)[0]
        try:
            upstream_outputs = await self._step_cache.load_outputs(plan_job_id, upstream_step)
        except CacheMissError as exc:
            msg = (
                f"{job.job_type.value} step {job.job_id}: no {upstream_step} step output "
                f"for {plan_job_id}"
            )
            raise WorkerError(msg) from exc
        artifact = next(
            (o for o in upstream_outputs if content_type_predicate(o.content_type)),
            None,
        )
        if artifact is None:
            msg = (
                f"{job.job_type.value} step {job.job_id}: no matching artifact in "
                f"{upstream_step} output (content_type predicate failed)"
            )
            raise WorkerError(msg)
        artifact_path = Path(artifact.path)
        if not await asyncio.to_thread(artifact_path.exists):
            msg = f"{job.job_type.value} step {job.job_id}: artifact file missing: {artifact_path}"
            raise WorkerError(msg)

        form = {
            "request": (None, json.dumps(_job_to_dict(job)).encode("utf-8"), "application/json"),
            "input": (
                artifact_path.name,
                await asyncio.to_thread(artifact_path.read_bytes),
                artifact.content_type,
            ),
        }
        resp = await self._post_multipart(form)
        ctype = resp.headers.get("content-type", "")
        if ctype.startswith("multipart/mixed"):
            return await self._parse_multipart(resp, job.job_id)
        return _result_adapter.validate_json(resp.content)
```

- [ ] **Step 5: Run the new test; verify it passes**

```bash
uv run pytest tests/shell/transports/test_http_worker.py -v -k "translation_loads_upstream"
```

Expected: PASS.

- [ ] **Step 6: Add a TTS-branch test and an ASR-regression test**

Add to `tests/shell/transports/test_http_worker.py`:

```python
@pytest.mark.asyncio
async def test_execute_tts_loads_upstream_chunks(tmp_path, monkeypatch) -> None:
    """TTS step: same multipart path as TRANSLATION."""
    # Reuse the TRANSLATION helper pattern; job_type = TTS, payload target_language only.
    # ... (analogous to the TRANSLATION test, with job_type=WorkerType.TTS) ...
```

```python
@pytest.mark.asyncio
async def test_execute_asr_still_uses_multipart(tmp_path, monkeypatch) -> None:
    """ASR regression: the refactor preserves the existing ASR multipart path."""
    # ... (analogous to the 8b ASR multipart test, refactored to the new helper) ...
```

- [ ] **Step 7: Run all HttpWorker tests; verify no regression**

```bash
uv run pytest tests/shell/transports/test_http_worker.py -v
```

Expected: PASS.

- [ ] **Step 8: Lint + type-check**

```bash
uv run ruff check src/acheron/shell/transports/http.py tests/shell/transports/test_http_worker.py
uv run mypy src/acheron/shell/transports/http.py
```

Expected: clean.

- [ ] **Step 9: Commit**

```bash
git add src/acheron/shell/transports/http.py tests/shell/transports/test_http_worker.py
git commit -m "refactor(transport): HttpWorker.execute uses match; ASR|TRANSLATION|TTS share _execute_with_upstream_input"
```

---

### Task 7: Refactor `Qwen3TTSRunpodHandler.handle` to read from `Input`

**Files:**
- Modify: `workers/qwen3tts/handler.py:142-196`
- Modify: `workers/qwen3tts/handler.py:99-115` (capabilities)
- Modify: `workers/qwen3tts/tests/test_handler.py` (full rewrite of fixtures)
- Modify: `workers/qwen3tts/tests/test_capabilities.py` (assert max_input_tokens)

- [ ] **Step 1: Add `max_input_tokens` to the qwen3tts capabilities**

Modify `workers/qwen3tts/handler.py`. Add `import json` at the top (if not present) and update the `capabilities` method:

```python
    def capabilities(self) -> WorkerCapabilities:
        """Return the worker's static capabilities (no I/O, sync)."""
        metadata: dict[str, JsonValue] = {
            "speakers": cast("list[JsonValue]", sorted(_ALL_SPEAKERS)),
            "default_speaker": self._settings.default_speaker,
        }
        return WorkerCapabilities(
            worker_type=WorkerType.TTS,
            supported_languages_in=frozenset(_LANG_MAP),
            supported_languages_out=frozenset(_LANG_MAP),
            supported_formats_in=frozenset({"text"}),
            supported_formats_out=frozenset({"wav"}),
            max_payload_bytes=None,
            batch_capable=True,
            max_input_tokens=2048,  # qwen3-tts is a 2K-context model
            model_source=f"huggingface:{_MODEL_ID}",
            metadata=metadata,
        )
```

- [ ] **Step 2: Write the failing test for the new behaviour**

Add to `workers/qwen3tts/tests/test_capabilities.py`:

```python
def test_capabilities_max_input_tokens() -> None:
    from workers.qwen3tts.handler import Qwen3TTSRunpodHandler

    h = Qwen3TTSRunpodHandler(_settings())
    assert h.capabilities().max_input_tokens == 2048
```

- [ ] **Step 3: Run the new test; verify it passes**

```bash
uv run pytest workers/qwen3tts/tests/test_capabilities.py -v
```

Expected: PASS.

- [ ] **Step 4: Refactor `handle` to read from `Input`**

Modify `workers/qwen3tts/handler.py`. Replace the body of `handle`:

```python
    async def handle(self, job: Job, input: Input | None = None) -> list[Artifact]:  # noqa: A002
        """Run batched custom-voice inference for all chunks in the job.

        Chunks arrive via the ``input`` parameter (8b's ``BytesInput`` Protocol):
        JSON-serialised ``chunks.json`` from the upstream chunking step. ``input``
        is required; chunks in ``job.payload`` is no longer a supported path.
        """
        if self._model is None:
            msg = "Qwen3-TTS model not loaded (startup() not run)"
            raise WorkerError(msg)
        if input is None:
            msg = "Qwen3-TTS requires a chunks.json input (multipart part)"
            raise WorkerError(msg)
        chunks_json_bytes = b"".join([chunk async for chunk in input.stream()])
        if not chunks_json_bytes:
            return []
        try:
            raw_chunks = json.loads(chunks_json_bytes.decode("utf-8"))
        except (json.JSONDecodeError, UnicodeDecodeError) as exc:
            msg = f"chunks.json is not valid JSON: {exc}"
            raise WorkerError(msg) from exc
        if not isinstance(raw_chunks, list):
            msg = "chunks.json must be a JSON array of chunk dicts"
            raise WorkerError(msg)
        chunks: list[dict[str, Any]] = [c for c in raw_chunks if isinstance(c, dict)]
        if not chunks:
            return []
        target_lang = job.payload.get("target_language")
        if not isinstance(target_lang, str) or target_lang not in _LANG_MAP:
            msg = f"Unsupported target language: {target_lang!r}"
            raise WorkerError(msg)
        qwen_lang = _LANG_MAP[target_lang]

        speaker = job.payload.get("speaker")
        if not isinstance(speaker, str) or not speaker:
            speaker = self._settings.per_language_defaults.get(target_lang, self._settings.default_speaker)
        if speaker not in _ALL_SPEAKERS:
            msg = f"Unknown speaker '{speaker}' in worker config"
            raise WorkerError(msg)

        texts = [_chunk_text(c) for c in chunks]
        languages = [qwen_lang] * len(chunks)
        speakers = [speaker] * len(chunks)
        instructs = [c.get("instruct", "") for c in chunks]

        import soundfile as sf  # noqa: PLC0415 - lazy, not always installed

        def _generate() -> tuple[list[Any], int]:
            return self._model.generate_custom_voice(  # type: ignore[no-any-return]
                text=texts, language=languages, speaker=speakers, instruct=instructs
            )

        wavs, sr = await asyncio.to_thread(_generate)

        artifacts: list[Artifact] = []
        for i, (wav, chunk) in enumerate(zip(wavs, chunks, strict=True)):
            buf = io.BytesIO()
            sf.write(buf, wav, sr, format="WAV")
            seq = chunk.get("sequence_id", i)
            chapter_id = _chunk_chapter_id(chunk)
            artifacts.append(
                BytesArtifact(
                    filename=f"{chapter_id}_{seq:04d}.wav",
                    content_type="audio/wav",
                    data=buf.getvalue(),
                    metadata={
                        "sequence_id": seq,
                        "chapter_id": chapter_id,
                        "sample_rate": sr,
                    },
                )
            )
        return artifacts
```

Add `Input` to the imports at the top of `handler.py`:

```python
from acheron.worker_sdk.inputs import Input
```

Add `import json` at the top of `handler.py` (if not already present).

- [ ] **Step 5: Update the existing test fixtures**

Replace `workers/qwen3tts/tests/test_handler.py` with a version that constructs `BytesInput` from chunks:

```python
"""Unit tests for Qwen3TTSRunpodHandler.handle() with the model mocked."""

from __future__ import annotations

import json
from typing import Any, cast

import numpy as np
import pytest

from acheron.core.errors import WorkerError
from acheron.core.models import Job, JsonValue, WorkerType
from acheron.worker_sdk.artifacts import BytesArtifact
from acheron.worker_sdk.inputs import BytesInput
from acheron.worker_sdk.settings import WorkerSettings


def _settings(**overrides: Any) -> WorkerSettings:
    base: dict[str, Any] = {
        "worker_id": "w",
        "orchestrator_url": "http://o:8000",
        "price_source": "zero",
        "default_speaker": "Ryan",
    }
    base.update(overrides)
    return WorkerSettings(**base)


def _build_input(chunks: list[dict[str, Any]]) -> BytesInput:
    return BytesInput(
        content_type="application/json",
        data=json.dumps(chunks).encode("utf-8"),
    )


def _build_job(target_language: str = "en") -> Job:
    payload: dict[str, JsonValue] = {
        "chapter_id": "ch1",
        "target_language": target_language,
    }
    return Job(
        job_id="job-xyz-synth-ch1",
        job_type=WorkerType.TTS,
        payload=payload,
        chapter_id="ch1",
    )


class _FakeModel:
    def __init__(self, wavs: list[np.ndarray], sr: int) -> None:
        self._wavs = wavs
        self._sr = sr

    def generate_custom_voice(
        self, text: list[str], language: list[str], speaker: list[str], instruct: list[str]
    ) -> tuple[list[np.ndarray], int]:
        return self._wavs, self._sr


class TestHandle:
    @pytest.mark.asyncio
    async def test_handle_returns_bytes_artifacts_in_order(self) -> None:
        from workers.qwen3tts.handler import Qwen3TTSRunpodHandler

        h = Qwen3TTSRunpodHandler(_settings())
        h._model = _FakeModel(
            wavs=[np.zeros(100, dtype=np.float32), np.zeros(200, dtype=np.float32)],
            sr=22050,
        )
        job = _build_job()
        chunks = [
            {"chapter_id": "ch1", "sequence_id": 0, "text": "hello"},
            {"chapter_id": "ch1", "sequence_id": 1, "text": "world"},
        ]
        out = await h.handle(job, input=_build_input(chunks))
        assert len(out) == 2
        bytes_arts = cast("list[BytesArtifact]", out)
        assert all(isinstance(a, BytesArtifact) for a in bytes_arts)
        assert bytes_arts[0].filename == "ch1_0000.wav"
        assert bytes_arts[1].filename == "ch1_0001.wav"
        assert bytes_arts[0].content_type == "audio/wav"
        assert bytes_arts[0].metadata["sequence_id"] == 0
        assert bytes_arts[1].metadata["sequence_id"] == 1

    @pytest.mark.asyncio
    async def test_handle_empty_chunks_returns_empty_list(self) -> None:
        from workers.qwen3tts.handler import Qwen3TTSRunpodHandler

        h = Qwen3TTSRunpodHandler(_settings())
        h._model = _FakeModel([], 22050)
        job = _build_job()
        out = await h.handle(job, input=_build_input([]))
        assert out == []

    @pytest.mark.asyncio
    async def test_handle_no_input_raises_worker_error(self) -> None:
        from workers.qwen3tts.handler import Qwen3TTSRunpodHandler

        h = Qwen3TTSRunpodHandler(_settings())
        h._model = _FakeModel([], 22050)
        job = _build_job()
        with pytest.raises(WorkerError, match="requires a chunks.json input"):
            await h.handle(job, input=None)

    @pytest.mark.asyncio
    async def test_handle_unknown_language_raises_worker_error(self) -> None:
        from workers.qwen3tts.handler import Qwen3TTSRunpodHandler

        h = Qwen3TTSRunpodHandler(_settings())
        h._model = _FakeModel([], 22050)
        job = _build_job(target_language="xx")
        with pytest.raises(WorkerError, match="Unsupported target language"):
            await h.handle(job, input=_build_input([{"chapter_id": "ch1", "sequence_id": 0, "text": "hi"}]))

    @pytest.mark.asyncio
    async def test_handle_unknown_speaker_in_config_raises_worker_error(self) -> None:
        from workers.qwen3tts.handler import Qwen3TTSRunpodHandler

        h = Qwen3TTSRunpodHandler(_settings(default_speaker="Bogus"))
        h._model = _FakeModel([np.zeros(50, dtype=np.float32)], 22050)
        job = _build_job()
        with pytest.raises(WorkerError, match="Unknown speaker"):
            await h.handle(job, input=_build_input([{"chapter_id": "ch1", "sequence_id": 0, "text": "hi"}]))

    @pytest.mark.asyncio
    async def test_handle_without_startup_raises_worker_error(self) -> None:
        from workers.qwen3tts.handler import Qwen3TTSRunpodHandler

        h = Qwen3TTSRunpodHandler(_settings())
        with pytest.raises(WorkerError, match="model not loaded"):
            await h.handle(_build_job(), input=_build_input([{"chapter_id": "ch1", "sequence_id": 0, "text": "hi"}]))

    @pytest.mark.asyncio
    async def test_handle_per_language_default_overrides_global_default(self) -> None:
        from workers.qwen3tts.handler import Qwen3TTSRunpodHandler

        settings = _settings(default_speaker="Ryan", per_language_defaults={"zh": "Vivian"})
        h = Qwen3TTSRunpodHandler(settings)
        h._model = _FakeModel([np.zeros(50, dtype=np.float32)], 22050)

        captured: dict[str, Any] = {}

        def _spy(text, language, speaker, instruct):
            captured["speaker"] = speaker
            return [np.zeros(50, dtype=np.float32)], 22050

        h._model.generate_custom_voice = _spy  # type: ignore[method-assign]
        job = _build_job(target_language="zh")
        await h.handle(job, input=_build_input([{"chapter_id": "ch1", "sequence_id": 0, "text": "你好"}]))
        assert captured["speaker"] == ["Vivian"]

    @pytest.mark.asyncio
    async def test_handle_uses_job_speaker_when_provided(self) -> None:
        from workers.qwen3tts.handler import Qwen3TTSRunpodHandler

        h = Qwen3TTSRunpodHandler(_settings(default_speaker="Ryan"))
        h._model = _FakeModel([np.zeros(50, dtype=np.float32)], 22050)

        captured: dict[str, Any] = {}

        def _spy(text, language, speaker, instruct):
            captured["speaker"] = speaker
            return [np.zeros(50, dtype=np.float32)], 22050

        h._model.generate_custom_voice = _spy  # type: ignore[method-assign]
        job = Job(
            job_id="j1",
            job_type=WorkerType.TTS,
            payload={"chapter_id": "ch1", "target_language": "en", "speaker": "Dylan"},
            chapter_id="ch1",
        )
        await h.handle(job, input=_build_input([{"chapter_id": "ch1", "sequence_id": 0, "text": "hi"}]))
        assert captured["speaker"] == ["Dylan"]

    @pytest.mark.asyncio
    async def test_handle_chunks_with_no_chapter_id_raises_worker_error(self) -> None:
        from workers.qwen3tts.handler import Qwen3TTSRunpodHandler

        h = Qwen3TTSRunpodHandler(_settings())
        h._model = _FakeModel([], 22050)
        with pytest.raises(WorkerError, match="chapter_id"):
            await h.handle(_build_job(), input=_build_input([{"sequence_id": 0, "text": "hi"}]))

    @pytest.mark.asyncio
    async def test_handle_chunks_with_no_text_raises_worker_error(self) -> None:
        from workers.qwen3tts.handler import Qwen3TTSRunpodHandler

        h = Qwen3TTSRunpodHandler(_settings())
        h._model = _FakeModel([], 22050)
        with pytest.raises(WorkerError, match="chunk.text"):
            await h.handle(_build_job(), input=_build_input([{"chapter_id": "ch1", "sequence_id": 0}]))

    @pytest.mark.asyncio
    async def test_handle_chapter_id_with_slash_raises_worker_error(self) -> None:
        from workers.qwen3tts.handler import Qwen3TTSRunpodHandler

        h = Qwen3TTSRunpodHandler(_settings())
        h._model = _FakeModel([], 22050)
        with pytest.raises(WorkerError, match="path component"):
            await h.handle(_build_job(), input=_build_input([{"chapter_id": "../../etc", "sequence_id": 0, "text": "x"}]))

    @pytest.mark.asyncio
    async def test_handle_chapter_id_dotdot_raises_worker_error(self) -> None:
        from workers.qwen3tts.handler import Qwen3TTSRunpodHandler

        h = Qwen3TTSRunpodHandler(_settings())
        h._model = _FakeModel([], 22050)
        with pytest.raises(WorkerError, match="path component"):
            await h.handle(_build_job(), input=_build_input([{"chapter_id": "..", "sequence_id": 0, "text": "x"}]))

    @pytest.mark.asyncio
    async def test_handle_chapter_id_with_nul_raises_worker_error(self) -> None:
        from workers.qwen3tts.handler import Qwen3TTSRunpodHandler

        h = Qwen3TTSRunpodHandler(_settings())
        h._model = _FakeModel([], 22050)
        with pytest.raises(WorkerError, match="illegal whitespace"):
            await h.handle(_build_job(), input=_build_input([{"chapter_id": "ch1\x00admin", "sequence_id": 0, "text": "x"}]))

    @pytest.mark.asyncio
    async def test_handle_malformed_chunks_json_raises(self) -> None:
        from workers.qwen3tts.handler import Qwen3TTSRunpodHandler

        h = Qwen3TTSRunpodHandler(_settings())
        h._model = _FakeModel([], 22050)
        bad = BytesInput(content_type="application/json", data=b"not json {{{")
        with pytest.raises(WorkerError, match="not valid JSON"):
            await h.handle(_build_job(), input=bad)

    @pytest.mark.asyncio
    async def test_handle_chunks_json_not_list_raises(self) -> None:
        from workers.qwen3tts.handler import Qwen3TTSRunpodHandler

        h = Qwen3TTSRunpodHandler(_settings())
        h._model = _FakeModel([], 22050)
        bad = BytesInput(content_type="application/json", data=b'{"a": 1}')
        with pytest.raises(WorkerError, match="JSON array"):
            await h.handle(_build_job(), input=bad)
```

- [ ] **Step 6: Run the qwen3tts tests; verify they pass**

```bash
uv run pytest workers/qwen3tts/tests -v
```

Expected: PASS.

- [ ] **Step 7: Lint + type-check**

```bash
uv run ruff check workers/qwen3tts/handler.py workers/qwen3tts/tests/
uv run mypy workers/qwen3tts/handler.py
```

Expected: clean.

- [ ] **Step 8: Commit**

```bash
git add workers/qwen3tts/handler.py workers/qwen3tts/tests/test_handler.py workers/qwen3tts/tests/test_capabilities.py
git commit -m "refactor(qwen3tts): handler reads chunks from Input; capabilities publish max_input_tokens=2048"
```

---

### Task 8: Final gate

**Files:**
- (no source changes; verification only)

- [ ] **Step 1: Run `just lint-strict`**

```bash
just lint-strict
```

Expected: clean (lint-strict, lint-imports).

- [ ] **Step 2: Run `just type-check`**

```bash
just type-check
```

Expected: clean (mypy + basedpyright).

- [ ] **Step 3: Run `just test`**

```bash
just test
```

Expected: all tests pass; coverage ≥ 80%.

- [ ] **Step 4: Run `just validate`**

```bash
just validate
```

Expected: clean (all four gates).

- [ ] **Step 5: Commit if any autofixes**

If `just validate` made autofixes, commit them:

```bash
git status  # review changes
git add -A
git commit -m "chore(layer8c): final validate polish" --allow-empty
```

- [ ] **Step 6: Hand off to Sub-plan 2**

Sub-plan 2 (`2026-06-23-layer8c-translategemma-worker-and-deploy.md`) ships the `workers/translategemma/` workspace member, its handler + tests, the Dockerfile + worker yamls, the docker-compose service, the Justfile target, and the GHCR CI workflow. The cross-cutting fix from this sub-plan must be merged to `main` before the worker is registered; otherwise the worker would publish a `capabilities` without `max_input_tokens` (the new field would be `None`, and the planner check would skip it).
