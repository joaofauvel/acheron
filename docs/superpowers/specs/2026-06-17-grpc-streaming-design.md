# gRPC Streaming Transport — Layer 6

**Date:** 2026-06-17
**Status:** Done
**Depends on:** Layer 0-5

## Overview

GrpcWorker implements `StreamingWorker` via server-side gRPC streaming for TTS. The GPU worker streams PCM audio chunks back as they're generated, bypassing disk I/O. Orchestrator is unaware of streaming — GrpcWorker presents the same interface as HttpWorker.

## Proto Definition

```protobuf
// proto/acheron/synthesis.proto
syntax = "proto3";
package acheron;

service Synthesis {
  rpc Synthesize(SynthesisRequest) returns (stream AudioChunk);
}

message SynthesisRequest {
  string job_id = 1;
  string text = 2;
  string language = 3;
  string model = 4;
}

message AudioChunk {
  bytes pcm_data = 1;
  int32 sample_rate = 2;
  int32 channels = 3;
}
```

Server-side streaming: client sends one `SynthesisRequest`, server streams back `AudioChunk` messages with raw PCM bytes.

## GrpcWorker

Implements `StreamingWorker` (same interface as HttpWorker). Constructor takes a gRPC channel.

- `execute(job)` — TTS: calls `Synthesize`, collects all `AudioChunk` into `JobResult` with assembled PCM bytes. Non-TTS: raises `WorkerError`.
- `health()` — gRPC health checking protocol (`grpc.health.v1.Health/Check`).
- `capabilities()` — hardcoded TTS capabilities (no HTTP call needed).
- `submit_batch(batch)` / `poll_batch(handle)` / `collect_results(handle)` — delegates to `execute` per job. Streaming is transparent; same batch behavior as HttpWorker.

Error handling: `grpc.aio.AioRpcError` caught and mapped to `WorkerError` / `WorkerUnavailableError`.

## Stub gRPC TTS Worker

Local gRPC server implementing `Synthesis` service. Returns canned silent PCM chunks. Registers with orchestrator on startup via HTTP `POST /workers` (same as HTTP stubs).

- `grpc_worker_stub.py` — gRPC server + self-registration
- Runs on port 9001

## File Layout

```
proto/acheron/synthesis.proto
src/acheron/shell/transports/grpc.py
tests/shell/test_grpc_worker.py
stubs/grpc_worker_stub.py
stubs/tests/test_grpc_worker_stub.py
```

## Dependencies

Added via `uv add`:
- `grpcio~=1.81`
- `grpcio-tools~=1.81` (dev dependency)
- `grpcio-health-checking~=1.81`

## Proto Compilation

`grpcio-tools` generates Python stubs from `.proto` files. Generated code goes to `src/acheron/proto/`. Compilation via `uv run python -m grpc_tools.protoc`.

## Docker Compose

New `tts-grpc-stub` service:
- Same container as HTTP stubs
- Runs gRPC server on port 9001
- Registers with orchestrator as `transport: "grpc"`
- `docker-compose.yml` adds port 9001 mapping

## What This Doesn't Do

- No bidirectional streaming (server-side only)
- No ASR/translation gRPC streaming (TTS only)
- No orchestrator awareness of streaming mechanism
- No client-side streaming (text sent as single message)
