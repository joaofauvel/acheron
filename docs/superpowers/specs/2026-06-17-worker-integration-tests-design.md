# Worker Integration Tests — Design

**Date:** 2026-06-17
**Status:** Draft
**Depends on:** Layer 0-6

## Overview

Integration tests verifying the full orchestrator → step handler → worker → result path with real stub workers. Tests both HTTP and gRPC transports, happy paths, error paths, and edge cases.

## Architecture

Tests run real stub workers as background tasks (FastAPI/gRPC servers on localhost), register them with a real orchestrator, submit jobs via `submit_job()`, and verify results.

## Worker Factory

The `_default_worker_factory` in `step_handler.py` is updated to dispatch based on transport:

```python
def _default_worker_factory(registered: RegisteredWorker) -> Worker:
    if registered.transport == "grpc":
        channel = grpc.aio.insecure_channel(registered.endpoint)
        return GrpcWorker(channel)
    return HttpWorker(registered.endpoint)
```

This is a real code change — the factory now supports both HTTP and gRPC workers.

## Translation Stub

New `stubs/translation_stub.py` — minimal FastAPI app returning mock translated text. Appends " [translated]" to input text. Same self-registration pattern as other stubs.

## Test Scenarios

### Happy Path

1. **EPUB → TTS (HTTP)**: Submit `EpubRequest(en→es)`, verify plan completes with all steps done
2. **EPUB → TTS (gRPC)**: Same but TTS uses gRPC transport
3. **Audio → ASR → TTS**: Submit `AudioRequest(en→es)`, verify ASR and TTS steps complete

### Error Path

4. **Worker unreachable**: Register worker at unreachable endpoint, submit job, verify job fails with appropriate error
5. **Worker returns error**: Stub returns 500, verify step fails and job marked failed

### Edge Cases

6. **No matching worker**: Submit job for unsupported language pair, verify `WorkerError`
7. **Multiple workers same type**: Register two TTS workers (HTTP + gRPC), verify first match is used
8. **Registration token**: Verify workers must authenticate to register

## File Layout

```
tests/integration/test_worker_integration.py
stubs/translation_stub.py
stubs/tests/test_translation_stub.py
src/acheron/shell/step_handler.py  — updated factory
```

## Fixtures

- `http_tts_stub`, `http_asr_stub`, `http_translation_stub` — HTTP stub servers
- `grpc_tts_stub` — gRPC stub server
- `worker_registry` — registry with all stubs registered
- `orchestrator` — real orchestrator with transport-aware factory
