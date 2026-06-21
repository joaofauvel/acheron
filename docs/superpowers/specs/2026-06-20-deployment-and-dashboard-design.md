# Deployment and Dashboard Design Spec

This specification defines the decoupling and containerization of model-specific workers, native health check provider plugins, and dashboard improvements for tracking worker health errors and backend connection state.

## 1. Decoupled, Model-Specific Workers

To keep workers completely modular, each model is packaged as its own self-contained codebase and Docker image:

* **Location:** Each worker resides under `workers/<model_name>/` (e.g., `workers/qwen3tts/`, `workers/whisperv3large/`).
* **Zero Coupling:** Workers have no dependency on the `acheron` orchestrator code or package. They interact solely over HTTP/gRPC standard APIs:
  * For HTTP: Implement `/health`, `/capabilities`, and `/execute` endpoints.
  * For gRPC: Implement the bidirectional stream service defined in `proto/synthesis.proto`.
* **CI/CD Build & Publish:**
  * The CI workflow (`.github/workflows/docker-publish.yml`) builds separate containers:
    * `Dockerfile.orchestrator` → `ghcr.io/<repo>/acheron-orchestrator:latest`
    * `workers/qwen3tts/Dockerfile` → `ghcr.io/<repo>/qwen3tts-worker:latest`
    * `workers/whisperv3large/Dockerfile` → `ghcr.io/<repo>/whisperv3large-worker:latest`
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
