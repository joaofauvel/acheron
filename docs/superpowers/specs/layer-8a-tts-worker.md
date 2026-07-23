# Layer 8a — TTS Worker (RunPod Serverless Blueprint) Design

First of three Layer 8 sub-projects. Establishes the worker **blueprint** (`acheron.worker_sdk`) and ships the `qwen3tts` worker as the first concrete implementation. ASR (`8b`) and translation (`8c`) reuse the blueprint in their own sub-project cycles.

## Scope

**In scope:**
- A new `acheron.worker_sdk` subpackage inside the existing `acheron` wheel: the `WorkerHandler` ABC, composable `Artifact` outputs, RunPod edge runtime, RunPod price discovery, registration client, FastAPI factory, and the `acheron-worker-edge` image entrypoint.
- One shipped worker: `workers/qwen3tts/` (RunPod serverless runtime image only).
- Orchestrator-side changes: `HttpWorker` multipart/mixed parsing, `GrpcWorker` `Artifact` mode, shared `_materialize_artifact` / `_build_result` helpers, `JobMetrics.cost_basis`, dashboard cost-confidence rendering.
- Worker config discovery (`WORKER_CONFIG` → `<worker_name>.worker.yaml` → `worker.yaml` → env-only).
- Edge container as the RunPod bridge (published `acheron-worker-edge` image, runs alongside the orchestrator in the main `docker-compose.yml`, configured via `.env`).
- Stub matrix under `stubs/` covering the SDK across HTTP/gRPC × local/runpod × volume/multipart.
- GHCR CI workflow publishing `acheron-qwen3tts-runpod` images on tag and `main`.

**Out of scope (deferred to separate sub-projects):**
- 8b — `whisperv3large` ASR worker.
- 8c — `translategemma` translation worker (supersedes an earlier stub spec that predates the blueprint; see [Layer 8c spec](./layer-8c-translategemma-worker.md)).
- `Qwen3-TTS-12Hz-1.7B-Base` voice cloning (future sub-project after 8a/8b/8c).
- Local-GPU edge mode (`acheron-worker-sdk local-edge` CLI subcommand and a `Qwen3TTSLocalHandler`). Workers commit to one deployment mode by being one mode; v1 ships RunPod serverless only.
- Per-chunk `instruct` metadata in the plan payload (handler supports it; planner doesn't emit it yet).
- Multi-speaker per book; voice generation. v1 uses one speaker per job, configured on the worker.
- Per-chapter parallelism and `workers_max > 1` endpoint scaling.
- **Worker selection on job submission.** The `asr_model` field on `AudioRequest` / `SubmitJobRequest` is wired into the transcribe step's payload today, but `step_handler._language_matches` selects workers purely by `WorkerType` + language pair (`first-registered-wins`); the field is effectively a no-op. There is no `--tts` / `--translation` analog, and `acheron job submit ... --asr <id>` is **not** exposed by the CLI today despite being listed under "Implemented Commands" in the design spec (corrected by this sub-project). Real per-step worker targeting (e.g. `asr_model`, `tts_model`, `translation_model` hints on the plan request, validated by the planner against the registry) is cross-cutting surgery across planner + step_handler + schemas + CLI and is deferred to a separate sub-project. The deployer selects which workers are active via `docker-compose.yml` service entries; with one RunPod worker per `WorkerType` per deployment, language-match selection suffices.

## Repository Layout

Single `acheron` wheel. The SDK is a new subpackage that imports from `acheron.core` only. Workers are top-level packages outside the `acheron` import tree.

```
src/
  acheron/
    core/                # existing pure types — extended with CostBasis
      models.py
      interfaces.py
      errors.py
      ...
    shell/               # existing orchestrator I/O — extended
      transports/
        http.py           # multipart/mixed support
        grpc.py           # Artifact mode
        _multipart.py     # NEW: shared materializer + _build_result
      ...
      dashboard/          # cost-confidence rendering
    proto/
    worker_sdk/           # NEW: the blueprint
      __init__.py
      handler.py          # WorkerHandler ABC + lifecycle hooks
      artifacts.py        # Artifact Protocol + BytesArtifact / StreamArtifact / FileArtifact
      app.py              # create_worker_app(handler, settings) -> FastAPI
      cli.py              # `acheron-worker-edge` image entrypoint module
      registration.py     # register_with_orchestrator()
      schemas.py          # pydantic request/response for /execute
      settings.py         # WorkerSettings (yaml + env)
      cloud.py            # make_runpod_handler(adapter) for cloud-side serverless entry
      pricing.py          # PriceSource protocol + RunPodPrice / StaticPrice / ZeroPrice
      config_loader.py    # YAML discovery + env-override
      _edge_http.py       # internal FastAPI app served by the entrypoint
      _runpod_client.py   # internal: wraps runpod.Endpoint(id).run + poll + timeout
  workers/                # NEW top-level dir (not in the acheron import tree)
    qwen3tts/
      handler.py          # Qwen3TTSRunpodHandler (the only class shipped here)
      runpod_entrypoint.py
      worker.yaml
      Dockerfile.runpod
      pyproject.toml       # workspace member; deps: acheron, qwen-tts, torch, soundfile
      README.md
      tests/
stubs/                     # SDK test scaffolding (replaces existing 4 stubs)
  tts_local_stub/
  tts_volume_stub/
  tts_runpod_stub/         # uses a mock RunPod HTTP server in-process
  tts_grpc_stub/           # exercises GrpcWorker Artifact mode
  asr_local_stub/
  translation_local_stub/
  translation_runpod_stub/
.github/
  workflows/
    build-workers.yml      # NEW: publish workers/qwen3tts to GHCR
```

**Import boundaries** (new import-linter contracts):
- `acheron.worker_sdk -> acheron.core` (allowed)
- `acheron.worker_sdk -/-> acheron.shell` (forbidden)
- `workers.* -> acheron.worker_sdk, acheron.core` (allowed)
- `workers.* -/-> acheron.shell` (forbidden)

Workers `pip install acheron` for the SDK + `core` types. The `acheron.shell` subpackage is present on disk but never imported by workers; import-linter fails the build if a worker tries.

## Deployment Topology

RunPod Serverless endpoints don't expose the FastAPI `/health` / `/capabilities` / `/execute` shape the orchestrator's `HttpWorker` calls. They speak RunPod's `/run` + `/status` + `/cancel` job protocol, and they scale to zero — they only boot when RunPod schedules a job. So a thin always-on **edge container** bridges the orchestrator's HTTP-worker protocol and RunPod's serverless job API.

| Image | Where it runs | Contains | Published by |
|---|---|---|---|
| `acheron-qwen3tts-runpod` | inside the RunPod serverless endpoint (cloud) | model + `Qwen3TTSRunpodHandler` + `runpod.serverless.start(...)` | GHCR by CI |
| `acheron-worker-edge` | alongside the orchestrator (compose service) | FastAPI app + RunPod forwarder + registration client; no GPU | GHCR by CI |

The edge container is **generic across all workers** — same image for TTS/ASR/translation, only `worker.yaml` + env differ per service. The recoverer's deploy surface is `docker-compose.yml` (service entry present in the main compose) + `.env` (RunPod endpoint ID, API key, registration token). The user does not clone the repo or build anything. See [Deployment Flow](#deployment-flow) for the step-by-step.

## `acheron.worker_sdk` API Surface

### `handler.WorkerHandler`

```python
class WorkerHandler(ABC):
    @abstractmethod
    def capabilities(self) -> WorkerCapabilities: ...

    @abstractmethod
    async def handle(self, job: Job) -> list[Artifact]: ...

    async def startup(self) -> None: ...    # default no-op
    async def shutdown(self) -> None: ...   # default no-op
```

- `capabilities()` is sync — returns a static description built from `worker.yaml` + model metadata. No I/O.
- `handle()` is async; workers wrap sync inference in `asyncio.to_thread`.
- `startup()` / `shutdown()` for model load / GPU teardown. Called by the SDK lifespan / cloud entrypoint.

### `artifacts.Artifact` — composable outputs

A Protocol with three concrete variants; the multipart encoder / volume writer treat them uniformly.

```python
class Artifact(Protocol):
    @property
    def filename(self) -> str: ...
    @property
    def content_type(self) -> str: ...
    @property
    def metadata(self) -> dict[str, JsonValue]: ...
    async def stream(self) -> AsyncIterator[bytes]: ...

@dataclass(frozen=True)
class BytesArtifact:    # in-memory bytes — chapter WAV, short text
    filename: str; content_type: str; data: bytes
    metadata: dict[str, JsonValue] = field(default_factory=dict)
    async def stream(self) -> AsyncIterator[bytes]: yield self.data

@dataclass(frozen=True)
class StreamArtifact:   # lazily-produced chunks — long audio, batched generation
    filename: str; content_type: str
    producer: Callable[[], AsyncIterator[bytes]]
    metadata: dict[str, JsonValue] = field(default_factory=dict)
    async def stream(self) -> AsyncIterator[bytes]:
        async for chunk in self.producer(): yield chunk

@dataclass(frozen=True)
class FileArtifact:     # worker wrote to disk (shared-volume mode, or a tmp file)
    filename: str; content_type: str; path: Path
    metadata: dict[str, JsonValue] = field(default_factory=dict)
    async def stream(self) -> AsyncIterator[bytes]:
        async with aiofiles.open(self.path, "rb") as f:
            while chunk := await f.read(64 * 1024): yield chunk
```

The Protocol declares `filename` / `content_type` / `metadata` as `@property` accessors (not plain attributes). This is intentional: the concrete implementations are `@dataclass(frozen=True)`, so their fields are read-only at instance level. A Protocol that declared plain attributes would be incompatible with frozen dataclasses under strict type-checkers (e.g. basedpyright reports `filename is writable in protocol, read-only in dataclass`). The property-based declaration matches the runtime read-only semantics of the frozen dataclasses.

Workers compose the variant their model's API naturally produces — no forced buffering. The SDK composes one multipart body (or one gRPC `repeated Artifact`) from any mix of variants.

### `app.create_worker_app` and `cli`

`create_worker_app(handler, settings) -> FastAPI` exposes `/health`, `/capabilities`, `/execute` driven by the handler. `/execute` flow: validate `Job`, `await handler.handle(job)`, emit multipart/mixed response (success) or a `JobResult`-shaped JSON body with `status=FAILED` and `error=<message>` (handler exception). The error body must be parseable by the orchestrator's `TypeAdapter(JobResult).validate_json` (Plan 2's `HttpWorker._parse_execute_response`); opaque 5xx with `{"status":"failed", "error": ...}` is forbidden. It registers with the orchestrator on lifespan startup via `register_with_orchestrator`.

The trailing metrics JSON part of the multipart response is built via `JobMetrics.model_dump_json()` (pydantic `TypeAdapter`-driven) so `None` fields — including `cost_basis` when no price source is wired — round-trip as JSON `null` rather than the string `"unknown"`. The `"unknown"` value is reserved for the **CostBasis.UNKNOWN** wire value (RunPod API was down, tried and failed); `null` means "no estimate at all".

`acheron.worker_sdk.cli` is the entrypoint module of the published `acheron-worker-edge` Docker image — it is the image's `CMD`, not a user-facing CLI. The deployer never invokes it directly; they configure the edge container via `.env` + `docker-compose.yml` (see "Deployment Flow"). It exists as a module so the same image serves TTS/ASR/translation edge containers — only the `WORKER_NAME` + `worker.yaml` + env differ per service.

v1 ships one mode:
- `acheron-worker-edge` (image `CMD`) — starts an HTTP edge container that registers with the orchestrator and forwards `/execute` to a RunPod serverless endpoint via `runpod.Endpoint(id).run(...)`. The worker's model lives in the cloud-side serverless runtime image; the edge container is GPU-less and only forwards.

A `local-edge` mode (edge container runs the handler in-process on a local GPU) is deferred to a separate future worker package.

### `cloud.make_runpod_handler`

```python
def make_runpod_handler(handler: WorkerHandler) -> Callable[[dict], dict]:
    async def _rp_handler(runpod_job: dict) -> dict:
        job = _deserialise_job(runpod_job["input"])
        artifacts = await handler.handle(job)
        return {"artifacts": [a.to_dict() for a in artifacts]}
    return _rp_handler
```

Used by `workers/qwen3tts/runpod_entrypoint.py` to load the model once at boot and route RunPod jobs through `runpod.serverless.start({"handler": adapter})`. The cloud-side handler has the same `handle()` contract as the edge-side; the SDK does not branch on deployment mode.

### `registration.register_with_orchestrator`

```python
async def register_with_orchestrator(
    client: httpx.AsyncClient, orchestrator_url: str, token: str,
    worker_id: str, endpoint: str, transport: str,
    capabilities: WorkerCapabilities,
    retries: int = 30, retry_delay: float = 2.0,
) -> None: ...
```

Retries with exponential backoff. Posts the existing `WorkerRegistrationRequest` schema — no orchestrator-side schema change. The SDK adds `metadata["health_provider"] = "runpod"` and `metadata["health_endpoint_id"] = settings.runpod_endpoint_id` so the existing `RunPodHealthProvider` cold-start detection (Layer 11) picks the worker up unchanged.

## Worker Settings & Config Discovery

`WorkerSettings` (pydantic `BaseSettings`) is populated from two sources merged in priority order. **Env vars win.** Secrets (`runpod_api_key`, `registration_token`) are rejected if present in YAML — fail-loud to keep them out of image layers.

**Discovery order** (first match wins):
1. `WORKER_CONFIG` env var → explicit path (absolute or relative).
2. `<cwd>/<worker_name>.worker.yaml` — `worker_name` comes from `WORKER_NAME` env var or the directory name.
3. `<cwd>/worker.yaml`.
4. Env vars only.

The `<worker_name>.worker.yaml` pattern supports co-located multi-worker deployments: a deployer mounts a directory of `{qwen3tts,whisperv3large,translategemma}.worker.yaml` files read-only and starts each container with `WORKER_NAME`. For remote (RunPod serverless) workers, config is purely env-driven — the runtime image has no mounted volume, env comes from the RunPod template.

The committed `workers/qwen3tts/worker.yaml` is the **image default**. A deployer can override individual fields by mounting a small `qwen3tts.worker.yaml` override without rebuilding the image.

### Settings shape

```python
class WorkerSettings(BaseSettings):
    # env_prefix = "ACHERON_WORKER__" — env vars are ACHERON_WORKER__<FIELD_UPPER>,
    # e.g. ACHERON_WORKER__RUNPOD_API_KEY. The double underscore after the prefix
    # matches the project's ACHERON_<SECTION>__<FIELD> convention
    # (see acheron.yaml.example:10). Avoids collision with the orchestrator's
    # own ACHERON_REGISTRATION_TOKEN / ACHERON_DATA_DIR env namespace.
    worker_id: str
    orchestrator_url: str
    registration_token: str | None = None       # env-only
    listen_host: str = "0.0.0.0"
    listen_port: int = 8001

    # RunPod edge backend
    runpod_api_key: str | None = None           # env-only
    runpod_endpoint_id: str | None = None      # env-only
    execution_timeout_s: float = 1800.0

    # Output transport — multipart default; volume opt-in for co-located edge
    output_mode: Literal["multipart", "volume"] = "multipart"
    output_volume_dir: str | None = None        # required iff output_mode == "volume"

    # Pricing
    price_source: Literal["runpod", "static", "zero"] = "runpod"
    # The GPU type is NOT a config field. RunPod is the single source of truth:
    # RunPodPrice queries the endpoint by runpod_endpoint_id to read its gpuIds,
    # then resolves uninterruptablePrice. Changing the GPU on the RunPod endpoint
    # (via `runpodctl serverless update <id> --gpu-id <new>` or the dashboard)
    # takes effect on the worker's next price refresh (within price_cache_ttl_s).
    # No image rebuild required.
    secure_cloud: bool = False                   # secure-cloud vs community-cloud rate quote
    dollars_per_hour: float | None = None        # required iff price_source == "static"
    price_cache_ttl_s: float = 3600.0

    # Voice (speaker) selection — see "Voice Config" below
    default_speaker: str = "Ryan"
    per_language_defaults: dict[str, str] = field(default_factory=dict)

    # Handler module — the runpod-edge CLI imports this
    handler: str = ""                            # e.g. "workers.qwen3tts.handler:Qwen3TTSRunpodHandler"
    model_id: str | None = None
```

## Voice Config (single speaker per job, v1)

The plan-level payload does not carry a speaker — the worker provides the default. This keeps the orchestrator's `Job.payload` minimal and lets each deployment pick its preferred voice.

```yaml
# workers/qwen3tts/worker.yaml (excerpt)
default_speaker: "Ryan"            # English-native speaker; overridden below per-language
per_language_defaults:
  en: "Ryan"           # English-native
  zh: "Vivian"         # Chinese-native
  ja: "Ono_Anna"
  ko: "Sohee"
  # other supported languages fall back to default_speaker
```

Resolution order in the handler:
1. `job.payload["speaker"]` if explicitly set (reserved for future multi-speaker work; not emitted by the planner today).
2. `settings.per_language_defaults.get(target_language, settings.default_speaker)`.

The SDK validates the chosen speaker against `handler.capabilities().metadata["speakers"]` at startup; unknown speaker in config raises `WorkerError` at boot (fail-fast). The 9 CustomVoice speakers are documented in the worker's `README.md`.

**v1 is single-speaker per job.** The same speaker is used for every chunk in the batch (consistent voice across the chapter). Multi-speaker per book, voice design via `Qwen3-TTS-12Hz-1.7B-VoiceDesign`, and voice cloning via `Qwen3-TTS-12Hz-1.7B-Base` are deferred to separate future sub-projects.

## Pricing & Cost Basis

`PriceSource` protocol with three composable variants. **`RunPodPrice` is the default.** Pricing is best-effort: never blocks a job, never silently conflates "unavailable" with "free."

### `PriceEstimate` and `CostBasis`

```python
@dataclass(frozen=True)
class PriceEstimate:
    cost: float | None
    reason: str | None = None

class PriceSource(Protocol):
    async def estimate(self, gpu_seconds: float) -> PriceEstimate: ...

class ZeroPrice:
    async def estimate(self, gpu_seconds: float) -> PriceEstimate:
        return PriceEstimate(cost=0.0, reason="zero (stub/local)")

@dataclass(frozen=True)
class StaticPrice:
    dollars_per_hour: float
    async def estimate(self, gpu_seconds: float) -> PriceEstimate: ...

@dataclass(frozen=True)
class RunPodPrice:
    api_key: str
    endpoint_id: str
    secure_cloud: bool = True
    cache_ttl_s: float = 3600.0
    # internal _rate / _rate_fetched_at cache; refresh is fault-tolerant
```

`RunPodPrice` queries `POST https://api.runpod.io/graphql?api_key=<KEY>`. The GPU type is **discovered from the endpoint**, not configured on the worker — RunPod is the single source of truth. `_refresh_rate()` makes two GraphQL calls (cached together for `cache_ttl_s`):

```graphql
# 1. Read the endpoint's configured GPU
query($endpoint_id: String!) {
  myself {
    endpoints { id gpuIds }     # client-side filter on id == endpoint_id
  }
}

# 2. Resolve the uninterruptable price for that GPU
query($gpu_id: String!, $secure: Boolean!) {
  gpuTypes(input: {id: $gpu_id}) {
    lowestPrice(input: {gpuCount: 1, secureCloud: $secure}) { uninterruptablePrice }
  }
}
```

**Deployer GPU change workflow (no image rebuild):**
1. Change the GPU on the existing RunPod endpoint via `runpodctl serverless update <endpoint-id> --gpu-id <new>` or the RunPod dashboard.
2. Restart the edge container (or wait `price_cache_ttl_s`) — the worker re-queries the endpoint and picks up the new GPU + new price automatically.

`estimate()`:
- `_rate` cached for `cache_ttl_s`; far past TTL → try async refresh.
- Refresh failure (either GraphQL call) does not raise. If `_rate` unset → `PriceEstimate(cost=None, reason="runpod pricing unavailable for endpoint <endpoint_id>")`.
- If `_rate` set → `PriceEstimate(cost=round(gpu_seconds * _rate / 3600.0, 6))`.

`Backend` translates `PriceEstimate.reason` into a `CostBasis` (new enum on `JobMetrics`):

```python
# src/acheron/core/models.py (extended)
class CostBasis(Enum):
    MEASURED = "measured"   # fresh uninterruptablePrice multiplied by actual gpu_seconds
    STATIC   = "static"    # configured dollars_per_hour
    CACHED   = "cached"    # serving last-known rate; API currently unavailable
    UNKNOWN  = "unknown"   # never refreshed or cache expired and refresh failed — cost is None

@dataclass(frozen=True)
class JobMetrics:
    duration_seconds: float
    gpu_seconds: float | None = None
    tokens_in: int | None = None
    tokens_out: int | None = None
    cost_estimate: float | None = None
    cost_basis: CostBasis | None = None     # NEW
```

`cost_basis` is optional and defaults to `None` — backward-compatible with existing JSON stubs that omit it.

### Mapping `PriceEstimate.reason → CostBasis`

- Fresh refresh succeeded this call → `MEASURED`.
- Cache hit (rate set, refresh not attempted) → `MEASURED` on first refresh + `CACHED` for subsequent TTL-window calls within the same process. The dashboard treats both as "real numbers" with subtle coloring.
- `_rate is None` after failed refresh → `UNKNOWN`.
- `StaticPrice` always → `STATIC`.
- `ZeroPrice` → `STATIC` (operator opted out; cost is `$0.00` with `STATIC` basis).

### Worker behavior on unknown price

- `PriceEstimate.cost is None` → `JobResult.metrics.cost_estimate is None`. The `Backend` does not raise.
- `PlanResult.total_cost` skips `None` per-step costs; aggregates `total_cost_basis` as the least-confident basis across all steps (`MEASURED + UNKNOWN = UNKNOWN`).
- Dashboard renders unknown ≠ free (see "Dashboard" below).

### Startup fault tolerance

`acheron-worker-edge` image entrypoint lifespan:
1. Build the `WorkerHandler` from the configured `handler` import path (the handler module is the cloud-side image bundled in the RunPod serverless endpoint — not the edge's responsibility, but the edge imports the module to call `capabilities()`).
2. If `price_source == "runpod"`: `await runpod_price._refresh_rate(client)`. Failure logs a warning, sets `_rate=None`. **Does not block startup.** Worker proceeds to register; subsequent `estimate()` calls retry on their own schedule.
3. `await register_with_orchestrator(...)`.

## Orchestrator-Side Changes

### `HttpWorker.execute()` — content-type-driven dispatch

```python
async def execute(self, job: Job) -> JobResult:
    resp = await self._client.post(f"{base_url}/execute", json=_job_to_dict(job))
    resp.raise_for_status()
    ctype = resp.headers.get("content-type", "")
    if ctype.startswith("multipart/mixed"):
        outputs, metrics = _parse_multipart_execute(resp)
    else:
        # Legacy JSON path — backward-compatible with HTTP stubs that emit OutputFile.path.
        return TypeAdapter(JobResult).validate_json(resp.content)
    return _build_result(job.job_id, outputs, metrics)
```

**`_parse_multipart_execute(resp)`** — iterates multipart/mixed parts. Each part becomes an `OutputArtifact` written to the orchestrator's `ACHERON_DATA_DIR/{plan_id}/{step_id}/{filename}` via `aiofiles` + `hashlib.sha256()` accumulation. The trailing JSON part carries `metrics` (including `cost_basis`). Calls the shared `_materialize_artifact` helper.

**No shared-volume assumption.** The orchestrator materializes bytes it received into its own `/data/jobs/...`. `StepCache` keeps working unchanged — these are just `OutputFile`s on the orchestrator's volume.

**Legacy JSON path stays.** Existing HTTP stubs continue to register and respond. Incremental migration: replace stubs at deployer's pace.

### `GrpcWorker` — `Artifact` mode

`proto/synthesis.proto` extended (additive, backward-compatible):

```proto
message OutputChunk {
  oneof payload {
    bytes pcm_data = 1;            // legacy: raw PCM stream (live-streaming use case — preserved)
    Artifact artifact = 2;         // new: structured artifact
  }
}

message Artifact {
  string filename = 1;
  string content_type = 2;
  bytes data = 3;
  map<string, string> metadata = 4;
}

message ExecuteResponse {
  repeated Artifact artifacts = 1;
  Metrics metrics = 2;
  string error = 3;
}
```

`GrpcWorker.execute()` consumes `Artifact` parts via the same `_materialize_artifact` / `_build_result` shared helpers. The legacy `pcm_data` mode is preserved — a future sub-project could wire it to a low-latency live-streaming TTS variant. The v1 Qwen3-TTS handler returns `BytesArtifact` (whole chapter WAV per chunk) over HTTP, and HTTP is the v1 transport; the gRPC `Artifact` mode is shipped so ASR / translation can pick gRPC later without re-doing the contract.

### Shared helpers — `shell/transports/_multipart.py` (NEW)

Two utilities used by both HTTP and gRPC transports:
- `_materialize_artifact(bytes, filename, content_type, metadata, dest_dir) -> OutputFile` — writes bytes to disk, computes size + SHA-256, returns an `OutputFile`.
- `_build_result(job_id, outputs, metrics) -> JobResult` — assembles a `JobResult` from a list of `OutputFile`s + a `JobMetrics` (with `cost_basis`).

Standalone-tested in `tests/shell/transports/test_multipart.py`.

### Cold-start detection — unchanged

The existing `RunPodHealthProvider` (Layer 11) and `HealthMonitor._handle_failure` consume `metadata["health_provider"]` + `metadata["health_endpoint_id"]`. The SDK's `register_with_orchestrator` tags RunPod workers' capabilities with these; cold-start detection works out of the box. No orchestrator code change.

### `worker_id` + `transport` — no schema change

The qwen3tts edge registers `transport: "http"`. The orchestrator's `step_handler.default_worker_factory` falls through its `match` to `HttpWorker(registered.endpoint)`. **No new transport case, no enum to extend.** Future gRPC workers register `transport: "grpc"`; the factory picks `GrpcWorker`. Unchanged.

### Cost aggregation — no orchestrator change

`PlanResult.total_cost` already sums `JobMetrics.cost_estimate` per step. `total_cost_basis` (new) is the least-confident basis across steps. Existing behavior preserved for steps with `cost_estimate=None`.

## Dashboard Updates

`GET /partials/cost` (existing partial) gains two new columns per job:

| Job ID | Status | Cost | Duration | Steps | **Cost Basis** | **Note** |
|---|---|---|---|---|---|---|
| job-xyz | completed | $0.42 | 102s | 5/5 | **Measured** | — |
| job-abc | completed | $0.31 | 88s | 5/5 | **Cached** | RunPod pricing API unavailable; serving last-known rate |
| job-def | completed | — | 75s | 4/4 | **Unknown** | RunPod pricing API unavailable |
| job-stub | completed | $0.00 | 12s | 5/5 | **Static** | — |

- Badge color: `MEASURED` green, `CACHED` amber, `UNKNOWN` gray with `—` in the cost cell, `STATIC` neutral.
- The job's overall `cost_basis` is the least-confident basis across its steps.
- Note column: short, plain English — populated from a small map keyed on `CostBasis` in the Jinja template (orchestrator-side), no per-step prose carried over the API.

## Stubs — SDK Test Matrix

Replaces the 4 existing stubs with a single matrix exercising the SDK across deployment shapes. Each stub `pip install`s `acheron` and provides a minimal `WorkerHandler` that generates deterministic fake outputs — no GPU, no model download.

| Stub | Handler | output_mode | transport | price | Purpose |
|---|---|---|---|---|---|
| `stubs/tts_local_stub/` | `StubTTSHandler` | `multipart` | http | `zero` | HTTP edge worker, *no* shared volume; multi-tenant shape. |
| `stubs/tts_volume_stub/` | `StubTTSHandler` | `volume` | http | `zero` | HTTP edge worker co-located, shared-volume mode — validates the legacy JSON-`path` frac path stays for stubs. |
| `stubs/tts_runpod_stub/` | `StubTTSHandler` | `multipart` | http | `static` ($0.69/hr) | Edge worker forwards to a mocked RunPod HTTP server in-process. Validates RunPod loop + cost return + cold-start metadata. |
| `stubs/tts_grpc_stub/` | `StubTTSHandler` | multipart (gRPC variant) | grpc | `zero` | gRPC variant returning `Artifact` parts. Tests `GrpcWorker` new consumer + parsers. |
| `stubs/asr_local_stub/` | `StubASRHandler` | `multipart` | http | `zero` | ASR shape (returns `BytesArtifact` of text). Validates ASR extension via the same SDK. |
| `stubs/translation_local_stub/` | `StubTranslationHandler` | `multipart` | http | `zero` | Translation shape. Proves the SDK is model-agnostic. |
| `stubs/translation_runpod_stub/` | `StubTranslationHandler` | `multipart` | http | `static` | RunPod-mock variant of the translation stub. Validates cost aggregation across two RunPod endpoints. |

Each stub directory has a `main.py` that calls `create_worker_app(handler, settings)` and `uvicorn.run`. **No per-stub FastAPI boilerplate** — the SDK's `app.py` handles `/health`, `/capabilities`, `/execute`, and registration. The stubs test the SDK's own routes.

For the RunPod-mock variants, `main.py` also starts a second tiny HTTP server on another port that speaks a minimal `/run` + status-polling protocol. The internal RunPod client (`acheron.worker_sdk._runpod_client`) is given this server's URL via a `RUNPOD_BASE_URL` test hook on the settings. This exercises the full submit → poll → collect loop without hitting RunPod's real API.

Docker setup:
- `Dockerfile` gains a single `worker-stub-base` stage + per-stub `CMD` overrides in compose.
- `docker-compose.yml` keeps separate service entries per stub (per-service healthcheck).
- Existing `tts-stub` / `asr-stub` / `translation-stub` / `grpc-stub` services ported onto the SDK scaffold.

## The Qwen3-TTS RunPod Worker

`workers/qwen3tts/` ships exactly one image: the RunPod serverless runtime containing `Qwen3TTSRunpodHandler` + model deps. CI publishes the image to GHCR. The deployer runs the generic `acheron-worker-sdk-edge` image alongside (under `stubs/` or a separately-documented path), configured via `worker.yaml` + env, to forward `/execute` calls to RunPod.

### Model

**`Qwen/Qwen3-TTS-12Hz-1.7B-CustomVoice`** (released January 2026).

- CustomVoice variant: 9 built-in premium speakers across gender/age/language/dialect (`Vivian`, `Serena`, `Uncle_Fu`, `Dylan`, `Eric`, `Ryan`, `Aiden`, `Ono_Anna`, `Sohee`).
- 10 supported languages: Chinese, English, Japanese, Korean, German, French, Russian, Portuguese, Spanish, Italian.
- Single-inference and batch inference: `model.generate_custom_voice(text=str|list, language=str|list, speaker=str|list, instruct=str|list)`.
- Streaming capable (Dual-Track, ~97ms first-packet); v1 uses non-streaming batch inference.
- Bfloat16 + FlashAttention 2; ~1.7B params; fits in 24GB comfortably.
- Inference via the official `qwen-tts` PyPI package: `from qwen_tts import Qwen3TTSModel`.

`Qwen3-TTS-12Hz-1.7B-Base` (voice cloning via 3-second ref audio) is the natural future sub-project; its API surface is documented at <https://github.com/QwenLM/Qwen3-TTS> but not used here.

### `handler.py`

```python
import asyncio, io, json
from acheron.core.models import WorkerType, WorkerCapabilities, Job
from acheron.worker_sdk import WorkerHandler, BytesArtifact
from acheron.worker_sdk.inputs import Input
from acheron.core.errors import WorkerError
from qwen_tts import Qwen3TTSModel
import torch, soundfile as sf

# ISO 639-1 (Acheron contract) → Qwen3-TTS language names
_LANG_MAP = {
    "en": "English", "zh": "Chinese", "ja": "Japanese", "ko": "Korean",
    "de": "German", "fr": "French", "ru": "Russian",
    "pt": "Portuguese", "es": "Spanish", "it": "Italian",
}
_ALL_SPEAKERS = frozenset({
    "Vivian", "Serena", "Uncle_Fu", "Dylan", "Eric",
    "Ryan", "Aiden", "Ono_Anna", "Sohee",
})
_MODEL_ID = "Qwen/Qwen3-TTS-12Hz-1.7B-CustomVoice"


class Qwen3TTSRunpodHandler(WorkerHandler):
    """Cloud-side handler run inside the RunPod serverless runtime image.

    Loads the model eagerly at boot (runpod_entrypoint.py calls startup(),
    then runpod.serverless.start(...)). handle() is invoked by the SDK's
    make_runpod_handler adapter on each incoming RunPod job.
    """

    def __init__(self, settings) -> None:
        self._settings = settings
        self._model: Qwen3TTSModel | None = None

    def capabilities(self) -> WorkerCapabilities:
        return WorkerCapabilities(
            worker_type=WorkerType.TTS,
            supported_languages_in=frozenset(_LANG_MAP),
            supported_languages_out=frozenset(_LANG_MAP),
            supported_formats_in=frozenset({"text"}),
            supported_formats_out=frozenset({"wav"}),
            max_payload_bytes=None,
            batch_capable=True,
            max_input_tokens=2048,  # qwen3-tts is 2K context; since 8c
            model_source=f"huggingface:{_MODEL_ID}",
            metadata={
                "speakers": sorted(_ALL_SPEAKERS),
                "default_speaker": self._settings.default_speaker,
            },
        )

    async def startup(self) -> None:
        def _load():
            self._model = Qwen3TTSModel.from_pretrained(
                _MODEL_ID,
                device_map="cuda:0",
                dtype=torch.bfloat16,
                attn_implementation="flash_attention_2",
            )
        await asyncio.to_thread(_load)

    async def shutdown(self) -> None:
        if self._model is not None:
            del self._model
            self._model = None
            torch.cuda.empty_cache()

    async def handle(self, job: Job, input: Input | None = None) -> list[Artifact]:
        """Run batched custom-voice inference for all chunks in the job.

        Chunks arrive via the ``input`` parameter (8b's ``BytesInput`` Protocol):
        JSON-serialised ``chunks.json`` from the upstream chunking step, sent
        as a multipart part. ``input`` is required; chunks in ``job.payload``
        is no longer a supported path. See "Cross-cutting 8c refactor" below.
        """
        if self._model is None:
            raise WorkerError("Qwen3-TTS model not loaded (startup() not run)")
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
        chunks = [c for c in raw_chunks if isinstance(c, dict)]
        if not chunks:
            return []
        target_lang = job.payload["target_language"]
        if target_lang not in _LANG_MAP:
            raise WorkerError(f"Unsupported target language: {target_lang}")
        qwen_lang = _LANG_MAP[target_lang]
        settings = self._settings
        speaker = job.payload.get("speaker") or settings.per_language_defaults.get(
            target_lang, settings.default_speaker
        )
        if speaker not in _ALL_SPEAKERS:
            raise WorkerError(f"Unknown speaker '{speaker}' in worker config")

        texts = [c["text"] for c in chunks]
        languages = [qwen_lang] * len(chunks)
        speakers = [speaker] * len(chunks)
        instructs = [c.get("instruct", "") for c in chunks]

        def _generate():
            return self._model.generate_custom_voice(
                text=texts, language=languages, speaker=speakers, instruct=instructs,
            )
        wavs, sr = await asyncio.to_thread(_generate)

        artifacts: list[BytesArtifact] = []
        for i, (wav, chunk) in enumerate(zip(wavs, chunks)):
            buf = io.BytesIO()
            sf.write(buf, wav, sr, format="WAV")
            seq = chunk.get("sequence_id", i)
            artifacts.append(BytesArtifact(
                filename=f"{chunk['chapter_id']}_{seq:04d}.wav",
                content_type="audio/wav",
                data=buf.getvalue(),
                metadata={"sequence_id": seq, "chapter_id": chunk["chapter_id"], "sample_rate": sr},
            ))
        return artifacts
```

### `runpod_entrypoint.py`

Loads the model once at boot, then routes RunPod jobs through `runpod.serverless.start(...)`.

```python
import asyncio, runpod
from acheron.worker_sdk import WorkerSettings
from acheron.worker_sdk.cloud import make_runpod_handler
from handler import Qwen3TTSRunpodHandler

settings = WorkerSettings.from_yaml("worker.yaml")
handler = Qwen3TTSRunpodHandler(settings)
asyncio.run(handler.startup())   # eager model load — RunPod scales to zero; we want it warm while running
runpod.serverless.start({"handler": make_runpod_handler(handler)})
```

RunPod's framework calls `handler(job)` for each incoming request; `make_runpod_handler` deserializes the input, runs `handler.handle()`, returns `{"artifacts": [...]}`.

### `worker.yaml` (image default)

```yaml
worker_id: "qwen3tts-1"
orchestrator_url: "http://orchestrator:8000"
listen_port: 8001
execution_timeout_s: 1800

# Pricing — RunPod GraphQL API is the default; fault-tolerant fallback to cached/unknown.
# The GPU type is NOT set here — RunPod is the single source of truth. RunPodPrice
# queries the endpoint by runpod_endpoint_id to read its gpuIds, then resolves
# uninterruptablePrice. Changing the GPU on the RunPod endpoint takes effect on
# the worker's next price refresh (within price_cache_ttl_s); no image rebuild.
price_source: runpod
secure_cloud: false                  # quote community-cloud rate (matches the GPU choice)
# price_cache_ttl_s: 3600.0          # sane default

# Single-speaker v1
default_speaker: "Ryan"               # English-native
per_language_defaults:
  en: "Ryan"
  zh: "Vivian"
  ja: "Ono_Anna"
  ko: "Sohee"

# Output transport — multipart default
output_mode: multipart

# Handler — used by the generic runpod-edge CLI (if deployer runs the edge container
# alongside; not used inside the RunPod runtime image itself)
handler: "workers.qwen3tts.handler:Qwen3TTSRunpodHandler"
model_id: "Qwen/Qwen3-TTS-12Hz-1.7B-CustomVoice"
```

Sensitive fields (`registration_token`, `runpod_api_key`, `runpod_endpoint_id`) are env-only — rejected if they appear in YAML.

### Dockerfile.runpod

```dockerfile
FROM python:3.12-slim AS runpod-runtime
ENV PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1 HF_HUB_OFFLINE=1 TRANSFORMERS_OFFLINE=1

RUN apt-get update && apt-get install -y --no-install-recommends \
    libsndfile1 git build-essential \
    && rm -rf /var/lib/apt/lists/*

RUN pip install --no-cache-dir torch==2.5.1 torchaudio==2.5.1 \
    --index-url https://download.pytorch.org/whl/cu121

COPY dist/acheron-*.whl /tmp/
RUN pip install /tmp/acheron-*.whl && rm /tmp/acheron-*.whl

RUN pip install --no-cache-dir qwen-tts soundfile flash-attn==2.5.9.post1 --no-build-isolation
RUN pip install --no-cache-dir runpod

WORKDIR /app
COPY handler.py runpod_entrypoint.py worker.yaml ./
ENV HF_HOME=/runpod-volume/huggingface-cache
CMD ["python", "runpod_entrypoint.py"]
```

**Network volume for cached weights** (avoids re-downloading ~3.4GB on every cold start): RunPod serverless mounts a network volume at `/runpod-volume`. Weights cache lives at `/runpod-volume/huggingface-cache`. `HF_HOME` points there; `HF_HUB_OFFLINE=1` + `TRANSFORMERS_OFFLINE=1` makes transformers prefer cached weights (matches RunPod's documented model-caching pattern).

Deployer pre-warms the volume once via a one-shot pod that runs:
```bash
huggingface-cli download Qwen/Qwen3-TTS-12Hz-1.7B-CustomVoice \
    --local-dir /runpod-volume/huggingface-cache/hub/models--Qwen--Qwen3-TTS-12Hz-1.7B-CustomVoice
```

### Capabilities sent at registration (from the edge)

```json
{
  "worker_id": "qwen3tts-1",
  "endpoint": "http://qwen3tts-edge:8001",
  "transport": "http",
  "capabilities": {
    "worker_type": "tts",
    "supported_languages_in": ["en","zh","ja","ko","de","fr","ru","pt","es","it"],
    "supported_languages_out": ["en","zh","ja","ko","de","fr","ru","pt","es","it"],
    "supported_formats_in": ["text"],
    "supported_formats_out": ["wav"],
    "max_payload_bytes": null,
    "batch_capable": true,
    "max_input_tokens": 2048,
    "model_source": "huggingface:Qwen/Qwen3-TTS-12Hz-1.7B-CustomVoice",
    "metadata": {
      "speakers": ["Aiden","Dylan","Eric","Ono_Anna","Ryan","Serena","Sohee","Uncle_Fu","Vivian"],
      "default_speaker": "Ryan",
      "health_provider": "runpod",
      "health_endpoint_id": "<endpoint id>"
    }
  }
}
```

The existing `RunPodHealthProvider` + `HealthMonitor._handle_failure` consume `health_provider` + `health_endpoint_id` to distinguish booting from offline during cold starts. No orchestrator change.

## Cross-cutting 8c refactor (qwen3tts end-to-end)

> Post-8c addition (2026-06-23). The 8a spec shipped worker-only;
> no orchestrator code path injected the upstream chunking step's
> chunks into the synthesize step's payload, so the
> `job.payload["chunks"]` read in `handle()` was a latent gap. 8c
> closes this gap and refactors the qwen3tts handler to match.

**Changes:**

- `Qwen3TTSRunpodHandler.capabilities()` publishes `max_input_tokens=2048`
  (qwen3-tts is a 2K-context model). The
  `WorkerCapabilities.max_input_tokens` field is new in 8c; see
  [Layer 8c spec](./layer-8c-translategemma-worker.md).
- `handle(self, job, input: Input | None = None)` reads chunks from
  the `Input` parameter (8b's `BytesInput`): JSON-serialised
  `chunks.json` from the upstream chunking step, sent as a multipart
  part. The `job.payload["chunks"]` read is removed (no fallback).
  Malformed `chunks.json` raises `WorkerError`; `input is None`
  raises `WorkerError("Qwen3-TTS requires a chunks.json input (multipart part)")`.
- The orchestrator's `HttpWorker.execute()` gains a `WorkerType.TTS`
  arm that loads the upstream chunking step's `chunks.json` from
  `StepCache` and POSTs it as a multipart part alongside the JSON
  envelope. ASR + TRANSLATION get the same shape.
- `Settings.chars_per_token: int = 4` is a new orchestrator setting
  (8c). The planner's `validate_chunking_fits_workers` (also 8c)
  uses it to check that the chunking step's `max_chunk_length` fits
  each text-input worker's `max_input_tokens`. Misconfigurations fail
  at plan compile time.

**Test fixture changes:** `workers/qwen3tts/tests/test_handler.py` —
`_build_job` no longer takes `chunks`; tests construct a `BytesInput`
with `chunks.json` bytes and pass it as `input`. Existing assertions
preserved.

**Behaviour unchanged:** Empty chunks still return `[]`; speaker /
language / chunk-shape validation unchanged; the 9-speaker custom-voice
model is unchanged.

## Deployment Flow

Documented in `workers/qwen3tts/README.md`. The deployer **never builds the worker image** — CI publishes to GHCR.

1. **Tag a release** (`git tag v1.0.0 && git push origin v1.0.0`). The `build-workers.yml` workflow builds `workers/qwen3tts/Dockerfile.runpod` and publishes:
   - `ghcr.io/<repo>/acheron-qwen3tts-runpod:latest` (movable)
   - `ghcr.io/<repo>/acheron-qwen3tts-runpod:<sha>` (immutable per commit)
   The workflow uses `docker/build-push-action` with `cache-from: type=gha` to cache the slow `pip install torch / qwen-tts / flash-attn` layers.
2. **Create the RunPod serverless template** referencing the pushed image, the GPU type list (`[L4, A5000, RTX 3090]` — 24GB minimum per the GPU choice), the network volume for the HF cache, and env vars:
   - `ACHERON_WORKER__ORCHESTRATOR_URL=http://orchestrator-host:8000` (reachable from inside RunPod)
   - `ACHERON_WORKER__REGISTRATION_TOKEN=<token>` (env-only)
   - `ACHERON_RUNPOD_ENDPOINT_ID` (set after step 3 — RunPod assigns the endpoint ID; redeploy template with it for cold-start metadata to flow correctly).
3. **Create the RunPod serverless endpoint** from the template. Note the endpoint ID. Set `workers_max: 1` for v1 (one replica suffices for a single book), `idle_timeout: 300` (matches the existing cost-containing shard strategy).
4. **Run the edge container** (the orchestrator host's `docker-compose.yml` adds a `qwen3tts-edge` service running the published generic `acheron-worker-edge` image):
   ```yaml
   qwen3tts-edge:
     image: ghcr.io/<repo>/acheron-worker-edge:latest
     environment:
        WORKER_NAME: qwen3tts
        ACHERON_WORKER__ORCHESTRATOR_URL: http://orchestrator:8000
        ACHERON_WORKER__WORKER_HOST: qwen3tts-edge
        ACHERON_WORKER__REGISTRATION_TOKEN: ${ACHERON_REGISTRATION_TOKEN}
        ACHERON_WORKER__RUNPOD_API_KEY: ${RUNPOD_API_KEY}
        ACHERON_WORKER__RUNPOD_ENDPOINT_ID: ${QWEN3TTS_RUNPOD_ENDPOINT_ID}
     volumes:
       - ./deploy-overrides/qwen3tts.worker.yaml:/app/qwen3tts.worker.yaml:ro
   ```
   The edge registers with the orchestrator, forwards `/execute` calls to RunPod's `/run`, streams audio back via multipart.
5. **Cold starts**: when no GPU pods are warm, the orchestrator's `HealthMonitor` reports the worker as `BOOTING` (via the existing `RunPodHealthProvider`), jobs queue at the orchestrator, and RunPod scales from zero as the first `/execute` arrives.

## GHCR CI Workflow

`.github/workflows/build-workers.yml` (new):

```yaml
name: Build and publish worker images
on:
  push:
    branches: [main]
    tags: ['v*']
  pull_request:
    paths:
      - 'workers/**'
      - 'src/acheron/worker_sdk/**'
      - '.github/workflows/build-workers.yml'

jobs:
  build-qwen3tts:
    runs-on: ubuntu-latest
    permissions:
      contents: read
      packages: write
    steps:
      - uses: actions/checkout@v4
      - uses: docker/setup-buildx-action@v3
      - uses: docker/login-action@v3
        with:
          registry: ghcr.io
          username: ${{ github.actor }}
          password: ${{ secrets.GITHUB_TOKEN }}
      - uses: docker/build-push-action@v5
        with:
          context: .
          file: workers/qwen3tts/Dockerfile.runpod
          push: ${{ github.event_name != 'pull_request' }}
          tags: |
            ghcr.io/${{ github.repository }}/acheron-qwen3tts-runpod:latest
            ghcr.io/${{ github.repository }}/acheron-qwen3tts-runpod:${{ github.sha }}
          cache-from: type=gha
          cache-to: type=gha,mode=max
```

- **Single job per worker.** ASR/translation workflows mirror this in their respective sub-projects. Matrix can grow; v1 publishes `acheron-qwen3tts-runpod` only.
- **Pin to `:<sha>`** in the RunPod template for reproducibility; bump to `:latest` when ready.
- **GHCR visibility** inherits the repo's — private repos → private images.
- **No local-GPU image is published by v1.** `Dockerfile` (local-GPU) is committed but not published; the local-path handler is deferred to a future worker package.
- **Justfile target:** `just build-worker qwen3tts` wraps the local `docker build` for dev iteration; CI uses the workflow directly.

## Testing Strategy

- **Unit tests** under `tests/worker_sdk/` (mirror of `src/acheron/worker_sdk/` per AGENTS.md):
  - `WorkerHandler` lifecycle, `Artifact` composition (BytesArtifact / StreamArtifact / FileArtifact streams equal), `_materialize_artifact`, `create_worker_app` integration via `httpx.AsyncClient` ASGI transport — no Docker.
  - `RunPodPrice` fault-tolerance: refresh succeeds → `MEASURED`; refresh fails with cached rate → `CACHED`; refresh fails without cache → `UNKNOWN`; `StaticPrice` → `STATIC`; `ZeroPrice` → `STATIC`.
  - `WorkerSettings.from_yaml` discovery order — all 4 priority levels + env override + secret-rejection.
- **Worker unit tests** under `workers/qwen3tts/tests/`:
  - `test_capabilities.py` — caps metadata shape, all 9 speakers, default speaker.
  - `test_handler.py` — `handle()` mocked `Qwen3TTSModel.generate_custom_voice`: batch inference for N chunks produces N `BytesArtifact`s with correct filenames + metadata; unknown language raises `WorkerError`; unknown speaker raises `WorkerError`.
- **Orchestrator transport tests:**
  - `tests/shell/transports/test_http_worker_multipart.py` — drives `tts_local_stub` and `tts_volume_stub` end-to-end.
  - `tests/shell/transports/test_grpc_worker_artifacts.py` — drives `tts_grpc_stub`, exercises `GrpcWorker` Artifact mode.
  - `tests/shell/transports/test_runpod_backend.py` — drives `tts_runpod_stub` end-to-end with the in-process RunPod mock.
  - `tests/shell/transports/test_multipart.py` — `_materialize_artifact` + `_build_result` standalone.
- **Dashboard tests:**
  - `tests/shell/dashboard/test_cost_partial.py` — renders each `CostBasis`; unknown ≠ free (cost cell shows `—`).
- **Costbasis model tests:** `tests/core/test_models.py` extended for `CostBasis` enum + `JobMetrics.cost_basis` field round-trip.

## File Map (Full Change List)

### Core
- `src/acheron/core/models.py` — add `CostBasis` enum; add `cost_basis: CostBasis | None = None` to `JobMetrics`.
- `tests/core/test_models.py` — cover `CostBasis` + `cost_basis` round-trip.

### Worker SDK (NEW)
- `src/acheron/worker_sdk/__init__.py` — public re-exports.
- `src/acheron/worker_sdk/handler.py` — `WorkerHandler` ABC + lifecycle hooks.
- `src/acheron/worker_sdk/artifacts.py` — `Artifact` Protocol + `BytesArtifact` / `StreamArtifact` / `FileArtifact`.
- `src/acheron/worker_sdk/app.py` — `create_worker_app(handler, settings) -> FastAPI`.
- `src/acheron/worker_sdk/cli.py` — `acheron-worker-edge` image entrypoint module.
- `src/acheron/worker_sdk/registration.py` — `register_with_orchestrator()`.
- `src/acheron/worker_sdk/schemas.py` — pydantic `Job` / `JobResult` request/response (strict `extra="forbid"`).
- `src/acheron/worker_sdk/settings.py` — `WorkerSettings(BaseSettings)` + `_ENV_ONLY_FIELDS`.
- `src/acheron/worker_sdk/cloud.py` — `make_runpod_handler()` adapter.
- `src/acheron/worker_sdk/pricing.py` — `PriceSource` protocol + `ZeroPrice` / `StaticPrice` / `RunPodPrice`.
- `src/acheron/worker_sdk/config_loader.py` — YAML discovery + env-override.
- `src/acheron/worker_sdk/_edge_http.py` — internal edge HTTP app driven by `_runpod_client`.
- `src/acheron/worker_sdk/_runpod_client.py` — wraps `runpod.Endpoint(id).run + poll + timeout`.
- `tests/worker_sdk/` — mirror of the above.

### Orchestrator transports
- `src/acheron/shell/transports/http.py` — content-type sniff + multipart/mixed branch.
- `src/acheron/shell/transports/grpc.py` — `Artifact` mode consumer; `pcm_data` mode preserved.
- `src/acheron/shell/transports/_multipart.py` (NEW) — `_materialize_artifact` + `_build_result` shared helpers.
- `proto/synthesis.proto` — extend `OutputChunk` with oneof; new `Artifact` + `ExecuteResponse` messages.
- `tests/shell/transports/test_multipart.py` (NEW).
- `tests/shell/transports/test_http_worker_multipart.py` (NEW).
- `tests/shell/transports/test_grpc_worker_artifacts.py` (NEW).
- `tests/shell/transports/test_runpod_backend.py` (NEW).

### Dashboard
- `dashboard/templates/index.html` — extend the Cost section partial with `Cost Basis` + `Note` columns.
- `dashboard/...` — Jinja map `CostBasis → badge color + note`.
- `tests/shell/dashboard/test_cost_partial.py` (NEW).

### Workers (NEW)
- `workers/qwen3tts/handler.py` — `Qwen3TTSRunpodHandler`.
- `workers/qwen3tts/runpod_entrypoint.py`.
- `workers/qwen3tts/worker.yaml`.
- `workers/qwen3tts/Dockerfile.runpod`.
- `workers/qwen3tts/pyproject.toml` — workspace member.
- `workers/qwen3tts/README.md`.
- `workers/qwen3tts/tests/test_handler.py`, `test_capabilities.py`.

### Stubs (replaced)
- `stubs/tts_local_stub/`, `tts_volume_stub/`, `tts_runpod_stub/`, `tts_grpc_stub/`, `asr_local_stub/`, `translation_local_stub/`, `translation_runpod_stub/`.
- Remove the existing `stubs/worker_stub.py`, `grpc_worker_stub.py`, `translation_stub.py`, and their tests.
- `Dockerfile` — replace `worker-stub` / `grpc-stub` stages with a single `worker-stub-base` stage + per-stub `CMD` overrides.
- `docker-compose.yml` — update stub services for the new matrix.

### CI / packaging
- `.github/workflows/build-workers.yml` (NEW) — publish `acheron-qwen3tts-runpod` to GHCR.
- `pyproject.toml` — declare `acheron-worker-edge` console script (`acheron-worker-edge = "acheron.worker_sdk.cli:main"`).
- `pyproject.toml` — uv workspace members declaration + import-linter contracts for `worker_sdk`, `workers.*`.
- `Justfile` — `build-worker <name>` target wrapping local `docker build`.
- `tests/__init__.py` paths adjusted for the new worker / SDK layouts per AGENTS.md mirror rule.

### `runpod` Python SDK packaging
- Declared in the top-level `pyproject.toml` `dependencies` as `runpod~=1.9` (transitively pins `cryptography<47`). The main `cryptography` pin is `~=46.0` (the highest 46.x line) — matches the dev group's `~=46.0` so `uv` resolution succeeds.
- Imported at module top in `acheron.worker_sdk._runpod_client`; no lazy wrapper.
- Unit tests monkey-patch `_open_endpoint` via `monkeypatch.setattr` so the SDK is never invoked.

## Plan 2 — Orchestrator Transports (deviations from the plan body)

- **Cost aggregation moved into each executor** (sequential, streaming, async) rather than `orchestrator.py`'s PlanResult construction. Each executor already accumulates per-step `JobMetrics` for `total_cost`; the per-step `cost_basis` is collected in the same pass and `PlanResult.total_cost_basis = aggregate_cost_basis(per_step_metrics)` is set in one place. The orchestrator's failure-path `PlanResult(...)` constructions (no steps ran) leave `total_cost_basis=None`, which is the desired default.
- **`aggregate_cost_basis` uses `max(...)` on the confidence order**, not `min`. The confidence map is `{MEASURED: 0, CACHED: 1, STATIC: 2, UNKNOWN: 3}`; least-confident has the highest number.
- **Multipart metadata is dropped at the orchestrator boundary.** The shared `_materialize_artifact` helper does not accept a `metadata` parameter — `OutputFile` has no `metadata` field. The `X-Acheron-Metadata` HTTP header is read on the boundary (not JSON-parsed); the gRPC `Artifact.metadata` map is never read at all. Both are dropped before materialization. Worker-supplied filenames are also sanitized against path-traversal attacks (`_safe_join` refuses blank, NUL-byte, `..`, and absolute filenames).
- **`PriceSource` Protocol is NOT `@runtime_checkable`.** No caller used `isinstance(x, PriceSource)`, and removing the decorator lets basedpyright do structural-subtyping checks for `ZeroPrice` / `StaticPrice` / `RunPodPrice`. The dataclass implementations still satisfy the protocol.
- **`HttpWorker` / `GrpcWorker` accept a `data_dir` constructor kwarg** that defaults to `ACHERON_DATA_DIR` env var (fallback `/data/jobs`). Both transports' `_parse_multipart` / `_assemble_artifacts` derive `dest_dir = data_dir / <plan_job_id> / <step_id>` from the `Job.job_id`.
- **`stubs/grpc_worker_stub.py` updated to emit `OutputChunk` (not `AudioChunk`)** — the proto extension renamed the message in Task 4. Tests follow.
- **Dashboard cost partial: `not basis or basis == "unknown"`** for the cost cell. A missing `total_cost_basis` (older orchestrator) renders the dash glyph (`—`) rather than `$0.00`. This is the same visual treatment as the explicit `"unknown"` basis.
- **`ExecuteRequest` schema is a runtime import in `_edge_http.py`** even though TC001 wants it in `TYPE_CHECKING` — FastAPI inspects the route signature at request time to know how to parse the body. Noqa on the import.

## Plan 3 — Worker + Stubs + Deploy (deviations from the plan body)

- **`workers.qwen3tts.handler.handle()` returns `list[Artifact]`, not `list[BytesArtifact]`.** The SDK's `WorkerHandler.handle()` is typed `list[Artifact]`; mypy rejects the narrower `list[BytesArtifact]` return on the override. Each artifact is a `BytesArtifact` instance — the type widening is in the signature only.
- **`workers.qwen3tts.handler._chunk_text` and `_chunk_chapter_id` raise `WorkerError` on missing/non-string fields.** The plan body used `c["text"]` which raises a raw `KeyError`; we wrap it in `WorkerError` so the orchestrator gets a structured error and the user sees a clean message (`chunk.text is required`). Tests assert the message.
- **`workers.qwen3tts.handler._chunk_chapter_id` rejects path-traversal values** (defense-in-depth alongside the orchestrator's `_safe_join`). Rejects `..`, `/`, `\`, NUL, and other illegal whitespace. The orchestrator boundary is safe today; this is for defense-in-depth and to fail fast at the worker.
- **`workers.qwen3tts.handler.handle()` rejects non-list or empty `chunks` with an empty artifact list (not an error).** The orchestrator passes `chunks: []` for "no audio" jobs (e.g., empty book); the worker's `handle()` returns `[]` (matches the orchestrator's streaming-executor contract). The SDK stub `stubs._sdk_base.StubTTSHandler` DOES return a single default silent WAV for empty chunks — that's a deliberate divergence so the stub stays useful for the integration-test fixture, but the production worker is strict.
- **New SDK class: `RunPodForwarderHandler` (`acheron.worker_sdk.cloud`).** The GPU-less edge container cannot import `Qwen3TTSRunpodHandler` (which needs torch). The forwarder implements `WorkerHandler` by accepting `/execute` from the orchestrator and forwarding the serialised job to a RunPod serverless endpoint via `RunPodClient`. Its `capabilities()` delegates to a *phantom* handler class (the cloud-side handler module is bundled in the edge image — it imports lazily, so no GPU deps are needed at import time). Settings get a new `phantom_handler: str | None` field. Tests in `tests/worker_sdk/test_cloud.py::TestRunPodForwarderHandler`.
- **`Dockerfile.edge` bundles `workers/qwen3tts/worker.edge.yaml`** (renamed from `worker.yaml` to avoid confusion with the cloud-side config). The edge image's `WORKER_NAME=qwen3tts` env causes the config loader to pick this file. `handler: acheron.worker_sdk.cloud:RunPodForwarderHandler` + `phantom_handler: workers.qwen3tts.handler:Qwen3TTSRunpodHandler`.
- **Cloud-side `Dockerfile.runpod` uses Python 3.12** (not 3.14). qwen-tts + flash-attn-2 wheels are pre-built for cp312; the 3.14-slim build would need to compile from source.
- **`Dockerfile.edge` uses Python 3.14-slim** to match the orchestrator + dashboard images.
- **`workers/qwen3tts/tests/__init__.py` uses a workspace-local `pythonpath = ["../.."]`.** Pytest picks the workers' `pyproject.toml` as `rootdir` because of the local config; the relative `pythonpath` lets `from workers.qwen3tts...` resolve.
- **`workers/qwen3tts/pyproject.toml` does NOT declare `qwen-tts` / `soundfile` as workspace deps.** They would pull `librosa` → `numba` → `llvmlite==0.36` which doesn't build on Python 3.14. The Docker image installs them directly. The workspace `pyproject.toml` is purely a packaging skeleton; the dev `uv sync` works because `qwen-tts` is never resolved.
- **The 7-stub matrix replaces the 4 legacy stubs.** New stubs:
  - `tts_local_stub` (compose: `tts-local-stub`, port 8001)
  - `asr_local_stub` (compose: `asr-local-stub`, port 8002)
  - `translation_local_stub` (compose: `translation-local-stub`, port 8003)
  - `qwen3tts-edge` (compose: `qwen3tts-edge`, port 8004) — the RunPod forwarder
  - `tts_volume_stub` (compose: `tts-volume-stub`, port 8005)
  - `tts_runpod_stub` (compose: `tts-runpod-stub`, port 8006)
  - `translation_runpod_stub` (compose: `translation-runpod-stub`, port 8007)
  - `tts_grpc_stub` (compose: `tts-grpc-stub`, port 9002) — HTTP-edge sidecar
  - Each is a ~15-line `main.py` calling `create_worker_app` + `uvicorn.run`. Per-stub variance lives in `worker.yaml` (price_source, output_mode, listen_port, dollars_per_hour). Compose service names now match the long stub names (the plan called for this; the original Task 8 step 6 used short names like `tts-stub`).
  - The `tts_grpc_stub` is HTTP-only at the edge (the gRPC contract is exercised in `tests/shell/test_grpc_worker.py` via `_FakeSynthesisServicer`). v1 workers ship HTTP.
- **`ACHERON_WORKER__RUNPOD_BASE_URL` env hook IS honored by `_open_endpoint`** (forwarded to the runpod SDK's own `RUNPOD_BASE_URL` if it accepts it). The runpod SDK's `Endpoint` constructor doesn't take a `base_url` kwarg; if the env var is silently ignored, the standard test seam is `monkeypatch.setattr("acheron.worker_sdk._runpod_client._open_endpoint", ...)`.
- **Mock RunPod server in `stubs/_sdk_base/mock_runpod.py` echoes the submitted job's id** in the `/run` response (was hardcoded `stub-job-1` originally). The mock is started in a daemon thread by the RunPod stubs; its purpose is reserved for a future integration test or forwarder.
- **`tests/integration/test_tls.py` is xfailed.** The legacy stubs (`worker_stub`, `grpc_worker_stub`) had their own TLS plumbing (using `acheron.shell.tls.uvicorn_ssl_kwargs`); the SDK's `create_worker_app` is HTTP-only in v1. Worker-side TLS is deferred to a follow-up.
- **`tests/integration/conftest.py` `grpc_tts_stub` fixture uses an inline `_LegacyGrpcTtsServicer`** (in the conftest) instead of the deleted `stubs.grpc_worker_stub.create_server`.
- **Type-check pipeline includes `workers/qwen3tts/`.** `just type-check` runs `mypy src/ tests/ workers/qwen3tts/`. `basedpyright.include` was extended to include `workers`. `mypy_path` was simplified back to `src:stubs` (the `workers` entry caused mypy to find the worker module twice under different names).
- **`.github/workflows/build-workers.yml` has two jobs**: `build-qwen3tts` (publishes `acheron-qwen3tts-runpod:<sha>+latest` to GHCR) and `build-edge` (publishes `acheron-worker-edge:<sha>+latest`). Both skip `push:` on PRs (builds for cache warming only). `cache-from: type=gha` / `cache-to: type=gha,mode=max` caches the heavy torch + qwen-tts layers.
- **Import-linter contract added: `workers-no-shell`.** Forbids `workers.* -/-> acheron.shell`. The worker package is allowed to import `acheron.worker_sdk` + `acheron.core` only.
- **`Justfile` adds `build-worker <name>` and `build-edge` targets** wrapping `uv build --package acheron --out-dir dist` + `docker build`. CI does the real publish.

## References

- [Acheron design spec](./architecture.md) — extended by this spec.
- [Implementation roadmap](./roadmap.md) — Layer 8 row updated to reflect 8a/8b/8c decomposition.
- [Deployment & dashboard design](./deployment-and-dashboard.md) — `HealthProvider` plumbing this spec reuses.
- [gRPC streaming design](./grpc-streaming.md) — extended additively here for the `Artifact` oneof.
- [Piepline streaming design](./pipeline-streaming.md) — `StreamingExecutor` reads `JobResult.cost_basis` unchanged.
- [Qwen3-TTS model card](https://huggingface.co/Qwen/Qwen3-TTS-12Hz-1.7B-CustomVoice) — CustomVoice variant with 9 built-in speakers.
- [Qwen3-TTS repo](https://github.com/QwenLM/Qwen3-TTS) — `qwen-tts` PyPI package, `generate_custom_voice` API.
- [RunPod serverless docs](https://docs.runpod.io/) — `runpod.serverless.start({"handler": ...})` cloud-side handler pattern, network volume + `HF_HUB_OFFLINE=1` model caching.
- [RunPod runpod-python SDK](https://github.com/runpod/runpod-python) — `endpoint = runpod.Endpoint(id)`; `req = endpoint.run(input)`; `req.status()`; `req.output(timeout=N)`.
- [RunPod GraphQL GPU types](https://api.runpod.io/graphql) — `gpuTypes(input: {id: ...}) { lowestPrice(input: {gpuCount: 1, secureCloud: ...}) { uninterruptablePrice } }`.
- An earlier translation sub-project stub predates the blueprint and is superseded by 8c.
