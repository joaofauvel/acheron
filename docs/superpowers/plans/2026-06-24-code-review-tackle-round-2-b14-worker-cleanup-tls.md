---
bundle: B14
name: Worker cleanup & TLS boilerplate
severity: MEDIUM
stories: 4
m_effort: 0
main_plan: 2026-06-24-code-review-tackle-round-2.md
---

# B14 — Worker cleanup & TLS boilerplate (ARCH-021, MAINT-017, -018, -019)

> **For agentic workers:** Use the **Common Workflow** from the main plan. Each task is S-effort with coarse detail. Read the full story text in `docs/code_review/architecture.md` and `docs/code_review/code-quality.md` before implementing.

**Bundle summary:** Extract shared uvicorn server runner, extract `parse_chunks_json` helper, split `TranslateGemmaRunpodHandler.handle` into smaller methods. All consolidations.

**Expected commits:** 3-4.

---

## Tasks

### Task 1: ARCH-021 — identical uvicorn+TLS 7-line boilerplate duplicated across 4 entry points

**Files:** `stubs/_sdk_base/server_runner.py` (new) or `src/acheron/worker_sdk/_server.py`; the 4 entry points (qwen3tts, granite_speech, translategemma, tts_local_stub, tts_grpc_stub, plus the edge image entry).

**Change:** extract `def run_worker_server(app: FastAPI, *, host: str, port: int, ssl_ctx: ssl.SSLContext | None = None) -> None: ...` and call it from all 4 entry points. The function should use uvicorn's `Config` and `Server` classes to support the SSL context.

**Test:** the 4 entry points should still start their servers correctly; the existing integration tests cover this. Add 1 unit test on the new `run_worker_server` function with a mock uvicorn server.

**Commit:** `refactor(ARCH-021): extract run_worker_server to share uvicorn+TLS boilerplate`.

---

### Task 2: MAINT-017 — `chunks.json` parsing duplicated byte-for-byte between qwen3tts and translategemma handlers

**Files:** `workers/_shared/chunks.py` (new) or `workers/_shared/chunks_parser.py`; `workers/qwen3tts/handler.py`; `workers/translategemma/handler.py`; test.

**Change:** extract `parse_chunks_json(input: BytesInput) -> list[Chunk]` (where `Chunk` is the shared dataclass from Task 3). Both handlers call it.

**Test:** add 3 unit tests on `parse_chunks_json` (valid input, empty array, malformed JSON). The existing handler tests should still pass.

**Commit:** `refactor(MAINT-017): extract parse_chunks_json to workers/_shared`.

---

### Task 3: MAINT-018 — per-chunk field validation duplicated between translategemma and qwen3tts

**Files:** `workers/_shared/chunks.py` (continue from Task 2); test.

**Change:** add `Chunk` dataclass and `validate_chunk_fields(chunk) -> None` in `workers/_shared/chunks.py`. Both handlers use them.

**Test:** 3 unit tests on `validate_chunk_fields` (valid chunk, missing required field, invalid value).

**Commit:** `refactor(MAINT-018): share Chunk dataclass and validate_chunk_fields in workers/_shared`.

---

### Task 4: MAINT-019 — `TranslateGemmaRunpodHandler.handle` is 54 lines and bundles 3 concerns

**Files:** `workers/translategemma/handler.py`; test.

**Change:** split `handle` into `_validate_payload(payload)`, `_parse_chunks(input)`, `_translate_and_artifact(chunks)`. The entry point orchestrates: `payload = self._validate_payload(payload); chunks = self._parse_chunks(input); return self._translate_and_artifact(chunks)`.

**Test:** the existing handler tests should still pass. Add 1 unit test on each new private method.

**Commit:** `refactor(MAINT-019): split TranslateGemmaRunpodHandler.handle into _validate_payload, _parse_chunks, _translate_and_artifact`.

---

## Bundle summary

- **Stories:** 4 (all S).
- **Commits:** 3-4 (Tasks 2+3 share the `chunks.py` file and should land together).
- **Cross-bundle:** B15's CORR-032 (handle materializes chunks.json) is the next-layer fix for the same handler; B15 should land after B14.
- **Surface to user if:** the `Chunk` dataclass needs fields that are specific to one worker (don't add them; the dataclass should only have the common fields).
