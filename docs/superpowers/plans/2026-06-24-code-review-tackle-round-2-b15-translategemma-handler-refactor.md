---
bundle: B15
name: TRANSLATEGEMMA handler refactor
severity: LOW
stories: 3
m_effort: 3
main_plan: 2026-06-24-code-review-tackle-round-2.md
---

# B15 — TRANSLATEGEMMA handler refactor (CORR-029, -032, -033)

> **For agentic workers:** Use the **Common Workflow** from the main plan. All 3 stories are M-effort and need full TDD. **Tackle in this order: CORR-032 (stream chunks.json) → CORR-033 (don't mutate tokenizer) → CORR-029 (partial success).** B14's `parse_chunks_json` and `Chunk` dataclass should land first; verify in the spec.

**Bundle summary:** Refactor `TranslateGemmaRunpodHandler` to (1) stream the chunks.json input instead of materialising it, (2) deep-copy the tokenizer per call instead of mutating it, (3) handle per-chunk failures with partial success instead of discarding all work.

**Prereqs:** B14's `parse_chunks_json` + `Chunk` dataclass in `workers/_shared/chunks.py`. B14 should land before B15.

**Expected commits:** 3.

---

## Tasks

### Task 1: CORR-032 (M) — `TranslateGemmaRunpodHandler.handle` materializes the entire chunks.json in memory

**Story:** `docs/code_review/correctness.md` § CORR-032 (LOW, M effort).

**Files:**
- Modify: `workers/translategemma/handler.py`.
- Test: `workers/translategemma/tests/test_handler.py` (add a streaming test).

#### Step 1: Write the failing test

```python
def test_handle_streams_chunks_json_does_not_materialise(monkeypatch):
    """CORR-032: handle() must stream the chunks.json input, not materialise it in memory."""
    # Create a 10 MB chunks.json input.
    # Mock `parse_chunks_json` to be a generator that yields chunks one at a time.
    # Call handle().
    # Assert that the mock was called with streaming=True (or that `parse_chunks_json` was
    # iterated lazily, not materialised as a list).
    ...
```

The exact mock setup depends on the current `handle` signature. Inspect the code first.

#### Step 2-3: Implement streaming

Replace the `chunks = list(parse_chunks_json(input))` pattern (or equivalent) with a streaming `for chunk in parse_chunks_json(input):` loop. The handler should process one chunk at a time and yield/return partial results.

#### Step 4-5: Run test, verify gate, subagent passes, commit

**Commit:** `fix(CORR-032): stream chunks.json input in TranslateGemmaRunpodHandler.handle`.

---

### Task 2: CORR-033 (M) — `_translate_batch` mutates the shared processor's tokenizer in-place

**Story:** `docs/code_review/correctness.md` § CORR-033 (LOW, M effort).

**Files:**
- Modify: `workers/translategemma/handler.py`.
- Test: `workers/translategemma/tests/test_handler.py`.

#### Step 1: Write the failing test

```python
def test_translate_batch_does_not_mutate_shared_tokenizer(monkeypatch):
    """CORR-033: _translate_batch must deep-copy the tokenizer per call."""
    # Mock the processor with a tokenizer that has a mutable attribute (e.g. padding_side).
    # Call _translate_batch once.
    # Assert the original tokenizer's padding_side is unchanged.
    # Call _translate_batch again with a different padding_side setting.
    # Assert the original is still unchanged (the second call's mutation is local).
    ...
```

#### Step 2-3: Implement deep-copy

Before any mutation, do `tokenizer = copy.deepcopy(self._processor.tokenizer)`. Use the copy in the function. Drop the copy at the end.

#### Step 4-5: Run test, verify gate, subagent passes, commit

**Commit:** `fix(CORR-033): deep-copy processor.tokenizer in _translate_batch to avoid in-place mutation`.

---

### Task 3: CORR-029 (M) — `_translate_batch` has no partial-success handling; mid-batch failure discards all completed work

**Story:** `docs/code_review/correctness.md` § CORR-029 (MEDIUM, M effort).

**Files:**
- Modify: `workers/translategemma/handler.py`.
- Test: `workers/translategemma/tests/test_handler.py`.

#### Step 1: Write the failing test

```python
def test_translate_batch_partial_success(monkeypatch):
    """CORR-029: a mid-batch failure should not discard previously translated chunks."""
    # Mock the model.generate to raise on the 3rd chunk.
    # Call _translate_batch with 5 chunks.
    # Assert that chunks 0, 1, 3, 4 (or 0, 1, 2, 4) are returned (depending on the threshold).
    # The 3rd chunk is the failure; the others succeed.
    ...


def test_translate_batch_below_threshold_raises(monkeypatch):
    """If the success rate is below the threshold, raise WorkerError."""
    # Mock the model.generate to raise on 4 out of 5 chunks.
    # Assert WorkerError is raised with the success_rate and the failed chunk indices.
    ...
```

#### Step 2-3: Implement partial-success

Wrap each chunk's translate call in `try/except (torch.cuda.OutOfMemoryError, ValueError)`:

```python
results: list[TranslatedChunk] = []
failed: list[ChunkRef] = []
for i, chunk in enumerate(chunks):
    try:
        results.append(self._translate_one(chunk))
    except (torch.cuda.OutOfMemoryError, ValueError) as exc:
        logger.warning("chunk %d failed: %s", chunk.id, exc)
        failed.append(ChunkRef(id=chunk.id, error=str(exc)))

if not results:
    raise WorkerError(f"all chunks failed: {failed}")
success_rate = len(results) / (len(results) + len(failed))
if success_rate < 0.5:  # configurable threshold
    raise WorkerError(f"success rate {success_rate:.1%} below threshold: {failed}")
return TranslationResult(translated=results, failed=failed)
```

Add `Settings.orchestrator.translation.success_rate_threshold: float = 0.5` (or per-worker). Make the threshold configurable.

#### Step 4-5: Run test, verify gate, subagent passes, commit

**Commit:** `fix(CORR-029): handle per-chunk failures in _translate_batch with partial success`.

---

## Bundle summary

- **Stories:** 3 (all M-effort).
- **Commits:** 3.
- **Order matters:** CORR-032 → CORR-033 → CORR-029.
- **Prereq:** B14's `parse_chunks_json` and `Chunk` dataclass. If B14 hasn't landed, do B14 first.
- **External libs:** `torch.cuda.OutOfMemoryError` is the standard exception; no new deps.
- **Surface to user if:** the threshold needs to be per-call (not per-worker), or the partial-success response shape needs to be a new Pydantic model.
