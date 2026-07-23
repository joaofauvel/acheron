# acheron-granite-speech

RunPod Serverless worker package for `ibm-granite/granite-speech-4.1-2b`.

## Image

CI publishes `ghcr.io/<owner>/acheron-granite-speech-runpod:latest` and
`:<sha>` on every push to `master` and on every `v*` tag. Pin your RunPod
template to `:<sha>` for reproducibility.

## RunPod Serverless setup (one-time)

1. **Create a network volume** for the HuggingFace cache to avoid re-downloading the ~4GB weights on every cold start. Mount it at `/runpod-volume/huggingface-cache`. Pre-warm it once:

   ```bash
   pip install "huggingface_hub[cli]" hf-transfer
   HF_HUB_ENABLE_HF_TRANSFER=1 huggingface-cli download \
       ibm-granite/granite-speech-4.1-2b \
       --local-dir /runpod-volume/huggingface-cache/hub/models--ibm-granite--granite-speech-4.1-2b
   ```

   `HF_HUB_ENABLE_HF_TRANSFER=1` is a pre-warm-only concern; it is not set in
   the runtime image because the runtime is offline (`HF_HUB_OFFLINE=1`).

2. **Create a RunPod serverless template** pointing at the published image. Set:
   - GPU type list: `[L4]` (24GB, the cheapest 24GB tier per the deployer's
     compute choice; single GPU per deployment).
   - Disk / container disk: ≥ 10 GB.
   - Network volume (from step 1) attached at `/runpod-volume`.
   - Environment variables: see "Environment variables" below.

3. **Create a serverless endpoint** from the template. Configure:
   - `workers_min: 0`, `workers_max: 1`.
   - `idle_timeout: 300`.
   - Note the endpoint ID.

4. **Configure the orchestrator-side edge service** (`docker-compose.yml`'s `granite-speech-edge`):

   ```env
   ACHERON_WORKER__REGISTRATION_TOKEN=<orchestrator's token>
   ACHERON_WORKER__RUNPOD_API_KEY=<your RunPod API key>
   ACHERON_WORKER__RUNPOD_ENDPOINT_ID=<endpoint id from step 3>
   ```

5. `docker compose --profile runpod-asr up -d`. The edge registers with the
   orchestrator; the orchestrator's `HealthMonitor` reports the worker as
   `BOOTING` until RunPod scales up the GPU pod on the first `/execute`.

## Environment variables

| Variable | Required? | Description |
|----------|-----------|-------------|
| `ACHERON_WORKER__WORKER_ID` | yes (or via worker.yaml) | Worker ID used at registration. Default in worker.yaml: `granite-speech-1`. |
| `ACHERON_WORKER__ORCHESTRATOR_URL` | yes | Orchestrator base URL. |
| `ACHERON_WORKER__WORKER_HOST` | Compose deployments | Hostname the orchestrator uses to reach this edge container. |
| `ACHERON_WORKER__REGISTRATION_TOKEN` | env-only | Bearer token used for `POST /workers`. |
| `ACHERON_WORKER__RUNPOD_API_KEY` | env-only | RunPod API key. |
| `ACHERON_WORKER__RUNPOD_ENDPOINT_ID` | env-only | The RunPod serverless endpoint ID. |
| `ACHERON_WORKER__EXECUTION_TIMEOUT_S` | optional | Per-job timeout (default 1800s). |
| `ACHERON_WORKER__PRICE_SOURCE` | optional | `runpod` (default), `static`, or `zero`. |
| `ACHERON_WORKER__SECURE_CLOUD` | optional | Quote secure-cloud vs community-cloud RunPod rate (default `false`). |
| `ACHERON_WORKER__LISTEN_PORT` | optional | Edge container listen port (default 8001). |

## Switching GPU types

RunPod is the single source of truth for the GPU type. To change:

1. `runpodctl serverless update <endpoint-id> --gpu-id <new>` (or via the RunPod dashboard).
2. Restart the edge container (or wait `price_cache_ttl_s`, default 3600s).

The worker re-queries the endpoint's `gpuIds` via the RunPod GraphQL API and
resolves the new `uninterruptablePrice`. No image rebuild required.

## Local-GPU mode

Not shipped in v1. A `GraniteSpeechLocalHandler` would be a separate future
worker package, not a config knob on this one.

## Languages and variants

`ibm-granite/granite-speech-4.1-2b` supports 6 ASR languages:
`en, fr, de, es, pt, ja`. Punctuation and truecasing for all 6 with the
hardcoded prompt ("transcribe the speech with proper punctuation and
capitalization.").

`granite-speech-4.1-2b-nar` (non-autoregressive) is explicitly excluded by
the deployer's choice. `granite-speech-4.1-2b-plus` (speaker-attributed
ASR + word-level timestamps) is deferred to a separate future sub-project.
