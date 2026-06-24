# Layer 8b — ASR Worker (RunPod Serverless) Design

Second of three Layer 8 sub-projects. Replays the Layer 8a TTS blueprint
(`acheron.worker_sdk`) for **ASR**: ships the `workers/granite_speech/` worker
against `ibm-granite/granite-speech-4.1-2b`, deployed as a RunPod Serverless
endpoint on a single 24GB L4 GPU. Extends the SDK with a typed `Input`
Protocol symmetric with the output-side `Artifact`, so ASR's audio-in
contract has the same shape as TTS's audio-out contract. The translation
sub-project (8c) reuses the same SDK and inherits the `Input` Protocol
forward — no further SDK churn.

## Scope

**In scope:**
- A new `Input` Protocol subpackage in `acheron.worker_sdk` (symmetric with
  the existing `Artifact` Protocol): `BytesInput` / `StreamInput` / `FileInput`
  variants. `WorkerHandler.handle()` gains an optional second parameter
  `input: Input | None = None` so every existing handler (TTS, translation
  stubs, `RunPodForwarderHandler`) keeps compiling unchanged.
- The SDK's `/execute` route accepts `multipart/form-data` on the request
  side (one `application/json` part for the `ExecuteRequest` envelope, plus
  one or more binary parts for the input payload). The legacy `application/json`
  request body continues to work (TTS / translation / non-audio workers).
- Orchestrator-side: `HttpWorker.execute()` branches on `job.job_type == ASR`
  to load the upstream extract step's audio from `StepCache` and POST
  multipart to the worker. The response side is unchanged (multipart/mixed
  in, `JobResult` JSON out — same path as TTS).
- One shipped worker: `workers/granite_speech/` against
  `ibm-granite/granite-speech-4.1-2b` (Apache 2.0). 6 ASR languages
  (`en, fr, de, es, pt, ja`); 2B-param BF16 model that fits comfortably in
  24GB.
- RunPod Serverless deployment mode only. A single L4 GPU endpoint per the
  deployer's compute choice. The existing `RunPodPrice` auto-discovers the
  GPU from the endpoint's `gpuIds`; switching GPU type takes effect on the
  next price refresh with no image rebuild.
- Edge container: the same generic `acheron-worker-edge` image (published
  by 8a CI). New compose service `granite-speech-edge` under a
  `runpod-asr` profile, configured via `worker.yaml` + env, forwards
  `/execute` to the RunPod serverless endpoint via `runpod.Endpoint(id).run(...)`.
- `RunPodHealthProvider` cold-start detection is reused unchanged — the
  worker's `capabilities.metadata["health_provider"] = "runpod"` plus
  `"health_endpoint_id"]` tag triggers the existing `HealthMonitor`'s
  `BOOTING` / `HEALTHY` / `OFFLINE` state machine.
- GHCR CI workflow publishes `acheron-granite-speech-runpod` images on tag
  and `main`.

**Out of scope (deferred to separate sub-projects):**
- 8c — `translategemma` translation worker. Refines the `Input` Protocol
  contract: translation is text-in/text-out, but the chunks arrive via a
  multipart `BytesInput` (carrying `chunks.json`), not as a `None`
  payload. 8c also retroactively refactors the qwen3tts handler to read
  from `Input` (closing the latent end-to-end gap the 8a spec left
  open). See [Layer 8c spec](./2026-06-23-layer8c-translategemma-worker-design.md).
- `granite-speech-4.1-2b-plus` (speaker-attributed ASR + word-level
  timestamps) — deferred to a future sub-project. v1 emits one transcript
  per chapter without word boundaries; the downstream `ChunkingHandler` is
  unchanged.
- `granite-speech-4.1-2b-nar` (non-autoregressive variant) — explicitly
  excluded by the deployer's choice. The non-autoregressive variant trades
  accuracy for throughput; the deployer picked the autoregressive 2B base
  for transcription quality.
- AST (speech translation) capability of the model. The model supports
  `en ↔ {fr, de, es, pt, ja}` and `en → {it, zh}` AST, but Acheron's
  pipeline has a separate TRANSLATION worker step, not an AST step. v1 is
  ASR-only; the model card's prompt change for AST is a v2 capability.
- Local-GPU `GraniteSpeechLocalHandler` — workers commit to one
  deployment mode by being one mode, per the 8a design. A local-GPU
  handler would be a separate future worker package, not a config knob.
- Per-chapter parallelism for ASR and `workers_max > 1` endpoint scaling.
  v1 is one book at a time, one ASR step at a time.
- Per-step worker targeting (the `asr_model` field on `AudioRequest` is
  wired into the transcribe step's payload today but
  `step_handler._language_matches` selects workers purely by `WorkerType`
  + language pair). Deferred to a separate sub-project per the 8a spec.

## Repository Layout

Single `acheron` wheel. The SDK gains a small new subpackage
(`worker_sdk.inputs`); workers are top-level packages outside the `acheron`
import tree; the orchestrator's `HttpWorker` is the only call site that
learns about the ASR step's upstream extract output.

```
src/
  acheron/
    core/                # existing pure types — unchanged
    shell/
      transports/
        http.py           # extended: _execute_asr_multipart branch
        _multipart.py     # extended: _parse_request_multipart helper
      ...
    proto/
    worker_sdk/           # extended (8a blueprint + 8b input protocol)
      __init__.py
      handler.py          # EXTENDED: handle() gains input: Input | None = None
      artifacts.py
      inputs.py           # NEW: Input Protocol + BytesInput / StreamInput / FileInput
      app.py
      cli.py
      registration.py
      schemas.py
      settings.py
      cloud.py            # EXTENDED: _serialise_job_for_runpod carries input_audio
      pricing.py
      config_loader.py
      _edge_http.py       # EXTENDED: /execute accepts multipart OR json
      _runpod_client.py
  workers/                # top-level dir (not in the acheron import tree)
    granite_speech/       # NEW worker
      handler.py          # GraniteSpeechRunpodHandler
      runpod_entrypoint.py
      worker.yaml         # image default for the RunPod runtime
      worker.edge.yaml    # edge-side config (RunPodForwarderHandler)
      Dockerfile.runpod
      pyproject.toml      # workspace member; deps: acheron
      README.md
      tests/
    qwen3tts/             # unchanged
    _shared.py            # NEW: _safe_chapter_id helper shared by all workers
stubs/
  asr_local_stub/         # updated StubASRHandler.handle accepts Input | None
  _sdk_base/__init__.py   # EXTENDED: StubASRHandler.handle accepts Input | None
.github/
  workflows/
    build-workers.yml     # EXTENDED: + build-granite-speech job
```

**Import boundaries** (no new import-linter contracts needed; the existing
8a boundaries cover 8b):
- `acheron.worker_sdk -> acheron.core` (allowed).
- `acheron.worker_sdk -/-> acheron.shell` (forbidden).
- `workers.* -> acheron.worker_sdk, acheron.core` (allowed).
- `workers.* -/-> acheron.shell` (forbidden).

`workers/_shared.py` is a small module under `workers/` exposing
`safe_chapter_id(cid: str) -> str` and `MAX_CHAPTER_ID_LEN`. Both
`workers/qwen3tts/handler.py` and `workers/granite_speech/handler.py`
import it; it imports only stdlib (`acheron.core.errors.WorkerError` is
allowed because `core` is in the allowed import set). Refactor of 8a's
inline `_chunk_chapter_id` into the shared helper is a one-line change
in 8a; the new helper's tests cover both workers.

## Deployment Topology

Same as 8a: the model lives inside the RunPod serverless endpoint; a
GPU-less edge container bridges the orchestrator's HTTP-worker protocol
and RunPod's `/run` + `/status` + `/cancel` job protocol.

| Image | Where it runs | Contains | Published by |
|---|---|---|---|
| `acheron-granite-speech-runpod` | inside the RunPod serverless endpoint (cloud) | model + `GraniteSpeechRunpodHandler` + `runpod.serverless.start(...)` | GHCR by CI (this sub-project) |
| `acheron-worker-edge` | alongside the orchestrator (compose service) | FastAPI app + RunPod forwarder + registration client; no GPU | GHCR by CI (8a, reused) |

The edge container is **generic across all workers** — same image for
TTS/ASR/translation, only `worker.yaml` + env differ per service. The
recoverer's deploy surface is `docker-compose.yml` (service entry present
in the main compose) + `.env` (RunPod endpoint ID, API key, registration
token). The user does not clone the repo or build anything.

The orchestrator communicates with the worker through the **same** `/execute`
endpoint shape 8a established — but 8b flips the input side from
`application/json` to `multipart/form-data` for ASR steps. The output side
(`multipart/mixed` response) is unchanged.

## `acheron.worker_sdk` API Surface

### `inputs.Input` — typed input protocol (symmetric with `Artifact`)

```python
# src/acheron/worker_sdk/inputs.py (NEW)

class Input(Protocol):
    """Transport-neutral input handed to WorkerHandler.handle() alongside the Job."""

    @property
    def content_type(self) -> str: ...  # noqa: D102
    @property
    def metadata(self) -> dict[str, JsonValue]: ...  # noqa: D102
    def stream(self) -> AsyncIterator[bytes]: ...  # noqa: D102


@dataclass(frozen=True)
class BytesInput:    # in-memory bytes — short audio, embedded text
    content_type: str
    data: bytes
    metadata: dict[str, JsonValue] = field(default_factory=dict)
    async def stream(self) -> AsyncIterator[bytes]: yield self.data


@dataclass(frozen=True)
class StreamInput:   # lazily-produced chunks — long audio, bounded memory
    content_type: str
    producer: Callable[[], AsyncIterator[bytes]]
    metadata: dict[str, JsonValue] = field(default_factory=dict)
    async def stream(self) -> AsyncIterator[bytes]:
        async for chunk in self.producer(): yield chunk


@dataclass(frozen=True)
class FileInput:     # worker reads from disk (shared-volume mode or tmp file)
    content_type: str
    path: Path
    metadata: dict[str, JsonValue] = field(default_factory=dict)
    async def stream(self) -> AsyncIterator[bytes]:
        async with aiofiles.open(self.path, "rb") as f:
            while chunk := await f.read(64 * 1024): yield chunk
```

The Protocol declares `content_type` and `metadata` as `@property`
accessors (not plain attributes) for the same reason `Artifact` does: the
concrete implementations are `@dataclass(frozen=True)`, so their fields
are read-only at instance level. A Protocol that declared plain attributes
would be incompatible with frozen dataclasses under strict type-checkers.

### `handler.WorkerHandler` — extended signature

```python
# src/acheron/worker_sdk/handler.py (EXTENDED)

class WorkerHandler(ABC):
    @abstractmethod
    def capabilities(self) -> WorkerCapabilities: ...

    @abstractmethod
    async def handle(self, job: Job, input: Input | None = None) -> list[Artifact]:
        """Run inference for `job`, consuming `input` if the step is audio-in.

        `input` is the new second parameter (8b). Default `None` keeps
        backward compatibility for stub handlers that don't take an input.
        Real workers (ASR, TTS, translation) consume `input` — see the
        8b ASR handler for audio bytes and the 8c handlers for text
        chunks; the qwen3tts handler was refactored to consume `input`
        in 8c (closing the latent end-to-end gap from 8a).

        The handler may consume `input` once via `input.stream()` (returns an
        `AsyncIterator[bytes]`); iterating past the first call is
        implementation-defined.
        """

    async def startup(self) -> None: ...    # default no-op
    async def shutdown(self) -> None: ...   # default no-op
```

The second parameter is keyword-defaulted to `None` so existing handler
subclasses (`Qwen3TTSRunpodHandler`, `StubTTSHandler`,
`StubTranslationHandler`, `RunPodForwarderHandler`) keep compiling and
behaving unchanged. Only `GraniteSpeechRunpodHandler` and
`StubASRHandler` consume `input` in 8b.

### `app.create_worker_app` and `cli`

`create_worker_app(handler, settings) -> FastAPI` exposes
`/health` + `/capabilities` + `/execute` driven by the handler. The
`/execute` route is extended to accept either:
- **`application/json`** — the existing path. TTS / translation / non-audio
  workers continue to receive the same `ExecuteRequest` envelope; `input`
  is `None` on the handler call.
- **`multipart/form-data`** — the 8b path. The first `application/json`
  part is the `ExecuteRequest` envelope; the first binary part (any
  content-type other than `application/json`) is built into a `BytesInput`
  and passed as the second argument to `handler.handle()`. ASR workers
  expect a binary audio part; TTS and translation workers (post-8c)
  expect a JSON `chunks.json` part. EXTRACTION / CHUNKING / PACKAGING
  continue to use the legacy `application/json` request body.

The error response body is unchanged: a `JobResult`-shaped JSON body with
`status=FAILED` and `error=<message>` (handler exception), parseable by
the orchestrator's `TypeAdapter(JobResult).validate_json`. Opaque 5xx with
`{"status":"failed", "error": ...}` is forbidden.

### `cloud.make_runpod_handler` and `RunPodForwarderHandler`

The cloud-side adapter and the edge forwarder gain one new code path:
serialise / deserialise the `Input` over RunPod's JSON `/run` wire.

```python
# src/acheron/worker_sdk/cloud.py (EXTENDED)

def _serialise_job_for_runpod(job: Job, input: Input | None = None) -> dict[str, Any]:
    out = {
        "input": {
            "job_id": job.job_id,
            "job_type": job.job_type.value,
            "payload": dict(job.payload),
            "chapter_id": job.chapter_id,
            "sequence_ids": list(job.sequence_ids) if job.sequence_ids else [],
        }
    }
    if input is not None:
        body = b"".join([chunk async for chunk in input.stream()])
        out["input"]["input_audio"] = {
            "content_type": input.content_type,
            "data": base64.b64encode(body).decode("ascii"),
            "metadata": dict(input.metadata),
        }
    return out


def make_runpod_handler(handler: WorkerHandler) -> Callable[[dict], Awaitable[dict]]:
    async def _rp_handler(runpod_job: dict) -> dict:
        job = _deserialise_job(runpod_job["input"])
        audio = runpod_job["input"].get("input_audio")
        if audio is not None:
            # Cloud-side: reconstruct a BytesInput from the base64-encoded
            # payload that the edge's RunPodForwarderHandler sent.
            input = _build_input_from_runpod(audio)
            artifacts = await handler.handle(job, input)
        else:
            artifacts = await handler.handle(job)
        return {"artifacts": [await _serialise(a) for a in artifacts]}
    return _rp_handler


class RunPodForwarderHandler(WorkerHandler):
    async def handle(self, job: Job, input: Input | None = None) -> list[Artifact]:
        if self._client is None:
            raise WorkerError("RunPodClient not initialised (startup() not run)")
        payload = _serialise_job_for_runpod(job, input)
        result = await self._client.run(payload)
        return _deserialise_runpod_artifacts(result)
```

The base64-encoded shape keeps RunPod's `/run` wire JSON-only (RunPod's
endpoint contract is JSON in / JSON out). TTS jobs (no `input_audio` field)
keep working: `_rp_handler` sees `audio is None` and calls
`handler.handle(job)`.

### `registration.register_with_orchestrator`

Unchanged from 8a. The SDK adds `metadata["health_provider"] = "runpod"`
and `metadata["health_endpoint_id"] = settings.runpod_endpoint_id` (already
done in 8a's `create_worker_app._registration_caps`; reused by 8b unchanged).

## Worker Settings & Config Discovery

`WorkerSettings` is reused unchanged from 8a. The same env-prefix
convention applies: `ACHERON_WORKER__<FIELD_UPPER>`. No new fields are
added in 8b — the prompt is hardcoded to the model's recommended
punctuation prompt (see "Capabilities" below), and a per-deployment
`asr_prompt` knob is reserved as a future extension (YAGNI: 1 prompt
suffices for v1).

**Discovery order** (same as 8a):
1. `WORKER_CONFIG` env var → explicit path.
2. `<cwd>/<worker_name>.worker.yaml` (worker_name from `WORKER_NAME` env).
3. `<cwd>/worker.yaml`.
4. Env vars only.

The committed `workers/granite_speech/worker.yaml` is the image default.
A deployer can override individual fields by mounting a small
`granite_speech.worker.yaml` override without rebuilding the image.

### Settings shape (relevant subset)

```yaml
# workers/granite_speech/worker.yaml (image default)
worker_id: "granite-speech-1"
orchestrator_url: "http://orchestrator:8000"
listen_port: 8001
execution_timeout_s: 1800
```

Sensitive fields (`registration_token`, `runpod_api_key`,
`runpod_endpoint_id`) are env-only — rejected if they appear in YAML.

## Pricing & Cost Basis

Unchanged from 8a. `RunPodPrice` is the default. The deployer provisions
a single L4 endpoint (the cheapest 24GB tier per the deployer's compute
choice); `RunPodPrice` queries the endpoint's `gpuIds` via GraphQL and
resolves `uninterruptablePrice` (community-cloud, `secure_cloud: false`).
Pricing is best-effort: never blocks a job, never silently conflates
"unavailable" with "free".

`JobMetrics.cost_basis` round-trips through the multipart metrics part via
`JobMetrics.model_dump_json()` (pydantic `TypeAdapter`-driven). The mapping
is identical to 8a:

- Fresh refresh succeeded this call → `MEASURED`.
- Cache hit (rate set, refresh not attempted) → `CACHED` for subsequent
  TTL-window calls within the same process.
- `_rate is None` after failed refresh → `UNKNOWN`.
- `StaticPrice` → `STATIC`.
- `ZeroPrice` → `STATIC` (operator opted out).

`PlanResult.total_cost_basis` aggregates across steps via
`aggregate_cost_basis` (least-confident basis wins). The dashboard's
`Cost Basis` column renders the badge unchanged.

## Orchestrator-Side Changes

### `HttpWorker.execute()` — content-type-driven dispatch (response side unchanged; request side gains an ASR branch)

```python
# src/acheron/shell/transports/http.py (EXTENDED)

class HttpWorker(Worker):
    def __init__(
        self,
        base_url: str,
        client: httpx.AsyncClient | None = None,
        *,
        data_dir: Path | str | None = None,
        step_cache: StepCache | None = None,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._client = client
        if data_dir is None:
            data_dir = Path(os.environ.get("ACHERON_DATA_DIR", "/data/jobs"))
        self._data_dir = Path(data_dir)
        self._step_cache = step_cache or StepCache(self._data_dir)

    async def execute(self, job: Job) -> JobResult:
        if job.job_type == WorkerType.ASR:
            return await self._execute_asr_multipart(job)
        # Existing JSON / multipart-mixed response path (unchanged).
        resp = await self._request("POST", "/execute", json=_job_to_dict(job))
        ctype = resp.headers.get("content-type", "")
        if ctype.startswith("multipart/mixed"):
            return await self._parse_multipart(resp, job.job_id)
        return _result_adapter.validate_json(resp.content)

    async def _execute_asr_multipart(self, job: Job) -> JobResult:
        """Read the upstream extract step's audio file and POST multipart."""
        plan_job_id = job.job_id.rsplit("-", 1)[0]
        extract_outputs = await self._step_cache.load_outputs(plan_job_id, "extract")
        audio_out = next(
            (o for o in extract_outputs if o.content_type.startswith("audio/")),
            None,
        )
        if audio_out is None:
            msg = f"ASR step {job.job_id}: no audio file in extract output"
            raise WorkerError(msg)
        audio_path = Path(audio_out.path)
        if not await asyncio.to_thread(audio_path.exists):
            msg = f"ASR step {job.job_id}: audio file missing: {audio_path}"
            raise WorkerError(msg)

        form = {
            "request": (None, json.dumps(_job_to_dict(job)).encode("utf-8"), "application/json"),
            "audio": (
                audio_path.name,
                await asyncio.to_thread(audio_path.read_bytes),
                audio_out.content_type,
            ),
        }
        if self._client is not None:
            resp = await self._client.post(f"{self._base_url}/execute", files=form)
        else:
            async with httpx.AsyncClient() as client:
                resp = await client.post(f"{self._base_url}/execute", files=form)
        resp.raise_for_status()
        ctype = resp.headers.get("content-type", "")
        if ctype.startswith("multipart/mixed"):
            return await self._parse_multipart(resp, job.job_id)
        return _result_adapter.validate_json(resp.content)
```

The `step_cache` parameter is a new keyword-only injection so the
transport can read upstream outputs without coupling to the
orchestrator's broader state. Production wiring:

- `HttpWorker.__init__` accepts `step_cache: StepCache | None = None`.
  When `None` (the default), the constructor builds a
  `StepCache(self._data_dir)` (which is a no-I/O `Path` wrapper). Tests
  pass an explicit `StepCache(tmp_path)` so they don't depend on the
  default `/data/jobs` directory.
- `default_worker_factory` gains a `step_cache: StepCache | None = None`
  keyword parameter and forwards it: `HttpWorker(registered.endpoint,
  data_dir=..., step_cache=step_cache)`. Existing call sites that pass
  no `step_cache` get a fresh `StepCache(self._data_dir)` at
  construction time — no production wiring change.
- The orchestrator passes its own `StepCache` instance (the same one
  used by `StreamingExecutor`) via `create_step_handler(registry,
  step_cache=self._step_cache, ...)`. The step handler forwards it to
  the factory. Production always has the cache available; the
  constructor default is only for tests.

### `_multipart.py` — `_parse_request_multipart` helper (NEW)

```python
# src/acheron/shell/transports/_multipart.py (EXTENDED)

async def _parse_request_multipart(
    ctype: str, body: bytes
) -> tuple[dict[str, Any], bytes, str]:
    """Parse a /execute request body into (job_dict, audio_bytes, audio_content_type).

    Accepts either multipart/form-data (one JSON part + zero or more binary
    parts) or plain application/json (legacy / TTS path). For multipart with
    no binary part, audio_bytes is empty and audio_content_type is "".
    """
    if not ctype.startswith("multipart/"):
        return (json.loads(body), b"", "")
    # BytesParser pass, pull the first JSON part and the first binary part.
    # Standalone-tested in tests/shell/transports/test_multipart.py.
```

### No planner / executor / step_handler change

The planner still emits a `transcribe` step with
`payload={"source_language": ..., "asr_model": ...}`. The step handler
still builds a `Job` and calls `worker.execute(job)`. The transport
branch is the only new code; it loads the extract output and wraps the
audio.

### `GrpcWorker` — unchanged

The `Artifact` mode (added in 8a) and the legacy `pcm_data` mode are both
preserved. ASR doesn't use gRPC in 8b; the v1 path is HTTP only. A future
sub-project could wire gRPC for ASR; the gRPC contract is ready.

### Cold-start detection — unchanged

The existing `RunPodHealthProvider` (Layer 11) and `HealthMonitor._handle_failure`
consume `metadata["health_provider"]` + `metadata["health_endpoint_id"]`.
The SDK's `register_with_orchestrator` tags RunPod workers' capabilities
with these; cold-start detection works out of the box.

### `worker_id` + `transport` — no schema change

The granite-speech edge registers `transport: "http"`. The orchestrator's
`step_handler.default_worker_factory` falls through its `match` to
`HttpWorker(registered.endpoint)`. The new `step_cache` parameter is
keyword-only; existing call sites (tests that construct `HttpWorker`
directly without `step_cache`) get a default-constructed `StepCache`
pointed at `ACHERON_DATA_DIR`.

### Cost aggregation — no orchestrator change

`PlanResult.total_cost` already sums `JobMetrics.cost_estimate` per step.
`total_cost_basis` aggregates across steps via `aggregate_cost_basis` (8a
introduced). The ASR step emits a per-step `cost_estimate` (RunPod-derived)
+ `cost_basis` exactly like 8a TTS.

## Dashboard Updates

Unchanged from 8a. The ASR step's `JobResult` flows through the existing
`/partials/cost` partial just like TTS. The `Cost Basis` column renders
`MEASURED` / `CACHED` / `UNKNOWN` / `STATIC` with the same colour mapping.
A chapter that costs $0.0042 to transcribe on a $0.40/hr L4 looks the
same in the dashboard as a chapter that costs $0.0042 to synthesise on
the same GPU.

## Stubs — SDK Test Matrix

The 8a stub matrix is reused unchanged. The only stub delta is
`StubASRHandler.handle()` gains `input: Input | None = None`:

```python
# stubs/_sdk_base/__init__.py (EXTENDED)

class StubASRHandler(WorkerHandler):
    def capabilities(self) -> WorkerCapabilities:
        return WorkerCapabilities(
            worker_type=WorkerType.ASR,
            supported_languages_in=frozenset({"en", "es", "fr", "de", "ja", "pt"}),
            supported_languages_out=frozenset({"en", "es", "fr", "de", "ja", "pt"}),
            supported_formats_in=frozenset({"mp3", "wav"}),
            supported_formats_out=frozenset({"text"}),
            max_payload_bytes=None,
            batch_capable=False,
            model_source=None,
            metadata={"stub": True},
        )

    async def handle(self, job: Job, input: Input | None = None) -> list[Artifact]:
        # `input` is accepted and ignored — the stub proves the multipart
        # contract end-to-end without GPU.
        text = "mock transcription"
        return [
            BytesArtifact(
                filename=f"{job.chapter_id}.txt",
                content_type="text/plain",
                data=text.encode("utf-8"),
                metadata={"chapter_id": job.chapter_id},
            )
        ]
```

The 6-language set matches the real worker's `supported_languages_in` so
planner tests that filter on the language set work unchanged. The
`asr_local_stub` `main.py` and `worker.yaml` are unchanged. The
`stubs/asr_local_stub/` is now exercised end-to-end with multipart input
via the new `tests/shell/transports/test_asr_multipart.py`.

No new stub directories are added in 8b. The `tts_runpod_stub` and
`translation_runpod_stub` patterns (mocked RunPod HTTP server in-process)
are reused for any future ASR-RunPod stub if a real-RunPod smoke test
becomes desirable; v1 doesn't need it (the `RunPodForwarderHandler` is
tested directly with the in-process mock via the `RunPodClient`).

## The Granite-Speech RunPod Worker

### Model

**`ibm-granite/granite-speech-4.1-2b`** (released 29 April 2026; Apache 2.0).

- 2B params; BF16 safetensors; built on `ibm-granite/granite-4.0-1b-base`.
- 16-conformer speech encoder + 2-layer q-former temporal projector +
  Granite-4.0-1B-base LLM. Total: ~4GB BF16 weights + encoder / projector
  + LLM scratch → fits comfortably in 24GB; 16GB is tight.
- 6 ASR languages: `en, fr, de, es, pt, ja`.
- Punctuation + truecasing on all 6 ASR languages (including German noun
  capitalisation) with a single prompt change.
- 16kHz mono PCM input requirement. The transformers processor accepts
  raw `bytes` (mp3 / wav / ogg) and resamples internally via
  `soundfile` / `librosa`.
- Inference via `transformers >= 4.52.1`:
  ```python
  from transformers import AutoProcessor, AutoModelForSpeechSeq2Seq
  processor = AutoProcessor.from_pretrained("ibm-granite/granite-speech-4.1-2b")
  model = AutoModelForSpeechSeq2Seq.from_pretrained(
      "ibm-granite/granite-speech-4.1-2b",
      device_map="cuda:0",
      torch_dtype=torch.bfloat16,
      attn_implementation="flash_attention_2",
  )
  ```
- Trained on 8x H100 for 30 days; inference at batch=1 fits in ~10-12GB
  VRAM; 24GB L4 has 2x headroom for the audio frame cache and LLM KV cache.

**Variants excluded by 8b**:
- `granite-speech-4.1-2b-nar` (non-autoregressive): explicitly excluded
  by the deployer's choice. Higher throughput, lower accuracy.
- `granite-speech-4.1-2b-plus` (speaker-attributed ASR + word-level
  timestamps): deferred to a future sub-project. v1 emits one transcript
  per chapter without word boundaries; the downstream `ChunkingHandler`
  unchanged.

### `handler.py`

```python
"""RunPod Serverless handler for ibm-granite/granite-speech-4.1-2b.

This module runs **inside the RunPod serverless runtime image** (see
``Dockerfile.runpod``). The cloud-side ``runpod_entrypoint.py`` imports
``GraniteSpeechRunpodHandler`` here, calls ``startup()`` eagerly at boot,
then ``runpod.serverless.start({"handler": make_runpod_handler(handler)})``.

A local-GPU fallback handler (``GraniteSpeechLocalHandler``) is deferred
to a separate future worker package — workers commit to one deployment
mode by being one mode, per the Layer 8a spec.
"""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING, Any

from acheron.core.errors import WorkerError
from acheron.core.models import Job, JsonValue, WorkerCapabilities, WorkerType
from acheron.worker_sdk.artifacts import Artifact, BytesArtifact
from acheron.worker_sdk.handler import WorkerHandler
from acheron.worker_sdk.inputs import Input
from workers._shared import safe_chapter_id

if TYPE_CHECKING:
    from acheron.worker_sdk.settings import WorkerSettings


_SUPPORTED_LANGS = frozenset({"en", "fr", "de", "es", "pt", "ja"})
_MODEL_ID = "ibm-granite/granite-speech-4.1-2b"
_DEFAULT_PROMPT = (
    "transcribe the speech with proper punctuation and capitalization."
)


class GraniteSpeechRunpodHandler(WorkerHandler):
    """Cloud-side handler run inside the RunPod serverless runtime image."""

    def __init__(self, settings: WorkerSettings) -> None:
        self._settings = settings
        self._model: Any = None
        self._processor: Any = None

    def capabilities(self) -> WorkerCapabilities:
        metadata: dict[str, JsonValue] = {
            "asr_prompt": _DEFAULT_PROMPT,
            "health_provider": "runpod",
        }
        return WorkerCapabilities(
            worker_type=WorkerType.ASR,
            supported_languages_in=_SUPPORTED_LANGS,
            supported_languages_out=_SUPPORTED_LANGS,
            supported_formats_in=frozenset({"mp3", "wav"}),
            supported_formats_out=frozenset({"text"}),
            max_payload_bytes=None,
            batch_capable=False,
            model_source=f"huggingface:{_MODEL_ID}",
            metadata=metadata,
        )

    async def startup(self) -> None:
        import torch  # noqa: PLC0415

        def _load() -> None:
            from transformers import (  # noqa: PLC0415
                AutoModelForSpeechSeq2Seq,
                AutoProcessor,
            )

            self._processor = AutoProcessor.from_pretrained(_MODEL_ID)
            self._model = AutoModelForSpeechSeq2Seq.from_pretrained(
                _MODEL_ID,
                device_map="cuda:0",
                torch_dtype=torch.bfloat16,
                attn_implementation="flash_attention_2",
            )

        await asyncio.to_thread(_load)

    async def shutdown(self) -> None:
        if self._model is not None:
            del self._model
            self._model = None
        if self._processor is not None:
            del self._processor
            self._processor = None
        import torch  # noqa: PLC0415

        torch.cuda.empty_cache()

    async def handle(self, job: Job, input: Input | None = None) -> list[Artifact]:
        if self._model is None or self._processor is None:
            msg = "Granite-Speech model not loaded (startup() not run)"
            raise WorkerError(msg)
        if input is None:
            msg = "Granite-Speech requires an audio input"
            raise WorkerError(msg)
        source_lang = job.payload.get("source_language")
        if not isinstance(source_lang, str) or source_lang not in _SUPPORTED_LANGS:
            msg = f"Unsupported source language: {source_lang!r}"
            raise WorkerError(msg)

        audio_bytes = b"".join([chunk async for chunk in input.stream()])
        if not audio_bytes:
            msg = "Empty audio input"
            raise WorkerError(msg)

        transcript = await asyncio.to_thread(self._transcribe, audio_bytes)
        chapter_id = safe_chapter_id(job.chapter_id)
        return [
            BytesArtifact(
                filename=f"{chapter_id}.txt",
                content_type="text/plain",
                data=transcript.encode("utf-8"),
                metadata={
                    "chapter_id": chapter_id,
                    "model": _MODEL_ID,
                    "language": source_lang,
                },
            )
        ]

    def _transcribe(self, audio_bytes: bytes) -> str:
        """Run transformers inference; returns the transcript string."""
        import torch  # noqa: PLC0415

        chat = [{"role": "user", "content": f"<|audio|>{_DEFAULT_PROMPT}"}]
        prompt_text = self._processor.tokenizer.apply_chat_template(
            chat, tokenize=False, add_generation_prompt=True
        )
        model_inputs = self._processor(
            prompt_text,
            audio_bytes,
            device="cuda:0",
            return_tensors="pt",
        ).to("cuda:0")
        with torch.inference_mode():
            model_outputs = self._model.generate(
                **model_inputs, max_new_tokens=4096, do_sample=False, num_beams=1
            )
        num_input_tokens = model_inputs["input_ids"].shape[-1]
        new_tokens = model_outputs[0, num_input_tokens:].unsqueeze(0)
        text = self._processor.tokenizer.batch_decode(
            new_tokens, add_special_tokens=False, skip_special_tokens=True
        )
        return text[0].strip()
```

### `runpod_entrypoint.py`

```python
"""RunPod Serverless entrypoint — loads the model eagerly at boot, then calls runpod.serverless.start.

RunPod schedules GPU pods on demand; the entry loads the model into VRAM
before the first inference request arrives so warm pods respond immediately
and cold pods pay the load cost once.
"""

from __future__ import annotations

import asyncio
import logging

import runpod

from acheron.worker_sdk.cloud import make_runpod_handler
from acheron.worker_sdk.config_loader import load_settings
from workers.granite_speech.handler import GraniteSpeechRunpodHandler

logging.basicConfig(level=logging.INFO)
logging.getLogger("transformers").setLevel(logging.WARNING)
logging.getLogger("urllib3").setLevel(logging.WARNING)
logging.getLogger("httpx").setLevel(logging.WARNING)
logger = logging.getLogger(__name__)


def main() -> None:
    """Boot the RunPod serverless worker: load model, then serve."""
    settings = load_settings()
    handler = GraniteSpeechRunpodHandler(settings)
    asyncio.run(handler.startup())
    runpod.serverless.start({"handler": make_runpod_handler(handler)})


if __name__ == "__main__":
    main()
```

### `worker.yaml` (image default)

```yaml
# Granite-Speech worker — image default config.
# Sensitive fields (RUNPOD_API_KEY, RUNPOD_ENDPOINT_ID, REGISTRATION_TOKEN)
# are env-only — rejected when present here. Override per-deploy by mounting
# a granite_speech.worker.yaml override or by setting ACHERON_WORKER_* env vars.

worker_id: "granite-speech-1"
orchestrator_url: "http://orchestrator:8000"
listen_port: 8001
execution_timeout_s: 1800

# Pricing — RunPod GraphQL API is the default. The GPU type is NOT a config
# field: RunPodPrice reads the endpoint's gpuIds via the RunPod GraphQL API.
# The deployer provisions a single L4 endpoint (cheapest 24GB tier); changing
# GPU on the RunPod endpoint takes effect on the next price_cache_ttl_s
# refresh; no image rebuild required.
price_source: runpod
secure_cloud: false
# price_cache_ttl_s: 3600.0

# Output transport — multipart default (output side; input is implicit
# from job_type == ASR).
output_mode: multipart

# Handler — used by the generic acheron-worker-edge CLI to import the handler
# class when running the edge container alongside the orchestrator. The
# runpod_entrypoint.py in the RunPod runtime image uses the import directly.
handler: "workers.granite_speech.handler:GraniteSpeechRunpodHandler"
model_id: "ibm-granite/granite-speech-4.1-2b"
```

### `worker.edge.yaml` (edge-side)

```yaml
# Edge-side worker config for the acheron-worker-edge image.
# Identical shape to workers/qwen3tts/worker.edge.yaml — phantom_handler
# is the cloud-side GraniteSpeechRunpodHandler, which the edge imports
# to read its static capabilities() without loading the model.

worker_id: "granite-speech-edge"
orchestrator_url: "http://orchestrator:8000"
listen_port: 8001
execution_timeout_s: 1800

handler: "acheron.worker_sdk.cloud:RunPodForwarderHandler"
phantom_handler: "workers.granite_speech.handler:GraniteSpeechRunpodHandler"
model_id: "ibm-granite/granite-speech-4.1-2b"

price_source: runpod
secure_cloud: false

# Edge transport is always HTTP multipart on the output side; the input
# side is multipart (form-data) when job_type == ASR, JSON otherwise.
output_mode: multipart
```

### `Dockerfile.runpod`

```dockerfile
# RunPod Serverless runtime image for ibm-granite/granite-speech-4.1-2b.
#
# Built from the repo root with:
#   docker build -f workers/granite_speech/Dockerfile.runpod -t acheron-granite-speech-runpod .
#
# CI publishes this image to ghcr.io via .github/workflows/build-workers.yml.

FROM python:3.12-slim AS runpod-runtime

ENV PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    HF_HUB_OFFLINE=1 \
    TRANSFORMERS_OFFLINE=1

# OS deps for soundfile (libsndfile) + ffmpeg fallback (mp3/ogg) +
# flash-attn build (git + build-essential).
RUN apt-get update && apt-get install -y --no-install-recommends \
        libsndfile1 \
        ffmpeg \
        git \
        build-essential \
    && rm -rf /var/lib/apt/lists/*

# PyTorch first — matches the CUDA version the host passes via --gpus.
# Pin to 2.5.1 + cu121; flash-attn must match the torch ABI.
RUN pip install --no-cache-dir torch==2.5.1 torchaudio==2.5.1 \
        --index-url https://download.pytorch.org/whl/cu121

# Install the acheron wheel (built from the monorepo) — provides acheron.worker_sdk.
COPY dist/acheron-*.whl /tmp/
RUN pip install /tmp/acheron-*.whl && rm /tmp/acheron-*.whl

# Worker deps — transformers + accelerate + soundfile + flash-attn.
# transformers >= 4.52.1 is required for native Granite-Speech support.
# hf-transfer is intentionally NOT installed: the runtime image is offline
# (HF_HUB_OFFLINE=1) and never re-downloads; hf-transfer is a pre-warm-only
# concern documented in the README.
RUN pip install --no-cache-dir \
        "transformers>=4.52.1" \
        accelerate \
        soundfile
RUN pip install --no-cache-dir flash-attn==2.5.9.post1 --no-build-isolation

# RunPod SDK so runpod.serverless.start() is importable.
RUN pip install --no-cache-dir runpod

WORKDIR /app

# The worker's deployable assets. The cloud-side handler is entrypoint-only.
COPY workers/granite_speech/handler.py /app/handler.py
COPY workers/granite_speech/runpod_entrypoint.py /app/runpod_entrypoint.py
COPY workers/granite_speech/worker.yaml /app/worker.yaml
COPY workers/__init__.py /app/workers/__init__.py
COPY workers/granite_speech/__init__.py /app/workers/granite_speech/__init__.py
COPY workers/_shared.py /app/workers/_shared.py

# HF cache lives on the RunPod network volume; offline mode forces the cached snapshot.
ENV HF_HOME=/runpod-volume/huggingface-cache \
    PYTHONPATH=/app

CMD ["python", "runpod_entrypoint.py"]
```

**Network volume for cached weights** (avoids re-downloading ~4GB on every
cold start): RunPod serverless mounts a network volume at
`/runpod-volume`. Weights cache lives at
`/runpod-volume/huggingface-cache`. `HF_HOME` points there;
`HF_HUB_OFFLINE=1` + `TRANSFORMERS_OFFLINE=1` makes transformers prefer
cached weights (matches RunPod's documented model-caching pattern).

Deployer pre-warms the volume once via a one-shot pod that runs (with
`hf-transfer` installed for the parallel-chunk speedup, since
pre-warm is the only moment the runtime image touches the network):
```bash
pip install "huggingface_hub[cli]" hf-transfer
HF_HUB_ENABLE_HF_TRANSFER=1 huggingface-cli download \
    ibm-granite/granite-speech-4.1-2b \
    --local-dir /runpod-volume/huggingface-cache/hub/models--ibm-granite--granite-speech-4.1-2b
```

`HF_HUB_ENABLE_HF_TRANSFER=1` is a pre-warm-only concern; it is not set
in the runtime image Dockerfile because the runtime image is offline
(`HF_HUB_OFFLINE=1`) and never re-downloads.

### Capabilities sent at registration (from the edge)

```json
{
  "worker_id": "granite-speech-1",
  "endpoint": "http://granite-speech-edge:8001",
  "transport": "http",
  "capabilities": {
    "worker_type": "asr",
    "supported_languages_in": ["de", "en", "es", "fr", "ja", "pt"],
    "supported_languages_out": ["de", "en", "es", "fr", "ja", "pt"],
    "supported_formats_in": ["mp3", "wav"],
    "supported_formats_out": ["text"],
    "max_payload_bytes": null,
    "batch_capable": false,
    "model_source": "huggingface:ibm-granite/granite-speech-4.1-2b",
    "metadata": {
      "asr_prompt": "transcribe the speech with proper punctuation and capitalization.",
      "health_provider": "runpod",
      "health_endpoint_id": "<endpoint id>"
    }
  }
}
```

The existing `RunPodHealthProvider` + `HealthMonitor._handle_failure`
consume `health_provider` + `health_endpoint_id` to distinguish booting
from offline during cold starts. No orchestrator change.

### Language implications for the planner

The ASR step's source-language must be one of `{en, fr, de, es, pt, ja}`.
The TTS step's target-language keeps its existing 10-language set from 8a
(`en, zh, ja, ko, de, fr, ru, pt, es, it`). Cross-language jobs from an
unsupported ASR source (e.g. `zh`, `ko`, `ru` audio input) are rejected
at plan compilation with the existing `InvalidLanguagePathError` — no
GPU time is spent, per the design spec's "Invalid language path" row in
the error-handling table.

## `workers/_shared.py` (NEW)

```python
"""Shared utilities for all worker handlers (8a TTS, 8b ASR, future 8c)."""

from __future__ import annotations

from acheron.core.errors import WorkerError

MAX_CHAPTER_ID_LEN = 128


def safe_chapter_id(cid: str) -> str:
    """Sanitise a chapter_id for use as a filename component.

    Rejects blank, NUL-byte, newline, tab, path-separator (``/`` / ``\\``),
    absolute-path, and ``..``-component values. The orchestrator's
    ``_safe_join`` defends the orchestrator boundary; this is
    defense-in-depth so the worker also fails fast on malicious input.
    """
    if not cid or "\x00" in cid or "\n" in cid or "\r" in cid or "\t" in cid:
        msg = f"chapter_id contains illegal whitespace/NUL: {cid!r}"
        raise WorkerError(msg)
    if len(cid) > MAX_CHAPTER_ID_LEN:
        msg = f"chapter_id too long ({len(cid)} > {MAX_CHAPTER_ID_LEN}): {cid!r}"
        raise WorkerError(msg)
    if "/" in cid or "\\" in cid or cid in {".", ".."} or ".." in cid.split("/") or ".." in cid.split("\\"):
        msg = f"chapter_id contains a path component: {cid!r}"
        raise WorkerError(msg)
    return cid
```

The 8a `Qwen3TTSRunpodHandler._chunk_chapter_id` and the 8b
`GraniteSpeechRunpodHandler.safe_chapter_id` both delegate to this
helper. The helper's tests (`workers/_shared/tests/test_safe_chapter_id.py`)
cover both call sites.

## Deployment Flow

Documented in `workers/granite_speech/README.md`. The deployer **never
builds the worker image** — CI publishes to GHCR.

1. **Pre-warm the RunPod network volume** (one-time, before creating the
   template) by running a one-shot pod that executes:
   ```bash
   huggingface-cli download ibm-granite/granite-speech-4.1-2b \
       --local-dir /runpod-volume/huggingface-cache/hub/models--ibm-granite--granite-speech-4.1-2b
   ```
2. **Tag a release** (`git tag v1.1.0 && git push origin v1.1.0`). The
   `build-workers.yml` workflow builds
   `workers/granite_speech/Dockerfile.runpod` and publishes:
   - `ghcr.io/<repo>/acheron-granite-speech-runpod:latest` (movable)
   - `ghcr.io/<repo>/acheron-granite-speech-runpod:<sha>` (immutable per commit)
   The workflow uses `docker/build-push-action` with
   `cache-from: type=gha` to cache the slow `pip install torch /
   transformers / flash-attn` layers.
3. **Create the RunPod serverless template** referencing the pushed image.
   Set:
   - GPU type list: `[L4]` (24GB, the cheapest 24GB tier per the
     deployer's compute choice; 1 GPU per the deployer's choice).
   - Disk / container disk: ≥ 10 GB.
   - Network volume (from step 1) attached at `/runpod-volume`.
   - Environment variables: see "Environment variables" below.
4. **Create the RunPod serverless endpoint** from the template. Configure:
   - `workers_min: 0`, `workers_max: 1` (sufficient for one book at a
     time; bump for concurrent books).
   - `idle_timeout: 300` (matches the existing cost-containment strategy).
   - Note the endpoint ID.
5. **Run the edge container** (the orchestrator host's `docker-compose.yml`
   adds a `granite-speech-edge` service running the published generic
   `acheron-worker-edge` image):
   ```yaml
   granite-speech-edge:
     image: ghcr.io/<repo>/acheron-worker-edge:latest
     profiles: ["runpod-asr"]
     ports:
       - "8008:8001"
     environment:
       WORKER_NAME: granite_speech
       ACHERON_ORCHESTRATOR_URL: http://orchestrator:8000
       ACHERON_REGISTRATION_TOKEN: ${ACHERON_REGISTRATION_TOKEN}
       ACHERON_WORKER__RUNPOD_API_KEY: ${RUNPOD_API_KEY}
       ACHERON_WORKER__RUNPOD_ENDPOINT_ID: ${GRANITE_SPEECH_RUNPOD_ENDPOINT_ID}
       ACHERON_WORKER__LISTEN_PORT: "8001"
     volumes:
       - ./deploy-overrides/granite_speech.worker.yaml:/app/granite_speech.worker.yaml:ro
     healthcheck:
       test: ["CMD-SHELL", "python", "-c", "import urllib.request; urllib.request.urlopen('http://localhost:8001/health').read()"]
       interval: 30s
       timeout: 5s
       retries: 3
       start_period: 10s
     depends_on:
       orchestrator:
         condition: service_healthy
   ```
   Host port `8008` is the next free port in the existing matrix (the
   stub services use `8001`-`8007`; the qwen3tts edge uses host `8004`).
   Internal container port is `8001` — matches the qwen3tts-edge pattern;
   each container has its own network namespace so the internal port
   doesn't need to be unique.
   The edge registers with the orchestrator, forwards `/execute` calls
   (with multipart input) to RunPod's `/run`, returns the transcript via
   multipart/mixed.
6. **Cold starts**: when no GPU pods are warm, the orchestrator's
   `HealthMonitor` reports the worker as `BOOTING` (via the existing
   `RunPodHealthProvider`), jobs queue at the orchestrator, and RunPod
   scales from zero as the first `/execute` arrives.

### Environment variables

| Variable | Required? | Description |
|----------|-----------|-------------|
| `ACHERON_WORKER__WORKER_ID` | yes (or via worker.yaml) | Worker ID used at registration. Default in worker.yaml: `granite-speech-1`. |
| `ACHERON_WORKER__ORCHESTRATOR_URL` | yes | Orchestrator base URL. |
| `ACHERON_WORKER__REGISTRATION_TOKEN` | env-only | Bearer token used for `POST /workers`. |
| `ACHERON_WORKER__RUNPOD_API_KEY` | env-only | RunPod API key (used by the edge forwarder and by the RunPod price source). |
| `ACHERON_WORKER__RUNPOD_ENDPOINT_ID` | env-only | The RunPod serverless endpoint ID created in step 4 above. |
| `ACHERON_WORKER__EXECUTION_TIMEOUT_S` | optional | Per-job timeout (default 1800s). |
| `ACHERON_WORKER__PRICE_SOURCE` | optional | `runpod` (default) | `static` | `zero`. |
| `ACHERON_WORKER__SECURE_CLOUD` | optional | Quote secure-cloud vs community-cloud RunPod rate (default `false`). |
| `ACHERON_WORKER__LISTEN_PORT` | optional | Edge container listen port (default 8001; matches qwen3tts-edge internal port). |

### Switching GPU types

Same as 8a. Operator runs
`runpodctl serverless update <endpoint-id> --gpu-id <new>` (or uses the
RunPod dashboard), then restarts the edge container (or waits
`price_cache_ttl_s`, default 3600s). The worker re-queries the endpoint's
`gpuIds` via the RunPod GraphQL API and resolves the new
`uninterruptablePrice`. No image rebuild required.

### Local-GPU mode

Not shipped in v1. A `GraniteSpeechLocalHandler` would be a separate
future worker package, not a config knob on this one.

### Languages and variants

`ibm-granite/granite-speech-4.1-2b` supports 6 ASR languages:
`en, fr, de, es, pt, ja`. Punctuation and truecasing for all 6 with the
hardcoded `asr_prompt` ("transcribe the speech with proper punctuation
and capitalization.").

`granite-speech-4.1-2b-nar` (non-autoregressive) is explicitly excluded
by the deployer's choice. `granite-speech-4.1-2b-plus` (speaker-attributed
ASR + word-level timestamps) is deferred to a separate future sub-project.

## GHCR CI Workflow

`.github/workflows/build-workers.yml` (extended):

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
      - 'src/acheron/core/models.py'
      - 'proto/**'
      - 'Dockerfile.edge'
      - 'Dockerfile'
      - '.github/workflows/build-workers.yml'

jobs:
  build-qwen3tts:
    # Job body identical to [Layer 8a spec, "GHCR CI Workflow"](./2026-06-22-layer8a-tts-worker-design.md#ghcr-ci-workflow).
    # Publishes acheron-qwen3tts-runpod:latest and :<sha> from workers/qwen3tts/Dockerfile.runpod.

  build-granite-speech:
    runs-on: ubuntu-latest
    permissions:
      contents: read
      packages: write
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with: { python-version: '3.14' }
      - name: Install uv
        run: pip install uv
      - name: Build acheron wheel
        run: uv build --package acheron --out-dir dist
      - uses: docker/setup-buildx-action@v3
      - uses: docker/login-action@v3
        with:
          registry: ghcr.io
          username: ${{ github.actor }}
          password: ${{ secrets.GITHUB_TOKEN }}
      - uses: docker/build-push-action@v5
        with:
          context: .
          file: workers/granite_speech/Dockerfile.runpod
          push: ${{ github.event_name != 'pull_request' }}
          tags: |
            ghcr.io/${{ github.repository }}/acheron-granite-speech-runpod:latest
            ghcr.io/${{ github.repository }}/acheron-granite-speech-runpod:${{ github.sha }}
          cache-from: type=gha
          cache-to: type=gha,mode=max

  build-edge:
    # Job body identical to [Layer 8a spec, "GHCR CI Workflow"](./2026-06-22-layer8a-tts-worker-design.md#ghcr-ci-workflow).
    # Publishes acheron-worker-edge:latest and :<sha> from Dockerfile.edge.
```

- **Single job per worker.** The matrix grows linearly as new workers
  ship; v1 publishes `acheron-qwen3tts-runpod` (8a) and
  `acheron-granite-speech-runpod` (8b).
- **Pin to `:<sha>`** in the RunPod template for reproducibility; bump to
  `:latest` when ready.
- **GHCR visibility** inherits the repo's — private repos → private images.
- **No local-GPU image is published by v1.**
- **Justfile target:** `just build-worker granite-speech` wraps the local
  `docker build` for dev iteration; CI uses the workflow directly.

## Testing Strategy

### Worker unit tests (`workers/granite_speech/tests/`)

- **`test_capabilities.py`** — `capabilities()` shape:
  `worker_type=ASR`; `supported_languages_in == supported_languages_out ==
  {en, fr, de, es, pt, ja}`; `supported_formats_in == {mp3, wav}`;
  `supported_formats_out == {text}`; `batch_capable=False`;
  `model_source == "huggingface:ibm-granite/granite-speech-4.1-2b"`;
  `metadata["asr_prompt"]` matches the default;
  `metadata["health_provider"] == "runpod"`.
- **`test_handler.py`** — `handle()` with a mocked
  `transformers.AutoModelForSpeechSeq2Seq` (via the `_FakeModel` pattern
  from 8a): one `BytesArtifact` produced per call with `text/plain` +
  correct chapter_id + language metadata. `handle()` with `input=None`
  raises `WorkerError`. `handle()` with an unsupported `source_language`
  raises `WorkerError`. `handle()` with empty audio bytes raises
  `WorkerError`.
- **`test_runpod_entrypoint.py`** — `runpod_entrypoint.main()` calls
  `handler.startup()` then `runpod.serverless.start` with the
  `make_runpod_handler`-wrapped handler; verified via mocks.

The mocked-model test pattern is exactly 8a's `_FakeModel` —
`transformers.AutoModelForSpeechSeq2Seq.from_pretrained` is monkey-patched
to return a stub class whose `generate` returns a fixed token tensor that
decodes to a known string. No `torch` or `transformers` install is
required for the unit test.

### `workers/_shared/tests/test_safe_chapter_id.py`

- Covers `safe_chapter_id` with NUL bytes, path separators, `..`
  components, blank strings, leading/trailing whitespace, oversize ids.
- 8a's `Qwen3TTSRunpodHandler._chunk_chapter_id` is refactored to
  delegate to `safe_chapter_id`; 8a tests continue to pass unchanged
  (same surface).

### Orchestrator transport tests

- **`tests/shell/transports/test_asr_multipart.py` (NEW)** — drives
  `asr_local_stub` end-to-end with multipart input. Uses the stub's
  `StubASRHandler` (updated to accept `Input | None`), confirms the
  response is parsed as `multipart/mixed` and that the `JobResult.outputs`
  contain one `OutputFile` with `content_type="text/plain"`. The
  orchestrator's `HttpWorker._execute_asr_multipart` reads the upstream
  extract output from a `tmp_path` `StepCache`, opens the audio file,
  sends multipart, and asserts the byte-equality of what reaches the
  stub. Includes a 1-test case for a missing extract output
  (`WorkerError`) and a 1-test case for an extract output without an
  audio file (`WorkerError`).
- **`tests/shell/transports/test_http_worker.py` (extended)** — adds a
  1-test case asserting the legacy JSON `HttpWorker.execute()` path (for
  EXTRACTION / CHUNKING / PACKAGING) is unchanged: with those
  `WorkerType` values, the transport sends `json=` and parses the
  response as `multipart/mixed` or `JobResult` JSON without entering
  the new `_execute_with_upstream_input` branch. (The TTS path
  changed in 8c — see the 8c plan's test additions for the new
  TTS / TRANSLATION arms.)
- **`tests/shell/transports/test_step_handler.py` (extended)** — adds a
  1-test case asserting the ASR branch of `HttpWorker.execute()` is
  invoked when `step.type == ASR`.
- **`tests/shell/transports/test_multipart.py` (extended)** — adds
  `_parse_request_multipart` test cases: multipart with JSON part + audio
  part; multipart with no binary part; legacy JSON body (no multipart
  envelope).

### SDK tests (`tests/worker_sdk/`)

- **`test_inputs.py` (NEW)** — `BytesInput.stream()` yields the in-memory
  bytes. `StreamInput.stream()` delegates to the producer. `FileInput.stream()`
  reads the file in 64KiB chunks. The Protocol's `@property` accessors
  return the field values from the frozen dataclass.
- **`test_handler_signature.py` (NEW)** — The `WorkerHandler.handle`
  signature is `(self, job, input=None)`. `Qwen3TTSRunpodHandler` (a
  concrete subclass) accepts both call styles: `handle(job)` and
  `handle(job, input=None)` both compile and dispatch. `asyncio.iscoroutine`
  check on the result for the async contract.
- **`test_edge_http_multipart.py` (NEW)** — The SDK's `/execute` route
  accepts a multipart body, builds the `Job` + `Input`, and passes them to
  the handler. A handler that receives a `BytesInput` with `audio/mpeg`
  content type can stream its bytes. A JSON-only body (no multipart)
  routes to the legacy path. An ASR handler that requires `input`
  receives `None` for a JSON-only request (legacy path).
- **`test_cloud_audio.py` (NEW)** — `_serialise_job_for_runpod` includes
  `input_audio` only when `input is not None`. `make_runpod_handler._rp_handler`
  reads `input_audio`, builds a `BytesInput`, and passes it to
  `handler.handle(job, input)`. Post-8c, TTS and TRANSLATION jobs also
  carry an `input` part (chunks.json) and call
  `handler.handle(job, input=BytesInput(...))`. EXTRACTION / CHUNKING /
  PACKAGING still call `handler.handle(job, input=None)` (legacy path).
  The base64 round-trip preserves bytes.
- **`test_runpod_forwarder.py` (extended)** — `RunPodForwarderHandler.handle`
  forwards `input` to the cloud side via base64; a TTS job (no input)
  continues to work unchanged.

### Stub tests

- **`stubs/_sdk_base/__init__.py`** — `StubASRHandler` accepts
  `input: Input | None = None`. Existing `stubs/tests/test_stubs_healthy.py`
  parameterizes the 7-stub matrix; the ASR stub continues to return a
  canned transcript when the orchestrator sends multipart with an audio
  part (the stub ignores the audio content).
- **`stubs/asr_local_stub/main.py` and `worker.yaml`** — unchanged.

### What we explicitly don't test in 8b

- Real GPU inference (no GPU in CI).
- Real RunPod API calls (mocked by the existing `_FakeRunpodServer`).
- ASR accuracy on a real audio file (model quality; not in scope).
- Real RunPod cold-start timing.

## File Map (Full Change List)

### SDK
- `src/acheron/worker_sdk/__init__.py` — re-exports `BytesInput`,
  `StreamInput`, `FileInput` (the `Input` Protocol stays internal).
- `src/acheron/worker_sdk/inputs.py` (NEW) — `Input` Protocol +
  `BytesInput` / `StreamInput` / `FileInput`.
- `src/acheron/worker_sdk/handler.py` (EXTENDED) — `handle()` gains
  `input: Input | None = None`.
- `src/acheron/worker_sdk/_edge_http.py` (EXTENDED) — `/execute` accepts
  `multipart/form-data` OR `application/json`.
- `src/acheron/worker_sdk/cloud.py` (EXTENDED) — `_serialise_job_for_runpod`
  carries `input_audio`; `make_runpod_handler._rp_handler` deserialises
  it; `RunPodForwarderHandler.handle()` accepts `input` and forwards.
- `tests/worker_sdk/test_inputs.py` (NEW).
- `tests/worker_sdk/test_handler_signature.py` (NEW).
- `tests/worker_sdk/test_edge_http_multipart.py` (NEW).
- `tests/worker_sdk/test_cloud_audio.py` (NEW).
- `tests/worker_sdk/test_runpod_forwarder.py` (EXTENDED).

### Orchestrator transports
- `src/acheron/shell/transports/http.py` (EXTENDED) — `_execute_asr_multipart`
  branch; new `step_cache` keyword-only injection; `HttpWorker.__init__`
  default-constructs `StepCache` from `ACHERON_DATA_DIR` for backward
  compat.
- `src/acheron/shell/transports/_multipart.py` (EXTENDED) —
  `_parse_request_multipart` helper.
- `tests/shell/transports/test_asr_multipart.py` (NEW).
- `tests/shell/transports/test_http_worker.py` (EXTENDED) — backward-compat
  case for the EXTRACTION / CHUNKING / PACKAGING (legacy JSON) path.
  8c adds the new TTS / TRANSLATION arm tests.
- `tests/shell/transports/test_step_handler.py` (EXTENDED) — ASR branch
  routing.
- `tests/shell/transports/test_multipart.py` (EXTENDED) — request parser
  test cases.

### Workers (NEW)
- `workers/_shared.py` (NEW) — `safe_chapter_id` + `MAX_CHAPTER_ID_LEN`.
- `workers/_shared/tests/test_safe_chapter_id.py` (NEW).
- `workers/qwen3tts/handler.py` (EXTENDED, 1-line refactor) —
  `_chunk_chapter_id` delegates to `safe_chapter_id`.
- `workers/granite_speech/handler.py` (NEW) — `GraniteSpeechRunpodHandler`.
- `workers/granite_speech/runpod_entrypoint.py` (NEW).
- `workers/granite_speech/worker.yaml` (NEW) — image default.
- `workers/granite_speech/worker.edge.yaml` (NEW) — edge-side config.
- `workers/granite_speech/Dockerfile.runpod` (NEW).
- `workers/granite_speech/pyproject.toml` (NEW) — workspace member.
- `workers/granite_speech/README.md` (NEW).
- `workers/granite_speech/tests/test_capabilities.py` (NEW).
- `workers/granite_speech/tests/test_handler.py` (NEW).
- `workers/granite_speech/tests/test_runpod_entrypoint.py` (NEW).

### Stubs
- `stubs/_sdk_base/__init__.py` (EXTENDED) — `StubASRHandler.handle`
  accepts `Input | None`.
- `stubs/asr_local_stub/main.py` — unchanged.
- `stubs/asr_local_stub/worker.yaml` — unchanged.

### CI / packaging
- `.github/workflows/build-workers.yml` (EXTENDED) — `build-granite-speech`
  job publishes `acheron-granite-speech-runpod` to GHCR.
- `Justfile` (EXTENDED) — `build-worker <name>` target wraps local
  `docker build` (already in 8a; reused unchanged).
- `pyproject.toml` (EXTENDED) — declare `workers/granite_speech` as a uv
  workspace member.
