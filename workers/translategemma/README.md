# acheron-translategemma

RunPod Serverless worker package for `google/translategemma-12b-it`.

## Image

CI publishes `ghcr.io/<owner>/acheron-translategemma-runpod:latest` and
`:<sha>` on every push to `main` and on every `v*` tag. Pin your RunPod
template to `:<sha>` for reproducibility.

## RunPod Serverless setup (one-time)

1. **Create a network volume** for the HuggingFace cache to avoid re-downloading
   the ~26GB weights on every cold start. Mount it at
   `/runpod-volume/huggingface-cache`. Pre-warm it once:

   ```bash
   pip install "huggingface_hub[cli]" hf-transfer
   HF_HUB_ENABLE_HF_TRANSFER=1 huggingface-cli download \
       google/translategemma-12b-it \
       --local-dir /runpod-volume/huggingface-cache/hub/models--google--translategemma-12b-it
   ```

   `HF_HUB_ENABLE_HF_TRANSFER=1` is a pre-warm-only concern; it is not set in
   the runtime image because the runtime is offline (`HF_HUB_OFFLINE=1`).

2. **Create a RunPod serverless template** pointing at the published image. Set:
   - GPU type list: `[A40]` (48GB; needed for 12B BF16 = ~26GB; a single GPU
     per deployment).
   - Disk / container disk: ≥ 30 GB (the snapshot is ~26GB).
   - Network volume (from step 1) attached at `/runpod-volume`.
   - Environment variables: see "Environment variables" below.

3. **Create a serverless endpoint** from the template. Configure:
   - `workers_min: 0`, `workers_max: 1`.
   - `idle_timeout: 300`.
   - Note the endpoint ID.

4. **Configure the orchestrator-side edge service** (`docker-compose.yml`'s
   `translategemma-edge`):

   ```env
   ACHERON_REGISTRATION_TOKEN=<orchestrator's token>
   ACHERON_WORKER__RUNPOD_API_KEY=<your RunPod API key>
   ACHERON_WORKER__RUNPOD_ENDPOINT_ID=<endpoint id from step 3>
   ```

5. `docker compose --profile runpod-translation up -d`. The edge registers
   with the orchestrator; the orchestrator's `HealthMonitor` reports the
   worker as `BOOTING` until RunPod scales up the GPU pod on the first
   `/execute`.

## Environment variables

| Variable | Required? | Description |
|----------|-----------|-------------|
| `ACHERON_WORKER__WORKER_ID` | yes (or via worker.yaml) | Worker ID used at registration. Default in worker.yaml: `translategemma-1`. |
| `ACHERON_WORKER__ORCHESTRATOR_URL` | yes | Orchestrator base URL. |
| `ACHERON_WORKER__REGISTRATION_TOKEN` | env-only | Bearer token used for `POST /workers`. |
| `ACHERON_WORKER__RUNPOD_API_KEY` | env-only | RunPod API key. |
| `ACHERON_WORKER__RUNPOD_ENDPOINT_ID` | env-only | The RunPod serverless endpoint ID. |
| `ACHERON_WORKER__MODEL_ID` | optional | HuggingFace model id (default `google/translategemma-12b-it`; switch to `google/translategemma-4b-it` for a smaller variant). |
| `ACHERON_WORKER__EXECUTION_TIMEOUT_S` | optional | Per-job timeout (default 1800s). |
| `ACHERON_WORKER__PRICE_SOURCE` | optional | `runpod` (default) | `static` | `zero`. |
| `ACHERON_WORKER__SECURE_CLOUD` | optional | Quote secure-cloud vs community-cloud RunPod rate (default `false`). |
| `ACHERON_WORKER__LISTEN_PORT` | optional | Edge container listen port (default 8009). |

## Switching GPU types

RunPod is the single source of truth for the GPU type. To change:

1. `runpodctl serverless update <endpoint-id> --gpu-id <new>` (or via the RunPod dashboard).
2. Restart the edge container (or wait `price_cache_ttl_s`, default 3600s).

The worker re-queries the endpoint's `gpuIds` via the RunPod GraphQL API and
resolves the new `uninterruptablePrice`. No image rebuild required.

## Switching model variants

Set `ACHERON_WORKER__MODEL_ID=google/translategemma-4b-it` and restart the
edge container. The next cold start re-downloads the smaller weights into
the network volume's HF cache.

## Local-GPU mode

Not shipped in v1. A `TranslateGemmaLocalHandler` would be a separate future
worker package, not a config knob on this one.

## Languages and variants

`google/translategemma-12b-it` supports 55 languages. v1 advertises the full
set so the orchestrator can plan any pair; language-path validation at plan
compile time still rejects pairs outside the orchestrator's
`SUPPORTED_LANGUAGES={en, es, fr, de}`.

`google/translategemma-4b-it` is the smaller sibling. The same 55-language
set; same `capabilities()` shape (drop-in replacement via `model_id` knob).
