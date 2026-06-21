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

## 2. Decoupled Provider Health Checks

To handle cold-start booting on platforms like RunPod Serverless or Hugging Face Inference Endpoints:

* **Abstract HealthProvider Interface:**
  We add a `HealthProvider` base class in `src/acheron/shell/health.py`:
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
  Workers specify their `health_provider` and `health_endpoint_id` in their capabilities metadata during registration.
* **Probing:**
  If the orchestrator's HTTP health check to a worker fails, the `HealthMonitor` calls the corresponding `HealthProvider`. If the platform API returns that the instance is initializing/booting, its state is marked as `Booting`.

## 3. Dashboard Updates

### A. Connected to Backend Circle
* Displays next to the main "Acheron" heading:
  * **🟢 Connected** (when dashboard fetches to the orchestrator succeed).
  * **🔴 Disconnected** (when dashboard fetches fail).
* Implemented via a `/partials/status` polling endpoint in the dashboard.

### B. Worker Status & Failure Logs
* The **Workers** table shows status badges: `Healthy`, `Booting`, or `Offline`.
* For `Booting` or `Offline` workers, a "View Error" button will toggle a clean inline card or modal showing the `last_error` string populated from the health monitor.
* No placeholder stubs for job submission or capabilities will be added.
