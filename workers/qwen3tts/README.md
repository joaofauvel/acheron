# acheron-qwen3tts

RunPod Serverless worker package for `Qwen/Qwen3-TTS-12Hz-1.7B-CustomVoice`.

## Image

CI publishes `ghcr.io/<owner>/acheron-qwen3tts-runpod:latest` and
`:<sha>` on every push to `main` and on every `v*` tag. Pin your RunPod
template to `:<sha>` for reproducibility.

## RunPod Serverless setup (one-time)

1. **Create a network volume** for the HuggingFace cache to avoid re-downloading the ~3.4GB weights on every cold start. Mount it at `/runpod-volume/huggingface-cache`. Pre-warm it once:

   ```bash
   huggingface-cli download Qwen/Qwen3-TTS-12Hz-1.7B-CustomVoice \
       --local-dir /runpod-volume/huggingface-cache/hub/models--Qwen--Qwen3-TTS-12Hz-1.7B-CustomVoice
   ```

2. **Create a RunPod serverless template** pointing at the published image. Set:
   - GPU type list: `[L4, A5000, RTX 3090]` (24GB minimum; the worker's pricing auto-discovers which GPU the endpoint is actually using — no image rebuild required to switch).
   - Disk/container disk: ≥ 10 GB.
   - Network volume (from step 1) attached at `/runpod-volume`.
   - Environment variables: see "Environment variables" below.

3. **Create a serverless endpoint** from the template. Configure:
   - `workers_min: 0`, `workers_max: 1` (sufficient for one book at a time; bump for concurrent books).
   - `idle_timeout: 300` (matches the existing cost-containment strategy).
   - Note the endpoint ID.

4. **Configure the orchestrator-side edge service** (`docker-compose.yml`'s `qwen3tts-edge`):

   ```env
   ACHERON_WORKER__REGISTRATION_TOKEN=<orchestrator's token>
   ACHERON_WORKER__RUNPOD_API_KEY=<your RunPod API key>
   ACHERON_WORKER__RUNPOD_ENDPOINT_ID=<endpoint id from step 3>
   ```

5. `docker compose --profile runpod-tts up -d`. The edge registers with the
   orchestrator; the orchestrator's `HealthMonitor` reports the worker as
   `BOOTING` until RunPod scales up the GPU pod on the first `/execute`.

## Environment variables

| Variable | Required? | Description |
|----------|-----------|-------------|
| `ACHERON_WORKER__WORKER_ID` | yes (or via worker.yaml) | Worker ID used at registration. Default in worker.yaml: `qwen3tts-1`. |
| `ACHERON_WORKER__ORCHESTRATOR_URL` | yes | Orchestrator base URL. |
| `ACHERON_WORKER__WORKER_HOST` | Compose deployments | Hostname the orchestrator uses to reach this edge container. |
| `ACHERON_WORKER__REGISTRATION_TOKEN` | env-only | Bearer token used for `POST /workers`. |
| `ACHERON_WORKER__RUNPOD_API_KEY` | env-only | RunPod API key (used by the edge forwarder and by the RunPod price source). |
| `ACHERON_WORKER__RUNPOD_ENDPOINT_ID` | env-only | The RunPod serverless endpoint ID created in step 3 above. |
| `ACHERON_WORKER__EXECUTION_TIMEOUT_S` | optional | Per-job timeout (default 1800s). |
| `ACHERON_WORKER__PRICE_SOURCE` | optional | `runpod` (default) | `static` | `zero`. |
| `ACHERON_WORKER__SECURE_CLOUD` | optional | Quote secure-cloud vs community-cloud RunPod rate (default `false`). |
| `ACHERON_WORKER__DEFAULT_SPEAKER` | optional | Speaker used when job payload doesn't set one (default `Ryan`). |
| `ACHERON_WORKER__LISTEN_PORT` | optional | Edge container listen port (default 8001). |

## Switching GPU types

RunPod is the single source of truth for the GPU type. To change the GPU:

1. `runpodctl serverless update <endpoint-id> --gpu-id <new>` (or via the RunPod dashboard).
2. Restart the edge container (or wait `price_cache_ttl_s`, default 3600s).

The worker re-queries the endpoint's `gpuIds` via the RunPod GraphQL API and
resolves the new `uninterruptablePrice`. No image rebuild required.

## Local-GPU mode

Not shipped in v1. A `Qwen3TTSLocalHandler` would be a separate future worker
package, not a config knob on this one.

## Languages and speakers

`Qwen3-TTS-12Hz-1.7B-CustomVoice` supports 10 languages:
`en zh ja ko de fr ru pt es it`.

Built-in premium speakers:
`Vivian, Serena, Uncle_Fu, Dylan, Eric, Ryan, Aiden, Ono_Anna, Sohee`.

Voice cloning (via `Qwen3-TTS-12Hz-1.7B-Base`) is deferred to a separate
sub-project.
