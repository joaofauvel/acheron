# Design Spec: README.md Redesign from the Ground Up

**Date:** 2026-06-24
**Status:** Revised
**Topic:** Ground-up rewrite and redesign of `README.md` for Acheron

---

## 1. Goal & Objectives

Acheron's current `README.md` is outdated, exposes internal implementation details while lacking key overview concepts, and misleads users about TLS, worker setup, and SDK transports. The goal of this redesign is to rewrite the document from scratch to balance the needs of:

- **End-users** running the CLI and dashboard to submit and monitor audiobook jobs.
- **Operators** deploying serverless GPU workers, configuring edge proxies, managing network variables, and setting up TLS.

---

## 2. Document Structure (Three-Tiered Progressive Model)

The new `README.md` will be organized into three progressive tiers that support scannability and serve both audiences without forcing either to skip the other.

### Tier 1: Quick Start & CLI User Guide

**What Acheron is.** One-paragraph summary: a distributed asynchronous audio-transformation pipeline that converts EPUB or audio input into chapterized audiobooks in a target language.

**Prerequisites.**
- System: Python 3.14+, `uv` (package manager), `just` (command runner), `direnv` (optional, auto-activates venv), Docker and Compose.
- CLI: `acheron` (for submitting and monitoring jobs), `runpodctl` (operators only, for creating RunPod endpoints).

**Quick Start (Local Dev).**
```bash
cp .env.example .env
docker compose up --build
```

Default local services:
- Orchestrator at `https://localhost:8000`. TLS is auto-enabled because the `certs-init` one-shot service generates a self-signed CA + per-service certs into `./certs/` and the compose file mounts them into every container.
- Dashboard at `http://localhost:8080`.
- Redis on `localhost:6379`.
- Local stub workers (TTS, ASR, translation, gRPC) auto-register with the orchestrator and return mock data. Replace with real GPU workers for production.

**Basic CLI Commands.**
- Submit EPUB: `acheron job submit book.epub --src en --dest es`
- Submit audio: `acheron job submit podcast.mp3 --src en --dest es --asr whisper-v3`
- Status: `acheron job status job-xyz`, `acheron job status job-xyz --verbose`
- Resumption: `acheron job resume job-xyz` (reuses step cache), `acheron job resume job-xyz --force-fresh` (re-runs from scratch)
- System overview: `acheron status`, `acheron jobs --active`, `acheron jobs --completed`
- Workers: `acheron workers`
- Capabilities: `acheron capabilities --src en --dest es`

**Dashboard.** Brief intro to the HTMX-based web UI for live monitoring at `http://localhost:8080`.

**Development (key `just` targets).**
- `just validate` — full pipeline: lint, import-lint, type-check (mypy + basedpyright), test.
- `just lint-strict` / `just type-check` / `just type-check-pyright` / `just lint-imports` / `just test` — individual gates.
- `just proto` — regenerate protobuf code after editing `proto/synthesis.proto`.
- `just certs` — regenerate dev TLS CA + per-service certs in `./certs/` (not needed for `docker compose up`; `certs-init` does this).
- `just build-worker <name>` / `just build-edge` — local image builds for dev iteration. CI publishes images to `ghcr.io` on pushes to `main` and version tags.

---

### Tier 2: Conceptual Architecture & Streaming

**Mermaid Architecture Diagram.** Shows the data path with Step Cache nested under the Orchestrator (Step Cache is a sub-component of the orchestrator's `ACHERON_DATA_DIR`, not a separate downstream node):
```
CLI → Orchestrator (with local Step Cache) → Edge Worker (proxy, GPU-less) → RunPod Serverless
```
The orchestrator's local in-process CPU handlers (EXTRACTION, CHUNKING, PACKAGING) handle the orchestration steps in-process; only GPU-bearing steps (TTS, ASR, translation) traverse the Edge Worker.

**Core Concepts.**

- **Serverless GPU Workers.** RunPod endpoints scale to 0 GPU instances when idle and cold-start on a job's arrival — no always-on GPU cost. Workers boot, run the job, and shut down on idle timeout.

- **Edge Workers (GPU-less Proxies).** GPU workers inside RunPod serverless do not expose `/health` or `/execute` directly and do not register back to Acheron. An Edge Worker is a lightweight, GPU-less container with HTTP(S) reachability to the orchestrator that:
  1. Registers with the orchestrator at startup.
  2. Serves `/health` and `/execute` locally.
  3. Forwards `/execute` payloads to the configured RunPod endpoint and reports the result back.
  4. Queries RunPod's GraphQL API for the endpoint's GPU type and price.

- **Local In-Process Handlers (CPU).** The orchestrator runs in-process handlers for three orchestration steps that do not need a GPU:
  - `EXTRACTION` — EPUB chapter extraction or audio file copy.
  - `CHUNKING` — text segmentation (default max 250 characters per chunk).
  - `PACKAGING` — FFmpeg concat demuxer produces `.m4b` audiobooks (bitrate `128k`, codec `aac` by default).
  These are auto-registered at orchestrator startup. User-registered workers override them.

- **Local GPU Workers — Not Implemented.** There is currently no path to run a GPU worker on the orchestrator host or on a separate GPU host you manage. All GPU inference goes through RunPod serverless via an Edge Worker proxy.

**Worker SDK & Transports.** The `worker_sdk` package is the framework every Layer 8 worker implements (`WorkerHandler` ABC with `capabilities()`, `handle()`, `startup()`, `shutdown()` hooks). It supports three transports:

- **HTTP `multipart/mixed`** — the default. File-backed artifacts (e.g., WAV on disk) are read in 64 KiB chunks to bound memory; byte-backed artifacts (e.g., per-chapter WAVs) are sent as a single part. The orchestrator materializes received parts into its own `ACHERON_DATA_DIR`, so workers and the orchestrator do not need a shared filesystem.
- **gRPC** — used by the gRPC stub and as an alternative transport. Uses protobuf-defined `Artifact` parts.
- **Local** — direct in-process invocation. Used by the local EXTRACTION/CHUNKING/PACKAGING handlers and the integration test suite.

**Per-worker configuration** is driven by a `worker.yaml` file (searched at `<cwd>/<worker_name>.worker.yaml`, then `<cwd>/worker.yaml`). Env vars prefixed with `ACHERON_WORKER__` override YAML values at runtime so the same image can be retargeted without rebuilding. **Three fields are env-only** (rejected when supplied via YAML or constructor — they must come from `os.environ`):
- `ACHERON_WORKER__REGISTRATION_TOKEN`
- `ACHERON_WORKER__RUNPOD_API_KEY`
- `ACHERON_WORKER__RUNPOD_ENDPOINT_ID`

**Data Flow, Concurrency, and Batching.**

- **Data Hierarchy.**
  - Book level: raw input (`.epub`, `.mp3`).
  - Chapter level: split files from extraction (`chapter_001.txt`).
  - Chunk level: sub-divided text segments (max 250 characters by default) compiled into `chunks.json`.
  - WAV fragments: individual WAV audio files generated by TTS for each chunk (`chapter_001_0000.wav`).
  - Chapterized audiobook: re-merged and packaged chapters in `.m4b` format.

- **Streaming Executor & Bounded Queues.** Downstream steps (translation, TTS) have hard data dependencies on their immediate upstream step — they cannot begin until the upstream step's first chunk is available. The streaming executor models the plan as a linear pipeline of stages connected by bounded `asyncio.Queue`s (backpressure, default size 4). Stages run concurrently inside a single outer `asyncio.TaskGroup`, so a failure in any stage cancels the others instantly. Each stage has a `step_timeout` default of 1800 seconds.

- **GPU Batching.** TTS (`qwen3tts`) and translation (`translategemma`) advertise `batch_capable=True` and synthesize a whole job's chunks in one batched model call. ASR (`granite_speech`) is `batch_capable=False` and transcribes per audio file.

- **Multipart Transport.** File-backed paths stream in 64 KiB parts via `multipart/mixed` over HTTP between the Orchestrator and Edge Worker. This prevents the entire file payload from being buffered in worker RAM.

---

### Tier 3: Operator Deployment & Configuration Reference

**RunPod Serverless Deployment Guide.**
- **Network Volume for HF cache.** Mount a RunPod Network Volume into the worker template and pre-warm it with `huggingface-cli download <model>` once. Cold starts then skip the multi-GB model download.
- **Template configuration.** Disk size ≥ 10 GB. GPU selection is workload-dependent (see VRAM guidance below).
- **Endpoint creation.** `workers_min: 0` (scale to zero on idle), `workers_max: 1` (or higher for fan-out), idle timeout ~300s. Point the endpoint at the GHCR image (`ghcr.io/<repo>/acheron-<name>-runpod:<tag>`).

**GPU & VRAM Guidance (rule of thumb — consult each model's Hugging Face card for authoritative requirements).**
- TTS (`Qwen3-TTS-12Hz-1.7B-CustomVoice`) / ASR (`ibm-granite/granite-speech-4.1-2b`): 24 GB+ VRAM (e.g., L4, A5000, RTX 3090).
- Translation (`google/translategemma-12b-it`): 24 GB+ VRAM is the community-cloud baseline; Secure Cloud may be required at higher concurrency or batch size. Consult the model card before sizing.

**Edge Worker Proxy Setup.**
- **Profile-based opt-in.** Real GPU workers are gated behind Docker Compose profiles: `docker compose --profile runpod-tts up --build` enables `qwen3tts-edge`; analogous profiles exist for `runpod-asr` and `runpod-translation`.
- **Primary config is `worker.yaml`.** Each worker ships a `worker.yaml` (e.g., `workers/qwen3tts/worker.yaml`) and a `worker.edge.yaml` for the Edge image. Operator-tunable keys include `worker_id`, `orchestrator_url`, `listen_port`, `execution_timeout_s`, `price_source`, `secure_cloud`, `default_speaker`, `per_language_defaults` (TTS), `output_mode` (`multipart` | `volume`), `model_id`, and `output_volume_dir` (required when `output_mode == "volume"`).
- **Secrets are env-only** (see Worker SDK section above): set `ACHERON_WORKER__REGISTRATION_TOKEN`, `ACHERON_WORKER__RUNPOD_API_KEY`, `ACHERON_WORKER__RUNPOD_ENDPOINT_ID` in `.env` or your secret store.

**GPU & Pricing Auto-Discovery.**
- The Edge Worker queries RunPod's GraphQL API on schedule (`RunPodPrice.refresh()`) to discover the endpoint's active GPU type and hourly rate. The worker does not configure `gpu_type` — RunPod is the source of truth.
- Cache TTL is `ACHERON_WORKER__PRICE_CACHE_TTL_S` (default 3600s). GPU changes on the endpoint take effect on the next refresh; no image rebuild.
- Fault-tolerance: a refresh failure falls back to the last cached rate (basis `CACHED`); if no rate is cached, the basis is `UNKNOWN`. The job is never blocked by a missing price.

**TLS & Hardening Guide.**
- **Defaults in the codebase.** TLS is opt-in. Setting `ACHERON_TLS_CERT_FILE` + `ACHERON_TLS_KEY_FILE` together enables HTTPS; if both are unset, `tls.py` logs a WARNING and serves plain HTTP. The Docker Compose stack auto-enables TLS by mounting self-signed certs from the `certs-init` service into every container, so local dev always runs HTTPS.
- **Production.** Mount real certs (Let's Encrypt via cert-manager, your CA, etc.) with the right SANs, and set both env vars. No Acheron code change required.
- **Client-side trust.** Set `ACHERON_TLS_CA_FILE` (or `SSL_CERT_FILE`) to the CA bundle. The CLI defaults to `./certs/acheron-ca.crt` when present.
- **Disabling TLS.** Unset the cert/key env vars. To silence the WARNING when plain HTTP is intentional, set `ACHERON_ALLOW_INSECURE=1`.
- **Reverse proxy (optional).** Acheron does not ship a proxy. Point nginx, Caddy, or anything else at the orchestrator (HTTPS) and dashboard (HTTP) and terminate TLS there. The `ACHERON_TLS_*` env vars are independent of any proxy you add.

**YAML Configuration Guide (`acheron.yaml`).**
- **Precedence.** `$ACHERON_CONFIG_PATH` → `./acheron.yaml` / `./acheron.yml` → `/etc/acheron/acheron.yaml` / `/etc/acheron/acheron.yml`. First match wins.
- **Top-level blocks.** `orchestrator:` (data_dir, registration_token, health_check_interval_seconds), `workers:` (chunking, packaging), `providers:` (RunPod and Hugging Face API keys for decoupled health checks), and `chars_per_token` (CJK worst-case estimate for chunk-fit validation; default 1).
- **Env-var overrides.** Use `__` to address nested keys (e.g., `ACHERON_ORCHESTRATOR__DATA_DIR` → `orchestrator.data_dir`). Flat aliases like `ACHERON_DATA_DIR` and `ACHERON_REGISTRATION_TOKEN` also work for orchestrator-level settings.
- An example template is in `acheron.yaml.example`. Copy it with `cp acheron.yaml.example acheron.yaml`.

**Configuration Reference Table.** A consolidated markdown table of every env var, grouped by surface, with default and one-line description. The table is the operator's authoritative reference.

| Group | Variable | Default | Description |
|-------|----------|---------|-------------|
| Orchestrator / URLs | `ACHERON_URL` | `https://localhost:8000` | CLI and dashboard: orchestrator URL. Use `http://` to skip TLS. |
| Orchestrator / Registration | `ACHERON_REGISTRATION_TOKEN` | (auto-generated) | Worker registration shared secret. If unset, the orchestrator generates a secure token on startup and writes it to `{data_dir}/.registration_token`. |
| Orchestrator / Registration | `ACHERON_OPEN_REGISTRATION` | (unset) | Set to `1` to enable open worker registration (bypasses token checks, useful for local dev). |
| Orchestrator / Config | `ACHERON_CONFIG_PATH` | (unset) | Custom path to the YAML configuration file (searches `acheron.yaml` / `acheron.yml` if unset). |
| Orchestrator / Storage | `ACHERON_DATA_DIR` | `/data/jobs` | Orchestrator: plan and step-output cache directory (must be writable; orchestrator fails fast at startup if not). |
| Orchestrator / Storage | `ACHERON_STORE_BACKEND` | `memory` | Orchestrator: `memory` (in-process, dev) or `redis` (persistent, production). |
| Orchestrator / Storage | `REDIS_URL` | `redis://localhost:6379` | Redis connection (used when `ACHERON_STORE_BACKEND=redis`). |
| Orchestrator / TLS | `ACHERON_TLS_CERT_FILE` | (unset) | Server: path to PEM-encoded server cert. Set with `ACHERON_TLS_KEY_FILE` to enable HTTPS. |
| Orchestrator / TLS | `ACHERON_TLS_KEY_FILE` | (unset) | Server: path to PEM-encoded server key. Set with `ACHERON_TLS_CERT_FILE` to enable HTTPS. |
| Orchestrator / TLS | `ACHERON_TLS_CA_FILE` | (unset) | gRPC and CLI clients: path to PEM-encoded CA bundle to verify peer certs. Falls back to `SSL_CERT_FILE`, then `./certs/acheron-ca.crt` in the CLI's CWD. |
| Orchestrator / TLS | `ACHERON_ALLOW_INSECURE` | (unset) | Set to `1` to silence the plain-HTTP / insecure-gRPC WARNINGs emitted by `tls.py` when TLS env vars are unset. |
| Worker / Transport | `ACHERON_WORKER__WORKER_ID` | (required) | Stable identifier for this worker instance. |
| Worker / Transport | `ACHERON_WORKER__ORCHESTRATOR_URL` | (required) | Orchestrator URL the worker registers with and sends `/execute` to. |
| Worker / Transport | `ACHERON_WORKER__LISTEN_HOST` | `0.0.0.0` | Bind host for the worker's HTTP/gRPC server. |
| Worker / Transport | `ACHERON_WORKER__LISTEN_PORT` | `8001` | Bind port for the worker's HTTP/gRPC server. |
| Worker / Transport | `ACHERON_WORKER__EXECUTION_TIMEOUT_S` | `1800` | Per-step execution timeout. |
| Worker / Transport | `ACHERON_WORKER__OUTPUT_MODE` | `multipart` | `multipart` (stream bytes over HTTP) or `volume` (write to shared volume). |
| Worker / Transport | `ACHERON_WORKER__OUTPUT_VOLUME_DIR` | (unset) | Required when `output_mode == "volume"`. |
| Worker / Dispatch | `ACHERON_WORKER__HANDLER` | (unset) | Python import path to the worker handler class (used by `acheron-worker-edge` generic CLI). |
| Worker / Dispatch | `ACHERON_WORKER__MODEL_ID` | (unset) | Override the model id the handler loads (e.g., `Qwen/Qwen3-TTS-12Hz-1.7B-CustomVoice`). |
| Worker / Dispatch | `ACHERON_WORKER__PHANTOM_HANDLER` | (unset) | Edge-only: cloud-side handler class used solely to read static `capabilities()` (no model load). |
| Worker / Dispatch | `ACHERON_WORKER__LOG_LEVEL` | `INFO` | Standard logging level. |
| Worker / Secrets (env-only) | `ACHERON_WORKER__REGISTRATION_TOKEN` | (unset) | Bearer token for `Authorization` header on registration. Env-only — rejected when supplied via YAML or constructor. |
| Worker / Secrets (env-only) | `ACHERON_WORKER__RUNPOD_API_KEY` | (unset) | RunPod account API key for the GraphQL pricing endpoint. Env-only. |
| Worker / Secrets (env-only) | `ACHERON_WORKER__RUNPOD_ENDPOINT_ID` | (unset) | RunPod serverless endpoint id to forward `/execute` to. Env-only. |
| Worker / Pricing | `ACHERON_WORKER__PRICE_SOURCE` | `runpod` | `runpod` (auto-discover from RunPod GraphQL), `static` (fixed `DOLLARS_PER_HOUR`), or `zero` (stubs/local). |
| Worker / Pricing | `ACHERON_WORKER__SECURE_CLOUD` | `false` | When `price_source == "runpod"`, quote Secure Cloud (true) or Community Cloud (false) rates. |
| Worker / Pricing | `ACHERON_WORKER__DOLLARS_PER_HOUR` | (unset) | Required when `price_source == "static"`. |
| Worker / Pricing | `ACHERON_WORKER__PRICE_CACHE_TTL_S` | `3600` | RunPod rate cache TTL. Refreshed on demand when stale. |
| Worker / Cloud transport | `ACHERON_WORKER__RUNPOD_BASE_URL` | (unset) | Override the RunPod API base URL (e.g., for testing). |
| Worker / TTS | `ACHERON_WORKER__DEFAULT_SPEAKER` | `Ryan` | Default Qwen3-TTS speaker. Per-language defaults via `per_language_defaults` (set in YAML). |
| Worker / TTS | `ACHERON_WORKER__PER_LANGUAGE_DEFAULTS` | (unset) | JSON dict of language → speaker overrides. Set in `worker.yaml` rather than env. |

---

## 3. Verification Plan

1. **Ruff / Markdown lint.** Ensure the final file passes standard markdown formatting and doesn't trip repository linters.
2. **Link check.** Verify every in-repo file path, section header, and external link is valid and reachable.
3. **Execution check.** Verify that every command, env var, port, profile, and config key mentioned in the README exists in the codebase (cross-checked against `docker-compose.yml`, `acheron.yaml.example`, `Justfile`, `src/acheron/worker_sdk/settings.py`, `src/acheron/shell/config.py`, `src/acheron/tls.py`).
4. **Spec-to-code cross-check.** Re-verify the quantitative claims (queue size 4, 64 KiB streaming, 250-char chunking default, 3600s price cache, 1800s step timeout, compose profile names, `batch_capable` per worker) against the source after the spec is written, so the README is not shipped with stale numbers.
