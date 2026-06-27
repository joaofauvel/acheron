# Layer 8c — Translation Worker (RunPod Serverless) Design

Third and final Layer 8 sub-project. Replays the Layer 8a TTS blueprint
(`acheron.worker_sdk`) for **translation**: ships the
`workers/translategemma/` worker against `google/translategemma-12b-it`
(12B-param BF16, A40 GPU), deployed as a RunPod Serverless endpoint.

The sub-project also fixes the latent qwen3tts end-to-end gap: the
synthesize step's chunks arrive via a new `HttpWorker` arm (same shape
as ASR's), and the qwen3tts handler is refactored to read chunks from
the `Input` parameter (8b's `BytesInput` Protocol) instead of
`job.payload["chunks"]`.

The sub-project ships one cross-cutting fix to prevent misconfigurations
that would otherwise fail at runtime: a new
`WorkerCapabilities.max_input_tokens` field plus a separate
`validate_chunking_fits_workers` planner function. The orchestrator's
job-submission path calls both `compile_plan` (language path) and
`validate_chunking_fits_workers` (length budget). Misconfigurations
fail at plan compile time, before any GPU time is spent.

The pre-blueprint translation stub spec is superseded by this design.

## Scope

**In scope:**

- A new `WorkerCapabilities.max_input_tokens: int | None = None` field
  on the existing dataclass. `None` = unbounded (ASR, packaging).
  Translation + TTS set it.
- A new `ChunkingTooLongForWorkerError` exception, subclass of
  `InvalidLanguagePathError` so existing handling (job rejection,
  dashboard) still works.
- A new top-level `validate_chunking_fits_workers(capabilities,
  chunking_max_length, chars_per_token=4)` function in
  `acheron.core.planner`. Pure (no I/O, no global state). Caller is
  responsible for passing fresh `capabilities` and the chunking step's
  config.
- A new `Settings.chars_per_token: int = 4` field on the orchestrator
  config. Conservative default overestimates tokens for CJK languages;
  acceptable as a hard ceiling.
- `HttpWorker.execute()` refactored from `if job.job_type ==
  WorkerType.ASR:` to a `match job.job_type` with three arms: `ASR`
  (kept), `TRANSLATION` (new), `TTS` (new). The two new arms share a
  new `_execute_with_upstream_input` helper that loads the upstream
  step's outputs from `StepCache` and POSTs `multipart/form-data` (one
  `application/json` part for the envelope + one binary part for the
  upstream artifact). The old `_execute_asr_multipart` is removed
  (replaced by the helper with ASR's `audio/*` predicate).
- A new workspace member `workers/translategemma/` shipping one
  `TranslateGemmaRunpodHandler` that:
  - Loads `google/translategemma-12b-it` (BF16, A40) eagerly at boot.
  - Reads chunks from the `Input` parameter (8b's `BytesInput` Protocol)
    — JSON-serialised `chunks.json` as a multipart part.
  - Batches through `model.generate()` in passes of `_MAX_BATCH_SIZE = 4`
    to bound VRAM. `max_new_tokens=1024`, `max_input_tokens=2048` (the
    model's 2K context, hardcoded).
  - Returns one `BytesArtifact` per chunk, in order, with
    `filename="{chapter_id}_{seq:04d}.txt"`.
  - Publishes `max_input_tokens=2048` in `capabilities()`.
- `Qwen3TTSRunpodHandler.handle()` refactored to read chunks from
  `input` (parses JSON) instead of `job.payload["chunks"]`. The
  existing `_chunk_text` / `_chunk_chapter_id` helpers stay.
  `capabilities()` also publishes `max_input_tokens=2048` (qwen3-tts is
  also a 2K-context model).
- One config knob: `model_id` (default `google/translategemma-12b-it`).
  The deployer can flip to `google/translategemma-4b-it` without
  rebuilding the image. All other deployment-tunable values
  (`max_new_tokens=1024`, `max_input_tokens=2048`, `_MAX_BATCH_SIZE=4`,
  `quantization: bf16` only) are hardcoded.
- A new compose service `translategemma-edge` under a
  `runpod-translation` profile, using the generic `acheron-worker-edge`
  image (8a CI), configured via `worker.yaml` + env.
- `RunPodHealthProvider` cold-start detection reused unchanged
  (`capabilities.metadata["health_provider"] = "runpod"` +
  `health_endpoint_id`).
- GHCR CI workflow publishes `acheron-translategemma-runpod` on tag
  and `main`.
- Test fixtures update for `workers/qwen3tts/tests/test_handler.py`
  (chunks now arrive via `Input`, not `job.payload`) and
  `test_capabilities.py` (`max_input_tokens=2048`).

**Out of scope** (deferred to separate sub-projects):

- 4B BF16 quantization knobs (no `quantization` knob in v1; the
  deployer flips `model_id` if they want the 4B variant).
- `Unbabel/TowerInstruct-13B-v0.1` literary-tone alternative — separate
  future sub-project.
- AST (speech translation) and image-translation capabilities of
  TranslateGemma — v1 is text-only.
- Per-step worker targeting (the `translation_model` hint parallel to
  the existing-but-no-op `asr_model` field) — deferred per the 8a spec.
- Dynamic tokenizer-based token estimator (v1 uses a constant
  `chars_per_token`; v2 could call the tokenizer for an exact count).
- Local-GPU `TranslateGemmaLocalHandler` — workers commit to one
  deployment mode by being one mode, per the 8a spec.
- Per-chapter parallelism for translation and `workers_max > 1`
  endpoint scaling.

## Approach

Three high-level approaches were considered:

**A) Standalone stack (the pre-blueprint stub spec).** FastAPI + `/health` +
`/capabilities` + `/execute`, self-registration, no SDK reuse. Zero
coupling but bypasses 8a/8b's blueprint, the RunPod forwarder + edge
container + GHCR CI, `CostBasis`, `_shared.safe_chapter_id`. **Rejected**
— explicitly superseded by the 8a spec.

**B) Replay 8a, no 8b Input Protocol.** Chunks arrive via the legacy
`application/json` request body as `job.payload["chunks"]`. ASR stays
on multipart; TTS + translation stay on JSON. Two wire shapes; the
qwen3tts end-to-end gap remains. **Rejected.**

**C) Replay 8a + extend 8b's Input Protocol (chosen).** Chunks flow
as a multipart `Input` part (8b's `BytesInput`, `application/json`
content-type, `chunks.json` bytes). `HttpWorker.execute()` refactored
to a `match` on `WorkerType`, with arms for `ASR` (existing), `TTS`
(new — patches the qwen3tts end-to-end gap), and `TRANSLATION` (new).
`Qwen3TTSRunpodHandler.handle()` refactored to read from `input`.
ASR + TTS + translation share the same wire shape. The cross-cutting
`max_input_tokens` + `validate_chunking_fits_workers` fix prevents the
chunking-vs-worker misconfig footgun that would otherwise surface at
runtime.

## Repository Layout

Single `acheron` wheel. The new worker is a top-level package outside
the `acheron` import tree. The orchestrator's `HttpWorker` is the only
new call site that learns about the translate step's upstream
chunking output. The planner gains one new pure function; the
orchestrator's submit path calls both `compile_plan` and
`validate_chunking_fits_workers`.

```
src/
  acheron/
    core/
      errors.py            # EXTENDED: ChunkingTooLongForWorkerError
      models.py            # EXTENDED: WorkerCapabilities.max_input_tokens
      planner.py           # EXTENDED: validate_chunking_fits_workers()
    shell/
      config.py            # EXTENDED: Settings.chars_per_token
      orchestrator.py      # EXTENDED: submit path calls both
      transports/
        http.py            # EXTENDED: match on WorkerType; ASR | TRANSLATION | TTS arms
  workers/
    translategemma/        # NEW worker
      handler.py
      runpod_entrypoint.py
      worker.yaml          # image default for the RunPod runtime
      worker.edge.yaml     # edge-side config (RunPodForwarderHandler)
      Dockerfile.runpod
      pyproject.toml
      README.md
      tests/
    qwen3tts/              # EXTENDED
      handler.py           # reads chunks from input
      tests/
        test_handler.py    # updated fixtures
        test_capabilities.py # max_input_tokens=2048
    _shared.py             # unchanged
.github/workflows/
  build-workers.yml        # EXTENDED: + build-translategemma
docker-compose.yml         # EXTENDED: + translategemma-edge under runpod-translation
```

**Import boundaries** (no new import-linter contracts needed; the
existing 8a/8b boundaries cover 8c):

- `acheron.core` and `acheron.shell` retain their existing boundaries.
- `workers.* -> acheron.worker_sdk, acheron.core` (allowed).
- `workers.* -/-> acheron.shell` (forbidden).

## Deployment Topology

Same as 8a / 8b: the model lives inside the RunPod serverless endpoint;
a GPU-less edge container bridges the orchestrator's HTTP-worker
protocol and RunPod's `/run` + `/status` + `/cancel` job protocol.

| Image | Where it runs | Contains | Published by |
|---|---|---|---|
| `acheron-translategemma-runpod` | inside the RunPod serverless endpoint (cloud) | model + `TranslateGemmaRunpodHandler` + `runpod.serverless.start(...)` | GHCR by CI (this sub-project) |
| `acheron-worker-edge` | alongside the orchestrator (compose service) | FastAPI app + RunPod forwarder + registration client; no GPU | GHCR by CI (8a, reused) |

The edge container is **generic across all workers** — same image for
TTS / ASR / translation, only `worker.yaml` + env differ per service.
The deployer's deploy surface is `docker-compose.yml` (service entry
present in the main compose) + `.env` (RunPod endpoint ID, API key,
registration token). The user does not clone the repo or build
anything.

The orchestrator communicates with the worker through the `/execute`
endpoint. 8c adds a `TRANSLATION` arm to `HttpWorker.execute()` that
POSTs `multipart/form-data` (one `application/json` part for the
`ExecuteRequest` envelope + one binary part for the upstream
`chunks.json`).

## Orchestrator-Side Changes

### `WorkerCapabilities.max_input_tokens`

```python
# src/acheron/core/models.py (EXTENDED)

@dataclass(frozen=True)
class WorkerCapabilities:
    """..."""
    worker_type: WorkerType
    supported_languages_in: frozenset[str]
    supported_languages_out: frozenset[str]
    supported_formats_in: frozenset[str]
    supported_formats_out: frozenset[str]
    max_payload_bytes: int | None
    batch_capable: bool
    model_source: str | None
    max_input_tokens: int | None = None  # NEW: per-chunk input token limit; None = unbounded
    metadata: dict[str, JsonValue] = field(default_factory=dict)
```

Default `None` = unbounded. ASR / packaging omit it. Translation + TTS
set it.

### `ChunkingTooLongForWorkerError`

```python
# src/acheron/core/errors.py (EXTENDED)

class ChunkingTooLongForWorkerError(InvalidLanguagePathError):
    """Chunking step's max_chunk_length exceeds a text-input worker's max_input_tokens.

    Raised at plan compile time so misconfigurations fail fast, before any GPU time.
    Subclass of InvalidLanguagePathError so existing handling (job rejection, dashboard)
    still works.
    """
```

### `validate_chunking_fits_workers`

```python
# src/acheron/core/planner.py (EXTENDED)

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
```

`compile_plan` keeps its existing shape — no `chunking_max_length`
parameter. The chunking check is the caller's responsibility. The
caller (orchestrator submit path) calls both:

```python
# src/acheron/shell/orchestrator.py (EXTENDED)

def submit_job(request, ...):
    caps = registry.list_all()
    plan = compile_plan(request, strategy, caps)            # language path
    validate_chunking_fits_workers(                         # length budget
        caps,
        chunking_max_length=self._settings.workers.chunking.max_chunk_length,
        chars_per_token=self._settings.chars_per_token,
    )
    # ... persist + dispatch ...
```

`Settings.chars_per_token: int = 4` defaults to 4 and lives at the top
level of `Settings`. The `chunking.max_chunk_length` already exists
on the chunking local-handler config (`settings.workers.chunking.`) —
the orchestrator reads it from there.

### `HttpWorker.execute()` — match-based dispatch

```python
# src/acheron/shell/transports/http.py (EXTENDED)

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

The new helper `_execute_with_upstream_input` factors out the ASR /
TRANSLATION / TTS arms:

```python
# `Callable` import added in 8c for `_execute_with_upstream_input`.
from collections.abc import Callable


async def _execute_with_upstream_input(
    self,
    job: Job,
    *,
    upstream_step: str,
    content_type_predicate: Callable[[str], bool],
    form_field: str,
) -> JobResult:
    """Load the upstream step's outputs from StepCache and POST multipart to the worker.

    The orchestrator's ``StepCache`` holds the manifest of the previous step's
    outputs. We find the first ``OutputFile`` whose ``content_type`` matches
    ``content_type_predicate`` and POST ``multipart/form-data`` with one
    ``application/json`` part (the ``ExecuteRequest`` envelope) + one binary
    part (the upstream artifact) under ``form_field``. The worker's response
    is ``multipart/mixed`` and is parsed the same way as the legacy JSON path.

    ASR uses ``upstream_step="extract"``,
    ``content_type_predicate=lambda c: c.startswith("audio/")``, and
    ``form_field="audio"``. TRANSLATION and TTS use
    ``upstream_step="chunk"``,
    ``content_type_predicate=lambda c: c == "application/json"``, and
    ``form_field="chunks"``. The form field name is documented for
    symmetry with the worker's `multipart/form-data` part name; the
    SDK's multipart request parser keys off ``content_type``, not
    field name, so the field name is a convention rather than a
    contract.
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
        form_field: (
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

The old `_execute_asr_multipart` is removed; ASR now goes through
`_execute_with_upstream_input` with `upstream_step="extract"` and the
`audio/*` predicate. All existing ASR tests continue to pass.

The new TTS + TRANSLATION arms use the same helper with
`upstream_step="chunk"` and the `application/json` predicate. The
chunks.json file the orchestrator materialised during the chunking
step is the upstream artifact; the worker reads it from the multipart
`Input` part.

### `runpod_entrypoint.py` and edge transport

The `RunPodForwarderHandler` (8a) and `make_runpod_handler` (8b, which
extends to carry `Input` over RunPod's `/run` wire) are reused
unchanged. The translate worker's `Input` is a `BytesInput` with
`content_type="application/json"` and the chunks.json bytes. The
forwarder base64-encodes the bytes into RunPod's JSON `/run` wire
(established in 8b); the cloud-side `make_runpod_handler` decodes
back into a `BytesInput` and passes it to `TranslateGemmaRunpodHandler.handle()`.

### `GrpcWorker` — unchanged

The `Artifact` mode (8a) and the legacy `pcm_data` mode are both
preserved. Translation doesn't use gRPC in 8c; the v1 path is HTTP
only. A future sub-project could wire gRPC for translation; the
gRPC contract is ready.

### Cold-start detection — unchanged

The existing `RunPodHealthProvider` (Layer 11) and
`HealthMonitor._handle_failure` consume
`metadata["health_provider"]` + `metadata["health_endpoint_id"]`.
The SDK's `register_with_orchestrator` tags RunPod workers'
capabilities with these; cold-start detection works out of the box.

### Cost aggregation — no orchestrator change

`PlanResult.total_cost` already sums `JobMetrics.cost_estimate` per
step. `total_cost_basis` aggregates across steps via
`aggregate_cost_basis` (8a introduced). The translation step emits a
per-step `cost_estimate` (RunPod-derived) + `cost_basis` exactly like
8a TTS / 8b ASR.

## The TranslateGemma RunPod Worker

### Model

**`google/translategemma-12b-it`** (12B params / 13B total, BF16
safetensors, Gemma terms).

- Built on Gemma 3, fine-tuned for translation via SFT + RL
  distillation from Gemini. 69 languages (ISO 639-1 alpha-2; the
  full set in the v1 implementation is listed in
  `workers/translategemma/handler.py:_SUPPORTED_LANGS`).
- Model class: `AutoModelForImageTextToText` (it's a VLM in HF
  taxonomy, not pure causal LM). Processor: `AutoProcessor`.
- 2K-token input context (hardcoded; the model's stated limit).
- `apply_chat_template` with the strict content-list format. The model
  is opinionated: only User / Assistant roles, content must be a list
  with exactly one entry
  `{"type": "text", "source_lang_code", "target_lang_code", "text"}`
  (or `{"type": "image", ...}` for image translation — not used in
  v1).

**VRAM and quant:**

- 12B BF16 = ~24 GB weights; ~26-28 GB with KV cache and activations.
- **Requires A40 (48 GB).** A single A40 endpoint per the deployer's
  compute choice. `RunPodPrice` auto-discovers the GPU from the
  endpoint's `gpuIds`; switching GPU type takes effect on the next
  price refresh with no image rebuild.
- v1 has no `quantization` knob — BF16 is the only path. A future
  sub-project could add a `quantization: none | bitsandbytes_4bit`
  knob if the deployer wants to run on smaller GPUs.

**Variants:**

- 4B variant (`google/translategemma-4b-it`) — same `apply_chat_template`
  contract, smaller model. Deployer flips the `model_id` knob; no code
  change.

### `handler.py`

```python
"""RunPod Serverless handler for google/translategemma-12b-it.

This module runs **inside the RunPod serverless runtime image** (see
``Dockerfile.runpod``). The cloud-side ``runpod_entrypoint.py`` imports
``TranslateGemmaRunpodHandler`` here, calls ``startup()`` eagerly at
boot, then ``runpod.serverless.start({"handler": make_runpod_handler(handler)})``.

A local-GPU fallback handler (``TranslateGemmaLocalHandler``) is
deferred to a separate future worker package — workers commit to one
deployment mode by being one mode, per the Layer 8a spec.
"""

from __future__ import annotations

import asyncio
import json
from typing import TYPE_CHECKING, Any

from acheron.core.errors import WorkerError
from acheron.core.models import Job, JsonValue, WorkerCapabilities, WorkerType
from acheron.worker_sdk.artifacts import Artifact, BytesArtifact
from acheron.worker_sdk.handler import WorkerHandler
from acheron.worker_sdk.inputs import Input
from workers._shared import safe_chapter_id

if TYPE_CHECKING:
    from acheron.worker_sdk.settings import WorkerSettings


_MODEL_ID_DEFAULT = "google/translategemma-12b-it"
_MAX_INPUT_TOKENS = 2048
_MAX_BATCH_SIZE = 4
_MAX_NEW_TOKENS = 1024
# All 69 ISO 639-1 alpha-2 codes TranslateGemma supports. v1 advertises
# the full set so the orchestrator can plan any pair; language-path
# validation at plan compile time still rejects pairs outside
# SUPPORTED_LANGUAGES.
_SUPPORTED_LANGS = frozenset({
    "af", "am", "ar", "az", "be", "bg", "bn", "bs", "ca", "cs",
    "cy", "da", "de", "el", "en", "es", "et", "fa", "fi", "fr",
    "ga", "gl", "gu", "he", "hi", "hr", "hu", "hy", "id", "is",
    "it", "ja", "ka", "kk", "km", "kn", "ko", "ky", "lo", "lt",
    "lv", "mk", "ml", "mn", "mr", "ms", "my", "ne", "nl", "no",
    "pa", "pl", "pt", "ro", "ru", "si", "sk", "sl", "sr", "sv",
    "sw", "ta", "te", "th", "tr", "uk", "ur", "vi", "zh",
})


class TranslateGemmaRunpodHandler(WorkerHandler):
    """Cloud-side handler run inside the RunPod serverless runtime image."""

    def __init__(self, settings: WorkerSettings) -> None:
        self._settings = settings
        # The model + processor are typed loosely so the workspace tests
        # don't need torch or transformers installed.
        self._model: Any = None
        self._processor: Any = None

    def capabilities(self) -> WorkerCapabilities:
        """Return the worker's static description. No I/O — sync."""
        model_id = self._settings.model_id or _MODEL_ID_DEFAULT
        metadata: dict[str, JsonValue] = {
            "health_provider": "runpod",
        }
        return WorkerCapabilities(
            worker_type=WorkerType.TRANSLATION,
            supported_languages_in=_SUPPORTED_LANGS,
            supported_languages_out=_SUPPORTED_LANGS,
            supported_formats_in=frozenset({"text"}),
            supported_formats_out=frozenset({"text"}),
            max_payload_bytes=None,
            batch_capable=True,
            max_input_tokens=_MAX_INPUT_TOKENS,
            model_source=f"huggingface:{model_id}",
            metadata=metadata,
        )

    async def startup(self) -> None:
        """Eagerly load the model + processor at container boot."""
        import torch  # noqa: PLC0415

        def _load() -> None:
            from transformers import (  # noqa: PLC0415
                AutoModelForImageTextToText,
                AutoProcessor,
            )

            model_id = self._settings.model_id or _MODEL_ID_DEFAULT
            self._processor = AutoProcessor.from_pretrained(model_id)
            self._model = AutoModelForImageTextToText.from_pretrained(
                model_id,
                device_map="cuda:0",
                torch_dtype=torch.bfloat16,
            )

        await asyncio.to_thread(_load)

    async def shutdown(self) -> None:
        """Release GPU memory on edge-shutdown."""
        if self._model is not None:
            del self._model
            self._model = None
        if self._processor is not None:
            del self._processor
            self._processor = None
        import torch  # noqa: PLC0415

        torch.cuda.empty_cache()

    async def handle(self, job: Job, input: Input | None = None) -> list[Artifact]:  # noqa: A002
        """Translate the chunks from ``input`` (chunks.json as a multipart part)."""
        if self._model is None or self._processor is None:
            msg = "TranslateGemma model not loaded (startup() not run)"
            raise WorkerError(msg)
        if input is None:
            msg = "TranslateGemma requires a chunks.json input (multipart part)"
            raise WorkerError(msg)
        src = _require_str(job.payload, "source_language")
        tgt = _require_str(job.payload, "target_language")
        if src not in _SUPPORTED_LANGS:
            msg = f"Unsupported source language: {src!r}"
            raise WorkerError(msg)
        if tgt not in _SUPPORTED_LANGS:
            msg = f"Unsupported target language: {tgt!r}"
            raise WorkerError(msg)

        chunks_json_bytes = b"".join([chunk async for chunk in input.stream()])
        if not chunks_json_bytes:
            return []
        try:
            chunks_raw = json.loads(chunks_json_bytes.decode("utf-8"))
        except (json.JSONDecodeError, UnicodeDecodeError) as exc:
            msg = f"chunks.json is not valid JSON: {exc}"
            raise WorkerError(msg) from exc
        if not isinstance(chunks_raw, list):
            msg = "chunks.json must be a JSON array of chunk dicts"
            raise WorkerError(msg)

        chunks = [_normalize_chunk(c) for c in chunks_raw]
        if not chunks:
            return []

        translated = await asyncio.to_thread(self._translate_all, chunks, src, tgt)

        model_id = self._settings.model_id or _MODEL_ID_DEFAULT
        artifacts: list[Artifact] = []
        for c, t in zip(chunks, translated, strict=True):
            chapter_id = safe_chapter_id(c["chapter_id"])
            artifacts.append(
                BytesArtifact(
                    filename=f"{chapter_id}_{c['sequence_id']:04d}.txt",
                    content_type="text/plain",
                    data=t.encode("utf-8"),
                    metadata={
                        "chapter_id": chapter_id,
                        "sequence_id": c["sequence_id"],
                        "source_language": src,
                        "target_language": tgt,
                        "model": model_id,
                    },
                )
            )
        return artifacts

    def _translate_all(
        self,
        chunks: list[dict[str, Any]],
        src: str,
        tgt: str,
    ) -> list[str]:
        """Run TranslateGemma in passes of _MAX_BATCH_SIZE; return translated strings in order."""
        out: list[str] = []
        for start in range(0, len(chunks), _MAX_BATCH_SIZE):
            batch = chunks[start : start + _MAX_BATCH_SIZE]
            out.extend(self._translate_batch(batch, src, tgt))
        return out

    def _translate_batch(
        self,
        batch: list[dict[str, Any]],
        src: str,
        tgt: str,
    ) -> list[str]:
        """Translate one batch (up to _MAX_BATCH_SIZE chunks) in a single model.generate call."""
        import torch  # noqa: PLC0415

        messages_per_chunk = [
            [
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "text",
                            "source_lang_code": src,
                            "target_lang_code": tgt,
                            "text": c["text"],
                        }
                    ],
                }
            ]
            for c in batch
        ]
        prompts = [
            self._processor.apply_chat_template(m, tokenize=False, add_generation_prompt=True)
            for m in messages_per_chunk
        ]
        # Gemma 3 variants don't always set pad_token_id; fall back to eos.
        tokenizer = self._processor.tokenizer
        if tokenizer.pad_token_id is None and tokenizer.eos_token_id is not None:
            tokenizer.pad_token_id = tokenizer.eos_token_id
        inputs = self._processor(
            text=prompts,
            return_tensors="pt",
            padding=True,
            truncation=True,
            max_length=_MAX_INPUT_TOKENS,
        ).to("cuda:0")
        with torch.inference_mode():
            outputs = self._model.generate(
                **inputs, max_new_tokens=_MAX_NEW_TOKENS, do_sample=False
            )
        decoded: list[str] = []
        for i in range(len(batch)):
            prompt_len = int(inputs["attention_mask"][i].sum())
            new_tokens = outputs[i, prompt_len:]
            text = self._processor.decode(new_tokens, skip_special_tokens=True).strip()
            decoded.append(text)
        return decoded


def _require_str(payload: dict[str, JsonValue], key: str) -> str:
    """Read a required string field from a job payload; raise WorkerError on missing/wrong type."""
    v = payload.get(key)
    if not isinstance(v, str):
        msg = f"{key} is required and must be a str (got {type(v).__name__ if v is not None else 'missing'})"
        raise WorkerError(msg)
    return v


def _normalize_chunk(c: object) -> dict[str, Any]:
    """Validate and normalise a single chunk dict from chunks.json.

    Requires: ``chapter_id`` (str), ``sequence_id`` (int), ``text`` (str).
    Returns a plain dict usable by ``_translate_batch``.
    """
    if not isinstance(c, dict):
        msg = f"chunk must be a dict, got {type(c).__name__}"
        raise WorkerError(msg)
    if "chapter_id" not in c or not isinstance(c["chapter_id"], str):
        msg = "chunk.chapter_id is required (str)"
        raise WorkerError(msg)
    if "sequence_id" not in c or not isinstance(c["sequence_id"], int):
        msg = "chunk.sequence_id is required (int)"
        raise WorkerError(msg)
    if "text" not in c or not isinstance(c["text"], str):
        msg = "chunk.text is required (str)"
        raise WorkerError(msg)
    return {"chapter_id": c["chapter_id"], "sequence_id": c["sequence_id"], "text": c["text"]}
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
from workers.translategemma.handler import TranslateGemmaRunpodHandler

logging.basicConfig(level=logging.INFO)
logging.getLogger("transformers").setLevel(logging.WARNING)
logging.getLogger("urllib3").setLevel(logging.WARNING)
logging.getLogger("httpx").setLevel(logging.WARNING)
logger = logging.getLogger(__name__)


def main() -> None:
    """Boot the RunPod serverless worker: load model, then serve."""
    settings = load_settings()
    handler = TranslateGemmaRunpodHandler(settings)
    asyncio.run(handler.startup())
    runpod.serverless.start({"handler": make_runpod_handler(handler)})


if __name__ == "__main__":
    main()
```

### `worker.yaml` (image default)

```yaml
# TranslateGemma worker — image default config.
# Sensitive fields (RUNPOD_API_KEY, RUNPOD_ENDPOINT_ID, REGISTRATION_TOKEN)
# are env-only — rejected when present here. Override per-deploy by mounting
# a translategemma.worker.yaml override or by setting ACHERON_WORKER_* env vars.

worker_id: "translategemma-1"
# Default to TLS — the orchestrator serves HTTPS in the default compose stack
# (Layer 7c). The compose service overrides via env at deploy time.
orchestrator_url: "https://orchestrator:8000"
listen_port: 8001
execution_timeout_s: 1800

# Pricing — RunPod GraphQL API is the default. The GPU type is NOT a config
# field: RunPodPrice reads the endpoint's gpuIds via the RunPod GraphQL API.
# The deployer provisions a single A40 endpoint; changing GPU on the RunPod
# endpoint takes effect on the next price_cache_ttl_s refresh; no image
# rebuild required.
price_source: runpod
secure_cloud: false

# Output transport — multipart default (output side; input is implicit
# from job_type == TRANSLATION).
output_mode: multipart

# Handler — used by the generic acheron-worker-edge CLI to import the handler
# class when running the edge container alongside the orchestrator. The
# runpod_entrypoint.py in the RunPod runtime image uses the import directly.
handler: "workers.translategemma.handler:TranslateGemmaRunpodHandler"
# TranslateGemma-12B by default. Flip to "google/translategemma-4b-it" if
# the deployer wants the smaller variant. The handler auto-loads whatever
# model_id is set here; no image rebuild required.
model_id: "google/translategemma-12b-it"
```

### `worker.edge.yaml` (edge-side)

```yaml
# Edge-side worker config for the acheron-worker-edge image.
# Same shape as workers/qwen3tts/worker.edge.yaml and
# workers/granite_speech/worker.edge.yaml — phantom_handler is the
# cloud-side TranslateGemmaRunpodHandler, which the edge imports to read
# its static capabilities() without loading the model.

worker_id: "translategemma-edge"
orchestrator_url: "http://orchestrator:8000"
listen_port: 8001
execution_timeout_s: 1800

handler: "acheron.worker_sdk.cloud:RunPodForwarderHandler"
phantom_handler: "workers.translategemma.handler:TranslateGemmaRunpodHandler"
model_id: "google/translategemma-12b-it"

price_source: runpod
secure_cloud: false

# Edge transport is always HTTP multipart on the output side; the input
# side is multipart (form-data) when job_type in {ASR, TRANSLATION, TTS},
# JSON otherwise.
output_mode: multipart
```

### `Dockerfile.runpod`

```dockerfile
# RunPod Serverless runtime image for google/translategemma-12b-it.
#
# Built from the repo root with:
#   docker build -f workers/translategemma/Dockerfile.runpod -t acheron-translategemma-runpod .
#
# CI publishes this image to ghcr.io via .github/workflows/build-workers.yml.

FROM python:3.14-slim AS runpod-runtime

ENV PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    HF_HUB_OFFLINE=1 \
    TRANSFORMERS_OFFLINE=1

# OS deps: git for any future source builds. text-only model — no soundfile,
# no ffmpeg, no flash-attn.
RUN apt-get update && apt-get install -y --no-install-recommends \
        git \
    && rm -rf /var/lib/apt/lists/*

# PyTorch first — matches the CUDA version the host passes via --gpus.
# Pin to 2.5.1 + cu121; transformers 4.52.1+ requires a torch ABI in this range.
RUN pip install --no-cache-dir torch==2.5.1 \
        --index-url https://download.pytorch.org/whl/cu121

# Install the acheron wheel (built from the monorepo) — provides acheron.worker_sdk.
COPY dist/acheron-*.whl /tmp/
RUN pip install /tmp/acheron-*.whl && rm /tmp/acheron-*.whl

# Worker deps — transformers + accelerate. transformers >= 4.52.1 is required
# for native TranslateGemma support. hf-transfer is intentionally NOT
# installed: the runtime image is offline and never re-downloads.
RUN pip install --no-cache-dir \
        "transformers>=4.52.1" \
        accelerate

# RunPod SDK so runpod.serverless.start() is importable.
RUN pip install --no-cache-dir runpod

WORKDIR /app

# The worker's deployable assets. The cloud-side handler is entrypoint-only.
# Paths match what runpod_entrypoint.py imports:
#   `from workers.translategemma.handler import TranslateGemmaRunpodHandler`.
COPY workers/translategemma/handler.py /app/workers/translategemma/handler.py
COPY workers/translategemma/runpod_entrypoint.py /app/runpod_entrypoint.py
COPY workers/translategemma/worker.yaml /app/worker.yaml
COPY workers/__init__.py /app/workers/__init__.py
COPY workers/translategemma/__init__.py /app/workers/translategemma/__init__.py
COPY workers/_shared.py /app/workers/_shared.py

# HF cache lives on the RunPod network volume; offline mode forces the cached snapshot.
ENV HF_HOME=/runpod-volume/huggingface-cache \
    PYTHONPATH=/app

CMD ["python", "runpod_entrypoint.py"]
```

**Network volume for cached weights** (avoids re-downloading ~26GB on
every cold start): RunPod serverless mounts a network volume at
`/runpod-volume`. Weights cache lives at
`/runpod-volume/huggingface-cache`. `HF_HOME` points there;
`HF_HUB_OFFLINE=1` + `TRANSFORMERS_OFFLINE=1` makes transformers
prefer cached weights (matches RunPod's documented model-caching
pattern).

Deployer pre-warms the volume once via a one-shot pod that runs (with
`hf-transfer` installed for the parallel-chunk speedup, since
pre-warm is the only moment the runtime image touches the network):
```bash
pip install "huggingface_hub[cli]" hf-transfer
HF_HUB_ENABLE_HF_TRANSFER=1 huggingface-cli download \
    google/translategemma-12b-it \
    --local-dir /runpod-volume/huggingface-cache/hub/models--google--translategemma-12b-it
```

`HF_HUB_ENABLE_HF_TRANSFER=1` is a pre-warm-only concern; it is not
set in the runtime image Dockerfile because the runtime image is
offline (`HF_HUB_OFFLINE=1`) and never re-downloads.

### Capabilities sent at registration (from the edge)

```json
{
  "worker_id": "translategemma-1",
  "endpoint": "http://translategemma-edge:8001",
  "transport": "http",
  "capabilities": {
    "worker_type": "translation",
    "supported_languages_in": ["af", "am", "ar", "az", "be", "bg", "bn", "bs", "ca", "cs", "cy", "da", "de", "el", "en", "es", "et", "fa", "fi", "fr", "ga", "gl", "gu", "he", "hi", "hr", "hu", "hy", "id", "is", "it", "ja", "ka", "kk", "km", "kn", "ko", "ky", "lo", "lt", "lv", "mk", "ml", "mn", "mr", "ms", "my", "ne", "nl", "no", "pa", "pl", "pt", "ro", "ru", "si", "sk", "sl", "sr", "sv", "sw", "ta", "te", "th", "tr", "uk", "ur", "vi", "zh"],
    "supported_languages_out": ["af", "am", "ar", "az", "be", "bg", "bn", "bs", "ca", "cs", "cy", "da", "de", "el", "en", "es", "et", "fa", "fi", "fr", "ga", "gl", "gu", "he", "hi", "hr", "hu", "hy", "id", "is", "it", "ja", "ka", "kk", "km", "kn", "ko", "ky", "lo", "lt", "lv", "mk", "ml", "mn", "mr", "ms", "my", "ne", "nl", "no", "pa", "pl", "pt", "ro", "ru", "si", "sk", "sl", "sr", "sv", "sw", "ta", "te", "th", "tr", "uk", "ur", "vi", "zh"],
    "supported_formats_in": ["text"],
    "supported_formats_out": ["text"],
    "max_payload_bytes": null,
    "batch_capable": true,
    "max_input_tokens": 2048,
    "model_source": "huggingface:google/translategemma-12b-it",
    "metadata": {}
  }
}
```

`max_input_tokens` is a first-class field on `WorkerCapabilities` (8c);
`max_batch_size` is intentionally NOT a config knob or metadata field
(YAGNI — the deployer never overrides the hardcoded `_MAX_BATCH_SIZE = 4`).
`health_provider` and `health_endpoint_id` are added by the SDK layer
at registration time (`app._registration_caps`) from settings; the
worker's static `capabilities()` does not include them (matches the
qwen3tts pattern).

The full 69-language set is advertised so the orchestrator can plan any
pair; language-path validation at plan compile time still rejects
pairs outside `SUPPORTED_LANGUAGES = {en, es, fr, de}` (the
orchestrator's current set). The `max_input_tokens` field surfaces
the per-chunk token limit so the planner's
`validate_chunking_fits_workers` check works at plan compile time.

### Language implications for the planner

The translate step's `source_language` and `target_language` must each
be one of the orchestrator's `SUPPORTED_LANGUAGES = {en, es, fr, de}`
(unchanged from 8a). Cross-language jobs from a source outside this
set (e.g., `zh → en` translation) are rejected at plan compilation
with the existing `InvalidLanguagePathError` — no GPU time is spent,
per the design spec's "Invalid language path" row in the
error-handling table. The worker's 69-language capability is latent
and unused in v1.

## The Qwen3TTS Patch (8c retroactive)

The qwen3tts end-to-end integration has been a latent gap: the
handler reads `job.payload["chunks"]`, but no orchestrator code
injects the upstream chunking step's chunks into the synthesize
step's payload. 8c closes this gap.

### `Qwen3TTSRunpodHandler.handle()`

```python
# workers/qwen3tts/handler.py (REFACTORED — read chunks from input)

async def handle(self, job: Job, input: Input | None = None) -> list[Artifact]:  # noqa: A002
    """Run batched custom-voice inference for all chunks in the job.

    Chunks arrive via the ``input`` parameter (8b's ``BytesInput``
    Protocol): JSON-serialised ``chunks.json`` from the upstream
    chunking step. ``input`` is required; chunks in ``job.payload``
    is no longer a supported path (the previous behaviour was a
    latent gap — no orchestrator code injected chunks into the
    synthesize step's payload).
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
    # ... rest unchanged ...
```

### `Qwen3TTSRunpodHandler.capabilities()`

```python
# workers/qwen3tts/handler.py (EXTENDED — max_input_tokens=2048)

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
        max_input_tokens=2048,  # NEW — qwen3-tts is also a 2K-context model
        model_source=f"huggingface:{_MODEL_ID}",
        metadata=metadata,
    )
```

### Test fixture updates

`workers/qwen3tts/tests/test_handler.py` — `_build_job` no longer
takes `chunks`; tests construct a `BytesInput` with `chunks.json`
bytes:

```python
def _build_input(chunks: list[dict[str, Any]]) -> BytesInput:
    """Build a BytesInput carrying chunks.json for handler.handle() tests."""
    return BytesInput(
        content_type="application/json",
        data=json.dumps(chunks).encode("utf-8"),
    )


def _build_job(target_language: str = "en", chunks: list[dict[str, Any]] | None = None) -> Job:
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


# Test body pattern (existing tests update analogously):
@pytest.mark.asyncio
async def test_handle_returns_bytes_artifacts_in_order(self) -> None:
    h = Qwen3TTSRunpodHandler(_settings())
    h._model = _FakeModel(wavs=[...], sr=22050)
    job = _build_job(target_language="en")
    chunks = [
        {"chapter_id": "ch1", "sequence_id": 0, "text": "hello"},
        {"chapter_id": "ch1", "sequence_id": 1, "text": "world"},
    ]
    out = await h.handle(job, input=_build_input(chunks))
    assert len(out) == 2
    # ... rest unchanged ...
```

The "no chunks" test passes `BytesInput` with empty bytes; the
"empty list" test passes a `BytesInput` with `[]` JSON. The
malformed-input tests construct a `BytesInput` with non-JSON bytes.

## `workers/_shared.py` (unchanged)

The 8b-introduced `safe_chapter_id` helper is reused unchanged by
`TranslateGemmaRunpodHandler`. The new worker's tests cover both
call sites (qwen3tts + translategemma + granite-speech).

## Deployment Flow

Documented in `workers/translategemma/README.md`. The deployer
**never builds the worker image** — CI publishes to GHCR.

1. **Pre-warm the RunPod network volume** (one-time, before creating
   the template) by running a one-shot pod that executes:
   ```bash
   huggingface-cli download google/translategemma-12b-it \
       --local-dir /runpod-volume/huggingface-cache/hub/models--google--translategemma-12b-it
   ```
2. **Tag a release** (`git tag v1.2.0 && git push origin v1.2.0`).
   The `build-workers.yml` workflow builds
   `workers/translategemma/Dockerfile.runpod` and publishes:
   - `ghcr.io/<repo>/acheron-translategemma-runpod:latest` (movable)
   - `ghcr.io/<repo>/acheron-translategemma-runpod:<sha>` (immutable
     per commit)
   The workflow uses `docker/build-push-action` with
   `cache-from: type=gha` to cache the slow `pip install torch /
   transformers` layers.
3. **Create the RunPod serverless template** referencing the pushed
   image. Set:
   - GPU type list: `[A40]` (48GB, the only tier that fits 12B BF16).
   - Disk / container disk: ≥ 10 GB.
   - Network volume (from step 1) attached at `/runpod-volume`.
   - Environment variables: see "Environment variables" below.
4. **Create the RunPod serverless endpoint** from the template.
   Configure:
   - `workers_min: 0`, `workers_max: 1` (sufficient for one book at
     a time; bump for concurrent books).
   - `idle_timeout: 300` (matches the existing cost-containment
     strategy).
   - Note the endpoint ID.
5. **Run the edge container** (the orchestrator host's
   `docker-compose.yml` adds a `translategemma-edge` service running
   the published generic `acheron-worker-edge` image):
   ```yaml
   translategemma-edge:
     image: ghcr.io/<repo>/acheron-worker-edge:latest
     profiles: ["runpod-translation"]
     ports:
       - "8009:8001"
     environment:
       WORKER_NAME: translategemma
       ACHERON_ORCHESTRATOR_URL: http://orchestrator:8000
       ACHERON_REGISTRATION_TOKEN: ${ACHERON_REGISTRATION_TOKEN}
       ACHERON_WORKER__RUNPOD_API_KEY: ${RUNPOD_API_KEY}
       ACHERON_WORKER__RUNPOD_ENDPOINT_ID: ${TRANSLATEGEMMA_RUNPOD_ENDPOINT_ID}
       ACHERON_WORKER__LISTEN_PORT: "8001"
     volumes:
       - ./deploy-overrides/translategemma.worker.yaml:/app/translategemma.worker.yaml:ro
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
   Host port `8009` is the next free port in the existing matrix
   (stub services use `8001`-`8007`; qwen3tts-edge uses host `8004`;
   granite-speech-edge uses host `8008`). Internal container port is
   `8001` — each container has its own network namespace so the
   internal port doesn't need to be unique.
   The edge registers with the orchestrator, forwards `/execute`
   calls (with multipart input carrying chunks.json) to RunPod's
   `/run`, returns the translated text via `multipart/mixed`.
6. **Cold starts**: when no GPU pods are warm, the orchestrator's
   `HealthMonitor` reports the worker as `BOOTING` (via the existing
   `RunPodHealthProvider`), jobs queue at the orchestrator, and
   RunPod scales from zero as the first `/execute` arrives.

### Environment variables

| Variable | Required? | Description |
|----------|-----------|-------------|
| `ACHERON_WORKER__WORKER_ID` | yes (or via worker.yaml) | Worker ID used at registration. Default in worker.yaml: `translategemma-1`. |
| `ACHERON_WORKER__ORCHESTRATOR_URL` | yes | Orchestrator base URL. |
| `ACHERON_WORKER__REGISTRATION_TOKEN` | env-only | Bearer token used for `POST /workers`. |
| `ACHERON_WORKER__RUNPOD_API_KEY` | env-only | RunPod API key. |
| `ACHERON_WORKER__RUNPOD_ENDPOINT_ID` | env-only | The RunPod serverless endpoint ID. |
| `ACHERON_WORKER__EXECUTION_TIMEOUT_S` | optional | Per-job timeout (default 1800s). |
| `ACHERON_WORKER__PRICE_SOURCE` | optional | `runpod` (default) \| `static` \| `zero`. |
| `ACHERON_WORKER__SECURE_CLOUD` | optional | Quote secure-cloud vs community-cloud RunPod rate (default `false`). |
| `ACHERON_WORKER__LISTEN_PORT` | optional | Edge container listen port (default 8001). |
| `ACHERON_WORKER__MODEL_ID` | optional | HuggingFace model ID (default `google/translategemma-12b-it`). Flip to `google/translategemma-4b-it` for the smaller variant. |

### Switching GPU types

Operator runs
`runpodctl serverless update <endpoint-id> --gpu-id <new>` (or uses
the RunPod dashboard), then restarts the edge container (or waits
`price_cache_ttl_s`, default 3600s). The worker re-queries the
endpoint's `gpuIds` via the RunPod GraphQL API and resolves the new
`uninterruptablePrice`. No image rebuild required.

### Switching model variants

Operator sets `ACHERON_WORKER__MODEL_ID=google/translategemma-4b-it`
in the env, then restarts the edge container. The cloud-side
handler picks up the new model id at next boot. No image rebuild
required. Note: a 4B BF16 model fits on smaller GPUs (e.g. L4 24GB);
the deployer can also flip the endpoint's GPU type at the same time
to take advantage of the smaller footprint.

### Local-GPU mode

Not shipped in v1. A `TranslateGemmaLocalHandler` would be a
separate future worker package, not a config knob on this one.

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
      - 'src/acheron/core/planner.py'
      - 'src/acheron/core/errors.py'
      - 'src/acheron/shell/transports/http.py'
      - 'src/acheron/shell/config.py'
      - 'src/acheron/shell/orchestrator.py'
      - 'proto/**'
      - 'Dockerfile.edge'
      - 'Dockerfile'
      - '.github/workflows/build-workers.yml'

jobs:
  build-qwen3tts:
    # Job body identical to [Layer 8a spec, "GHCR CI Workflow"](
    # ./layer-8a-tts-worker.md#ghcr-ci-workflow).
    # Publishes acheron-qwen3tts-runpod:latest and :<sha> from workers/qwen3tts/Dockerfile.runpod.

  build-granite-speech:
    # Job body identical to [Layer 8b spec, "GHCR CI Workflow"](
    # ./layer-8b-asr-worker.md#ghcr-ci-workflow).
    # Publishes acheron-granite-speech-runpod:latest and :<sha> from workers/granite_speech/Dockerfile.runpod.

  build-translategemma:
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
          file: workers/translategemma/Dockerfile.runpod
          push: ${{ github.event_name != 'pull_request' }}
          tags: |
            ghcr.io/${{ github.repository }}/acheron-translategemma-runpod:latest
            ghcr.io/${{ github.repository }}/acheron-translategemma-runpod:${{ github.sha }}
          cache-from: type=gha
          cache-to: type=gha,mode=max

  build-edge:
    # Job body identical to [Layer 8a spec, "GHCR CI Workflow"](
    # ./layer-8a-tts-worker.md#ghcr-ci-workflow).
    # Publishes acheron-worker-edge:latest and :<sha> from Dockerfile.edge.
```

- **Single job per worker.** The matrix grows linearly as new
  workers ship; v1 publishes `acheron-qwen3tts-runpod` (8a),
  `acheron-granite-speech-runpod` (8b), and
  `acheron-translategemma-runpod` (8c).
- **Pin to `:<sha>`** in the RunPod template for reproducibility; bump
  to `:latest` when ready.
- **GHCR visibility** inherits the repo's — private repos → private
  images.
- **No local-GPU image is published by v1.**
- **Justfile target:** `just build-worker translategemma` wraps the
  local `docker build` for dev iteration; CI uses the workflow
  directly.

## Testing Strategy

### Worker unit tests (`workers/translategemma/tests/`)

- **`test_capabilities.py`** — `capabilities()` shape:
  `worker_type=TRANSLATION`;
  `supported_languages_in == supported_languages_out == 69-language
  set`; `supported_formats_in == {text}`;
  `supported_formats_out == {text}`;
  `max_input_tokens == 2048`; `batch_capable=True`;
  `model_source="huggingface:google/translategemma-12b-it"`;
  `metadata == {}` (the SDK's `_registration_caps` adds
  `health_provider` + `health_endpoint_id` at registration time, so
  the static capabilities don't carry them — matches the qwen3tts
  pattern).
  - Custom `model_id` setting: `capabilities().model_source` reflects
    the override.
- **`test_handler.py`** — `handle()` with a fake processor + fake
  model:
  - Rejects empty / None `input`.
  - Rejects missing / non-string `source_language` / `target_language`
    in `job.payload`.
  - Rejects unsupported `source_language` / `target_language` (codes
    not in `_SUPPORTED_LANGS`).
  - Rejects malformed chunks.json: not JSON, not a list, missing
    `chapter_id` / `sequence_id` / `text` fields.
  - Rejects chunks.json with non-string / non-int fields.
  - Returns one `BytesArtifact` per chunk, in order, with
    `filename="{chapter_id}_{seq:04d}.txt"`, `content_type="text/plain"`,
    metadata including `chapter_id`, `sequence_id`,
    `source_language`, `target_language`, `model`.
  - Per-chapter batching: a 10-chunk chapter produces 3 model calls
    (4 + 4 + 2).
  - Per-chunk chapter_id sanitisation (delegates to `safe_chapter_id`;
    rejects `..`, `/`, `\`, NUL).
  - `do_sample=False` is asserted (greedy decoding): the production
    handler passes `do_sample=False` to `model.generate`; v1 leaves
    this to the integration test against the real model — the kwarg
    is a one-line code change, the unit test would be a wrapper
    around a fake `generate` that doesn't materially test the
    behavior.
  - `_translate_batch` properly truncates over-length inputs: the
    production handler passes `truncation=True, max_length=_MAX_INPUT_TOKENS`
    to the processor; truncation is enforced by the HuggingFace
    processor, not by our code, so v1 leaves this to the integration
    test.
  - `pad_token_id` fallback: when the processor's tokenizer has
    `pad_token_id is None`, the handler sets it to `eos_token_id` and
    the call succeeds.
- **`test_runpod_entrypoint.py`** — boot smoke test (mirrors 8b's).
- **`test_normalize_chunk.py`** (if `_normalize_chunk` is private
  but tested) — boundary cases for missing fields.

### Worker SDK tests

- **`tests/worker_sdk/test_cloud.py`** — extend:
  - `RunPodForwarderHandler` forwards `Input` for translation jobs
    (mirrors ASR test from 8b). Asserts the base64-encoded `Input`
    bytes are on the RunPod `/run` wire (the existing 8b wire key
    `input_audio` is reused for the 8c `BytesInput`; the field is
    content-type agnostic at the wire level).
  - `make_runpod_handler` reconstructs `Input` from the runpod wire
    JSON and passes it to `TranslateGemmaRunpodHandler.handle()`.

### Orchestrator tests

- **`tests/core/test_models.py`** — extend:
  - `WorkerCapabilities` accepts `max_input_tokens=None` and a
    positive int.
- **`tests/core/test_planner.py`** — extend:
  - `validate_chunking_fits_workers` passes when chunking ≤ workers'
    `max_input_tokens * chars_per_token`.
  - Raises `ChunkingTooLongForWorkerError` when chunking exceeds any
    text-input worker's `max_input_tokens`.
  - Ignores workers with `max_input_tokens is None`.
  - Ignores non-text-input worker types (ASR).
  - Respects the `chars_per_token` argument (smaller value triggers
    the error earlier).
  - Error message includes `chunking_max_length`,
    `max_input_tokens`, and `chars_per_token`.
  - `compile_plan` signature unchanged (no `chunking_max_length`
    parameter added).
- **`tests/shell/transports/test_http.py`** — extend:
  - `HttpWorker.execute` branches on `WorkerType.TRANSLATION` to
    POST multipart (mocked upstream `chunks.json` from `StepCache`).
  - `HttpWorker.execute` branches on `WorkerType.TTS` to POST
    multipart (same shape).
  - The match refactor: no `if job.job_type == WorkerType.ASR` string
    check left in `execute()`.
  - The new `_execute_with_upstream_input` helper: rejects a job
    whose upstream step has no matching `OutputFile` (predicate
    fails).
  - ASR path still works (regression).
- **`tests/shell/test_orchestrator.py`** (if it exists) — extend:
  - The submit-job path calls `validate_chunking_fits_workers`
    after `compile_plan`; misconfigurations fail with
    `ChunkingTooLongForWorkerError` and the job is rejected.
  - `Settings.chars_per_token` is honoured.

### qwen3tts patch tests

- **`workers/qwen3tts/tests/test_handler.py`** — patch:
  - `_build_job` no longer takes `chunks`; tests construct a
    `BytesInput` with `chunks.json` bytes and pass it as `input`.
  - Existing assertions on per-chunk artifacts / batching / error
    paths preserved.
  - New test: `input is None` raises `WorkerError("Qwen3-TTS requires a chunks.json input...")`.
  - New test: malformed `input` raises `WorkerError` (not JSON, not
    a list).
- **`workers/qwen3tts/tests/test_capabilities.py`** — patch:
  - Assert `max_input_tokens == 2048`.

### Test independence

- Worker tests use the model mocking pattern (8a/8b established);
  no torch / transformers / runpod required at test time.
- The workspace `pyproject.toml` excludes torch / transformers from
  the dev install (mirroring 8a/8b); tests that need to import
  `from workers.translategemma.handler import ...` do so with
  `importlib` inside the test body (8a/8b pattern), so the package
  can be imported without GPU deps.
- `tests/worker_sdk/test_cloud.py` runs without runpod deps (uses an
  in-process mock, 8b pattern).

## Edge Cases

- **Empty chapters / empty `chunks` list** — `handle()` returns `[]`
  (no artifacts, no error), matching 8a's qwen3tts convention. The
  orchestrator's streaming executor handles empty artifact lists as a
  no-op (existing behaviour).
- **Single-chunk chapters** — handler still batches (batch of 1 is a
  valid `model.generate` call).
- **Long chapters (>> _MAX_BATCH_SIZE chunks)** — handler runs N
  passes of `_MAX_BATCH_SIZE` sequentially. Total time = N × (one
  batch_generate time).
- **Per-chunk over-length** —
  `processor(text=prompts, truncation=True, max_length=_MAX_INPUT_TOKENS)`
  truncates the input prompt; `max_new_tokens` is unchanged. We log
  a warning (via `logging.warning`) for truncated chunks so the
  deployer can see the chunking misconfig. v1: log only, no error —
  the planner's compile-time check is the hard gate; runtime
  truncation is defense in depth.
- **Unsupported language codes** — `WorkerError` raised in
  `handle()` before model load. Surfaces immediately, no GPU time
  wasted.
- **Malformed `chunks.json`** — `WorkerError` with a clear message
  ("chunks.json is not valid JSON: ...").
- **Model load failure** — `startup()` raises; runpod serverless
  reports the worker as failed; orchestrator's `HealthMonitor` marks
  `OFFLINE`.
- **Empty `Input` bytes** — `handle()` returns `[]` (no artifacts, no
  error), matching the qwen3tts empty-chunks convention. The handler
  treats an empty `Input` body the same as a JSON-parsed empty list
  (`chunks: []`).
- **WorkerType mismatch** — the existing `_language_matches` filter
  (8a spec) ensures only translation workers are picked for the
  translate step and only TTS workers for synthesize. The `match` in
  `HttpWorker.execute` is reached only when the orchestrator's
  step_handler has already filtered by type.

## Open Items (deferred, not blockers for 8c)

- 4B BF16 variant — deployer flips `model_id` knob, no code change.
  Image rebuild not required.
- `Unbabel/TowerInstruct-13B-v0.1` literary-tone alternative —
  separate future sub-project.
- AST (speech translation) and image-translation capabilities of
  TranslateGemma — v1 is text-only.
- Per-step worker targeting (`translation_model` hint parallel to
  `asr_model`) — deferred per the 8a spec.
- Dynamic tokenizer-based token estimator (v1 uses constant
  `chars_per_token`; v2 calls the tokenizer for an exact count).
- Local-GPU `TranslateGemmaLocalHandler` — workers commit to one
  deployment mode by being one mode, per the 8a spec.
- Per-chapter parallelism for translation and `workers_max > 1`
  endpoint scaling.

## File Map (final delta)

```
src/acheron/
  core/
    errors.py              # EXTENDED: ChunkingTooLongForWorkerError
    models.py              # EXTENDED: WorkerCapabilities.max_input_tokens
    planner.py             # EXTENDED: validate_chunking_fits_workers()
  shell/
    config.py              # EXTENDED: Settings.chars_per_token
    orchestrator.py        # EXTENDED: submit path calls both
    transports/
      http.py              # EXTENDED: match on WorkerType; ASR | TRANSLATION | TTS arms
workers/
  translategemma/          # NEW
    handler.py
    runpod_entrypoint.py
    worker.yaml
    worker.edge.yaml
    Dockerfile.runpod
    pyproject.toml
    README.md
    tests/
      __init__.py
      test_capabilities.py
      test_handler.py
      test_runpod_entrypoint.py
  qwen3tts/                # EXTENDED
    handler.py             # reads chunks from input
    tests/
      test_handler.py      # updated fixtures
      test_capabilities.py # max_input_tokens=2048
  _shared.py               # unchanged
.github/workflows/
  build-workers.yml        # EXTENDED: + build-translategemma
docker-compose.yml         # EXTENDED: + translategemma-edge under runpod-translation
pyproject.toml             # EXTENDED: workspace member acheron-translategemma
Justfile                   # EXTENDED: build-worker translategemma
docs/superpowers/specs/
  layer-8c-translategemma-worker.md   # NEW (this file)
```

**Import boundaries** (no new import-linter contracts needed; the
existing 8a/8b boundaries cover 8c):

- `acheron.core` and `acheron.shell` retain their existing boundaries.
- `workers.* -> acheron.worker_sdk, acheron.core` (allowed).
- `workers.* -/-> acheron.shell` (forbidden).
