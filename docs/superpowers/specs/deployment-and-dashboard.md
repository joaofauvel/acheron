# Deployment and Dashboard Design Spec

This specification defines the decoupling and containerization of model-specific workers, native health check provider plugins, and dashboard improvements for tracking worker health errors and backend connection state.

## 1. Decoupled, Model-Specific Workers

To keep workers completely modular, each model is packaged as its own self-contained codebase and Docker image:

* **Location:** Each worker resides under `workers/<model_name>/` (e.g., `workers/qwen3tts/`, `workers/whisperv3large/`, `workers/translategemma/`).
* **Zero Coupling:** Workers have no dependency on the `acheron` orchestrator code or package. They interact solely over HTTP/gRPC standard APIs:
  * For HTTP: Implement `/health`, `/capabilities`, and `/execute` endpoints.
  * For gRPC: Implement the bidirectional stream service defined in `proto/synthesis.proto`.
* **CI/CD Build & Publish:**
  * The CI workflow (`.github/workflows/docker-publish.yml`) builds separate containers:
    * `Dockerfile.orchestrator` → `ghcr.io/<repo>/acheron-orchestrator:latest`
    * `workers/qwen3tts/Dockerfile` → `ghcr.io/<repo>/qwen3tts-worker:latest`
    * `workers/whisperv3large/Dockerfile` → `ghcr.io/<repo>/whisperv3large-worker:latest`
    * `workers/translategemma/Dockerfile` → `ghcr.io/<repo>/translategemma-worker:latest`
* **Local Dev Builds:** A `just build-workers` target (or equivalent) should cover local image builds outside CI. Confirm `workers/` is not `.gitignore`d — model weights must not land there accidentally.

## 2. Decoupled Provider Health Checks

To handle cold-start booting on platforms like RunPod Serverless or Hugging Face Inference Endpoints:

* **`WorkerStatus` enum** (add to `src/acheron/core/models.py`):
  ```python
  class WorkerStatus(Enum):
      HEALTHY = "healthy"
      BOOTING = "booting"
      OFFLINE = "offline"
  ```

* **Abstract HealthProvider Interface:**
  Add a `HealthProvider` base class in `src/acheron/shell/health_providers.py` (separate file from `health.py` to avoid mixing the active polling loop with platform introspection logic):
  ```python
  class HealthProvider(ABC):
      @abstractmethod
      async def check_status(self, endpoint_id: str) -> WorkerStatus:
          """Query platform API to verify if the container is booting vs offline."""
          ...
  ```

* **Configuration (`acheron.yaml`):**
  Platform credentials are defined in the config YAML:
  ```yaml
  providers:
    runpod:
      api_key: "${RUNPOD_API_KEY}"
    huggingface:
      api_key: "${HF_API_KEY}"
  ```

* **Self-Registration:**
  Workers specify `health_provider` (e.g. `"runpod"` or `"huggingface"`) and `health_endpoint_id` in their capabilities metadata during registration. These are stored via `WorkerCapabilities.metadata` (typed `dict[str, JsonValue]`) — no first-class field change is required, but both keys must be treated as reserved and documented. Typos in these keys will be silently ignored at registration time.

* **`last_error` field on workers:**
  The "View Error" dashboard feature requires `last_error` to be surfaced on the `/workers` API response. Add `last_error: str | None = None` to `RegisteredWorker`. Propagate through all store serialization/deserialization paths (`_worker_fields`, `_deserialize_worker` in `stores/redis.py`). `HealthMonitor` populates this field on probe failure.

* **Probing:**
  If the orchestrator's HTTP health check to a worker fails, the `HealthMonitor` calls the corresponding `HealthProvider`. If the platform API returns that the instance is initializing/booting, its state is marked as `Booting`.

## 3. Dashboard Updates

### A. Connected to Backend Circle
* Displays next to the main "Acheron" heading:
  * **🟢 Connected** (when dashboard fetches to the orchestrator succeed).
  * **🔴 Disconnected** (when dashboard fetches fail).
* Implemented via a `/partials/status` endpoint in the **orchestrator API** (`src/acheron/shell/api/`), not the dashboard server itself. The dashboard polls it via HTMX.

### B. Worker Status & Failure Logs
* The **Workers** table shows status badges: `Healthy`, `Booting`, or `Offline` (sourced from `WorkerStatus` enum, §2).
* For `Booting` or `Offline` workers, a "View Error" button will toggle a clean inline card or modal showing the `last_error` string populated from the health monitor (see `last_error` field, §2).
* The `/workers` API response must include both `status` and `last_error` fields.
* No placeholder stubs for job submission or capabilities will be added.

## 4. Finalized Design Decisions (Implementation)

- **Scope split:** Sections 2 and 3 (health checks + dashboard) are implemented first. Section 1 (decoupled worker packaging + CI/CD) is deferred to a separate plan — it requires Docker/CUDA build context and GPU worker skeletons to validate.
- **`/partials/status` proxy:** The orchestrator owns the status partial logic (`GET /partials/status` → green "Connected" HTML). The dashboard proxies it via its own same-origin `/partials/status` route, returning red "Disconnected" HTML when the orchestrator is unreachable. This keeps the logic in the orchestrator (per spec) while working in the compose setup where the browser cannot resolve the orchestrator's internal hostname.
- **`health_endpoint_id` is provider-specific:** RunPod → serverless endpoint id (`GET /endpoints/{id}`); HuggingFace → `namespace/name` (`GET /v2/endpoints/{namespace}/{name}`).
- **RunPod mapping:** endpoint exists → `BOOTING` (cold start); 404/error → `OFFLINE`.
- **HuggingFace mapping:** `status.state` in `{pending, initializing, starting, running}` → `BOOTING`; `{paused, failed}` or 404/error → `OFFLINE`.
- **Booting workers are not removed** — the failure counter is not incremented while a platform reports booting. A boot timeout is a future extension.
- **`${VAR}` env-var expansion** is applied to all `acheron.yaml` string values (not just provider keys) by the YAML settings source.
- **`HealthProbeResult`** (healthy + error) replaces the prior `bool` return from `HealthCheckFn` so `last_error` captures the actual probe failure reason.
