# translategemma-worker Design Spec

Defines the implementation of the `translategemma-worker` container — the GPU-backed translation worker that handles `TRANSLATION` pipeline steps when `source_language != target_language`.

## Model

**`google/translategemma-12b-it`** (TranslateGemma 12B, instruction-tuned)

- Built on Gemma 3, fine-tuned for machine translation via SFT + RL distillation from Gemini.
- Outperforms the 27B general-purpose baseline by ~23.5% error reduction on WMT24++ benchmarks.
- Supports 55 languages; covers all language pairs the orchestrator plans today.
- Min ~16GB VRAM in bfloat16 (fits on a 24GB/40GB GPU; use quantization for tighter cards).
- Inference via HuggingFace `transformers` — `AutoModelForCausalLM` + `AutoTokenizer`.
- Uses the model's native chat template (`apply_chat_template`) for structuring translation requests. Do not use a bare prompt; the model is opinionated about its input format.

## API Contract

The worker is a self-contained HTTP service. No dependency on `acheron` package or orchestrator code.

### `GET /health`

Returns `200 OK` when the model is loaded and ready. Returns `503 Service Unavailable` while loading.

```json
{"status": "ok"}
```

### `GET /capabilities`

Returns the worker's identity and supported language pairs. All 55 TranslateGemma language pairs are declared.

```json
{
  "worker_type": "translation",
  "supported_languages_in": ["en", "es", "fr", "de", "zh", "ja", "..."],
  "supported_languages_out": ["en", "es", "fr", "de", "zh", "ja", "..."],
  "supported_formats_in": ["text/plain"],
  "supported_formats_out": ["text/plain"],
  "max_payload_bytes": 524288,
  "batch_capable": false,
  "model_source": "google/translategemma-12b-it"
}
```

### `POST /execute`

Translates a single chunk of text.

**Request:**
```json
{
  "job_id": "job-abc123",
  "job_type": "translation",
  "payload": {
    "text": "The quick brown fox.",
    "source_language": "en",
    "target_language": "es"
  },
  "chapter_id": "ch1",
  "sequence_ids": [0]
}
```

**Response (success):**
```json
{
  "job_id": "job-abc123",
  "status": "success",
  "outputs": [
    {
      "path": "/data/output/job-abc123-ch1-0.txt",
      "filename": "job-abc123-ch1-0.txt",
      "size_bytes": 42,
      "checksum": "<sha256>",
      "content_type": "text/plain"
    }
  ],
  "metrics": {
    "duration_seconds": 1.2,
    "gpu_seconds": 1.0,
    "tokens_in": 12,
    "tokens_out": 11,
    "cost_estimate": 0.0
  }
}
```

**Response (failure):**
```json
{
  "job_id": "job-abc123",
  "status": "failed",
  "outputs": [],
  "metrics": {"duration_seconds": 0.1},
  "error": "Translation failed: ..."
}
```

## Inference Implementation

```python
from transformers import AutoTokenizer, AutoModelForCausalLM

tokenizer = AutoTokenizer.from_pretrained("google/translategemma-12b-it")
model = AutoModelForCausalLM.from_pretrained(
    "google/translategemma-12b-it",
    device_map="auto",
    torch_dtype=torch.bfloat16,
)

def translate(text: str, source_language: str, target_language: str) -> str:
    messages = [
        {
            "role": "user",
            "content": f"Translate the following text from {source_language} to {target_language}:\n{text}",
        }
    ]
    prompt = tokenizer.apply_chat_template(
        messages, tokenize=False, add_generation_prompt=True
    )
    inputs = tokenizer(prompt, return_tensors="pt").to(model.device)
    outputs = model.generate(**inputs, max_new_tokens=1024)
    # Decode only the generated tokens (strip the prompt)
    return tokenizer.decode(outputs[0][inputs["input_ids"].shape[-1]:], skip_special_tokens=True)
```

Key points:
- Load model once at worker startup, not per-request.
- Use `bfloat16` for memory efficiency on GPU.
- Decode only the newly generated tokens (slice from `input_ids.shape[-1]`).
- Output text is written to a temp file and its path included in the response `outputs`.

## Self-Registration

On startup, the worker registers itself with the orchestrator:

```
POST /workers
{
  "worker_id": "translategemma-<uuid>",
  "endpoint": "http://<host>:<port>",
  "transport": "http",
  "capabilities": { ... }   # as returned by GET /capabilities
}
```

The registration endpoint and token are provided via environment variables:
- `ACHERON_ORCHESTRATOR_URL` — orchestrator base URL
- `ACHERON_REGISTRATION_TOKEN` — token for the `POST /workers` endpoint

## Environment Variables

| Variable | Default | Description |
|---|---|---|
| `ACHERON_ORCHESTRATOR_URL` | — | Required. Orchestrator URL for self-registration. |
| `ACHERON_REGISTRATION_TOKEN` | — | Required. Registration bearer token. |
| `WORKER_PORT` | `8002` | Port to listen on. |
| `MODEL_ID` | `google/translategemma-12b-it` | HuggingFace model ID. |
| `MAX_NEW_TOKENS` | `1024` | Max tokens generated per request. |
| `HF_HOME` | `/data/hf_cache` | HuggingFace cache dir (mount a persistent volume here). |

## Deployment

* **Serverless (RunPod / Hugging Face Inference Endpoints):** Cold-start time ~90–150s while loading the 12B model. The `HealthProvider` mechanism (see [deployment spec](./2026-06-20-deployment-and-dashboard-design.md)) handles this: the orchestrator marks the worker as `Booting` until `/health` returns 200. Target at least a 24GB GPU pod (e.g. RTX 4090, L4, A10G).
* **Local GPU (Docker Compose):** Add service to `docker-compose.yml` with `deploy.resources.reservations.devices` for GPU access. Mount a volume at `HF_HOME` to persist the model cache across restarts.
* **VRAM:** ~16GB in bfloat16. Fits comfortably on a 24GB consumer GPU or any 40GB+ enterprise card (A100, H100).

## Dockerfile sketch

```dockerfile
FROM pytorch/pytorch:2.3.0-cuda12.1-cudnn8-runtime

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

ENV HF_HOME=/data/hf_cache
ENV MODEL_ID=google/translategemma-12b-it

EXPOSE 8002
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8002"]
```

`requirements.txt` contains at minimum: `fastapi`, `uvicorn`, `transformers`, `torch`, `accelerate`, `httpx`.

## File Map

```
workers/translategemma/
├── Dockerfile
├── requirements.txt
├── main.py          # FastAPI app: /health, /capabilities, /execute
├── model.py         # Model load + translate()
└── register.py      # Self-registration logic on startup
```
