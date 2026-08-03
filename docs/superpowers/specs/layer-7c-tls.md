# Acheron — Layer 7c Design Spec

**TLS Support for Acheron Services**

This is a sub-project of [Acheron design spec](./architecture.md) and [implementation roadmap](./roadmap.md). It gives every Acheron service the ability to serve TLS and every Acheron client the ability to verify TLS, configured entirely through environment variables. Cert provenance and reverse proxying remain the deployer's responsibility.

## Goal

Three problems the current stack has around TLS:

1. **No TLS support in any Acheron service.** All HTTP and gRPC traffic is plaintext. Worker registration tokens cross the wire in cleartext, and the orchestrator's API can only be exposed over HTTP.
2. **No shared trust model.** When Layer 8 lands and GPU workers run on RunPod or HuggingFace Inference Endpoints (remote, public-internet), the orchestrator will need to verify their certs. There's no CA or trust path today.
3. **Reverse proxy coupling.** The current `docker-compose.yml` exposes services on plaintext ports. A deployer who wants TLS today must put a reverse proxy in front, but Acheron's own services don't understand TLS, so the proxy has to do all the work and the inner services have no fallback story for cert rotation, hostname changes, etc.

This spec fixes the application-side: every service can serve TLS, every client can verify TLS. It explicitly does **not** ship a reverse proxy — that's a deployment concern.

## Design

### 1. Configuration interface

Three env vars, all optional. Unset means "no TLS" (current behavior, backward compatible).

| Var | Used by | When set | When unset |
|---|---|---|---|
| `ACHERON_TLS_CERT_FILE` | Server side | Path to PEM-encoded server cert | — |
| `ACHERON_TLS_KEY_FILE` | Server side | Path to PEM-encoded server key (unencrypted) | — |
| `ACHERON_TLS_CA_FILE` | Client side | Path to PEM-encoded CA bundle | — |

**Server rule.** Both `ACHERON_TLS_CERT_FILE` and `ACHERON_TLS_KEY_FILE` must be set together, or neither. Setting only one is a misconfiguration and causes a startup error with a clear message. When both are set, the service serves HTTPS / TLS. When both are unset, the service serves HTTP (current behavior).

**Client rule.** If `ACHERON_TLS_CA_FILE` is set, the client uses it as the trust store. If unset, the client uses the system default trust store (`ssl.create_default_context()`). Hostname verification is always on.

**Why this shape.** It mirrors how the stdlib `ssl` module works (cert + key together, CA optional) and matches the conventions used by other tools (curl, openssl, kubernetes ingress). A deployer who knows TLS recognizes it immediately.

### 2. Server-side TLS

The shared `src/acheron/tls.py` module owns certificate parsing, expiry status, monitoring, and the persistent `ssl.SSLContext`. HTTP and gRPC entry points use the same TLS configuration contract.

**HTTP services** (orchestrator, tts-stub, asr-stub, translation-stub). All run via `uvicorn`. The orchestrator passes its persistent context to the server so an authenticated certificate reload can update the active context without restarting the process. Other services retain the file-based startup path. The helper returns the right kwargs dict for the file-based path:

```python
def uvicorn_ssl_kwargs() -> dict[str, str]:
    cert = os.environ.get("ACHERON_TLS_CERT_FILE")
    key = os.environ.get("ACHERON_TLS_KEY_FILE")
    if cert is None and key is None:
        return {}
    if cert is None or key is None:
        raise AcheronError(
            "ACHERON_TLS_CERT_FILE and ACHERON_TLS_KEY_FILE must be set together"
        )
    return {"ssl_certfile": cert, "ssl_keyfile": key}
```

Each entry point that calls `uvicorn.run(...)` passes `**uvicorn_ssl_kwargs()`.

**gRPC server support.** Services that instantiate `grpc.aio.server()` can use `grpc_server_credentials()` to build `grpc.ssl_server_credentials` from the configured certificate and key. No new dependency is required because the helper is provided by `grpcio`.

The current `stubs/tts_grpc_stub/main.py` is not a gRPC server: despite its historical name, it creates a FastAPI/Uvicorn HTTP worker-edge app. Its Compose listener is HTTP on port 9002, and its worker client registers with the HTTPS orchestrator. The gRPC credential helper is therefore not used by that Compose stub.

**Dashboard.** Stays HTTP. The expectation is that it ships in the same node as the orchestrator, so it doesn't need TLS — the orchestrator's HTTPS endpoint is the public surface. No changes to the dashboard.

### 3. Client-side TLS verification

**HTTP clients (httpx).** httpx respects `SSL_CERT_FILE` automatically — it inherits this behavior from the stdlib `ssl` module via `ssl.create_default_context()`. We set `SSL_CERT_FILE=/certs/acheron-ca.crt` in the environment and httpx trusts the Acheron CA. No code change.

This applies to:
- The orchestrator's httpx client (when calling HTTP workers for health, capabilities, jobs).
- The worker stubs' httpx client (when registering with the orchestrator).

**gRPC client (orchestrator → gRPC workers).** gRPC's Python API does not natively honor `SSL_CERT_FILE`, so `tls.grpc_channel_credentials` reads `ACHERON_TLS_CA_FILE` and falls back to `SSL_CERT_FILE` if unset — so a single trust-store env var covers the orchestrator's HTTP and gRPC clients alike. `ACHERON_TLS_CA_FILE` wins when both are set (explicit override for gRPC).

```python
def grpc_channel_credentials() -> grpc.ChannelCredentials | None:
    ca = os.environ.get("ACHERON_TLS_CA_FILE") or os.environ.get("SSL_CERT_FILE")
    if ca is None:
        return None
    ca_pem = Path(ca).read_bytes()
    return grpc.ssl_channel_credentials(root_certificates=ca_pem)
```

The orchestrator's `GrpcWorker` transport calls `grpc.aio.secure_channel(target, credentials)` when credentials are returned, falls back to `grpc.aio.insecure_channel(target)` otherwise. The `target` is the bare `host:port` from `WORKER_ENDPOINT` (the scheme is already stripped today — that behavior stays).

`docker-compose.yml` sets `ACHERON_TLS_CA_FILE=/certs/acheron-ca.crt` on the orchestrator explicitly, so the gRPC client has the CA even if `SSL_CERT_FILE` is unset (e.g. when a deployer customizes the env).

**CLI (host → orchestrator HTTP).** The CLI's httpx client resolves the trust store in this order:
1. `ACHERON_TLS_CA_FILE` (Acheron-specific override)
2. `SSL_CERT_FILE` (the standard env var honored by httpx and stdlib ssl)
3. `./certs/acheron-ca.crt` relative to CWD (dev convenience — auto-discovers the dev CA when the user runs `acheron` from the project root)
4. System trust store (`True`)

Explicit env vars win over the dev auto-discovery. The CLI's default `ACHERON_URL` is `https://localhost:8000` (so it Just Works against the dev/HTTPS orchestrator on the host). A user who wants HTTP-only sets `ACHERON_URL=http://localhost:8000` explicitly. The dashboard is unchanged and stays plain HTTP.

**SSL verification failure message.** `httpx.ConnectError` wraps both transport-level and TLS-level failures (the `start_tls` phase is part of the connection). When the underlying cause is an `ssl.SSLError`, the CLI prints a dedicated hint pointing the user at the trust store env vars (or `http://` URLs for plain HTTP) rather than the generic "Is the server running?" message.

**Hostname verification.** Always on. The dev cert script includes the right SANs (`<service-name>`, `localhost`, `127.0.0.1`) so verification passes without insecure fallbacks.

### 4. Dev cert generation

A `scripts/generate_dev_certs.py` script generates development-only material in `certs/` (gitignored). The first run creates a complete bundle and the `.dev-ca` marker. Re-running a complete marked bundle is a no-op; unmarked or partial material is rejected without overwriting it. Pass `--force` only when explicitly replacing a complete marked development bundle. Publication is staged so a failed generation preserves the previous valid bundle.

**Library: `cryptography`** (a runtime dependency because the orchestrator parses certificates for status and reload). Standard Python library for cert generation, no system dependencies, no shell-outs. Alternative approaches considered:
- `openssl` shell-out — rejected: platform-specific, ugly, harder to test.
- `pyOpenSSL` — rejected: wrapper layer over cryptography that adds no value here.

**Cert structure.**

```
certs/
├── acheron-ca.crt       # CA cert (PEM)
├── acheron-ca.key       # CA private key (PEM, no passphrase)
├── orchestrator.crt     # server cert
├── orchestrator.key
├── tts-stub.crt
├── tts-stub.key
├── asr-stub.crt
├── asr-stub.key
├── translation-stub.crt
├── translation-stub.key
├── tts-grpc-stub.crt    # gRPC worker cert
└── tts-grpc-stub.key    # historical HTTP worker-edge cert
```

**Cert details.**
- 2048-bit RSA keys, 1-year validity
- CA: `CN=Acheron Dev CA, O=Acheron`
- Each server cert: `CN=<service-name>`, `subjectAltName=DNS:<service-name>,DNS:localhost,IP:127.0.0.1`
- gRPC cert uses the same SAN pattern (gRPC clients check SAN by default)

**Entry point.** `just certs` runs `uv run python scripts/generate_dev_certs.py` and safely reuses existing marked development material. `just certs --force` is the explicit regeneration path; other arguments are rejected.

**`.gitignore`.** `certs/` added so generated dev certs never get committed.

### 5. Compose integration

The Compose stack mounts the host `./certs` directory at `/certs` (read-only) for services that need the development CA. The one-shot `certs-init` service mounts it read/write, creates a complete marked bundle on first start, reuses it on later starts, and refuses unmarked or partial material. Bind mounting keeps development certs visible on the host for inspection.

**Service config.** Compose enables TLS on the orchestrator and keeps local worker/dashboard listeners on their existing protocols:

| Service | Server configuration | Client configuration | URL/protocol |
|---|---|---|---|
| `orchestrator` | `ACHERON_TLS_{CERT,KEY}_FILE=/certs/{orchestrator.crt,orchestrator.key}` | `ACHERON_TLS_CA_FILE` and `SSL_CERT_FILE` point to `/certs/acheron-ca.crt` | serves HTTPS on port 8000 |
| local HTTP stubs | no server TLS env vars | `SSL_CERT_FILE=/certs/acheron-ca.crt` for calls to the orchestrator | serve HTTP on ports 8001–8003; register over HTTPS |
| `tts-grpc-stub` (historical name) | no server TLS env vars in Compose | `SSL_CERT_FILE=/certs/acheron-ca.crt` for calls to the orchestrator | serves an HTTP worker edge on port 9002; registers over HTTPS |
| `dashboard` | — | `SSL_CERT_FILE=/certs/acheron-ca.crt` for its orchestrator client | serves plaintext HTTP on port 8080 |

The local stubs advertise their existing `http://host:port` worker endpoints; only their client connection to the HTTPS orchestrator is TLS-protected. A separately deployed service that instantiates an HTTP or gRPC server can opt into server TLS with its per-service certificate; the current Compose and integration fixtures use HTTP worker edges.

**Orchestrator's view.** The orchestrator reads the worker endpoint from each registration payload. Compose local stubs advertise HTTP endpoints, while the integration TLS fixture advertises HTTPS endpoints; the existing HTTP transport passes the selected URL scheme to httpx. The gRPC client reads `ACHERON_TLS_CA_FILE` and verifies the CA when configured.

**Ports.** Unchanged. TLS is transport-level, not port-level, and the `ports: 8000:8000` mapping stays.

**Healthchecks.** The orchestrator healthcheck uses the development CA for HTTPS:

```yaml
test:
  - "CMD-SHELL"
  - "python"
  - "-c"
  - "import os, ssl, urllib.request; ctx = ssl.create_default_context(cafile=os.environ.get('SSL_CERT_FILE')); urllib.request.urlopen('https://localhost:8000/health', context=ctx).read()"
```

The dashboard and local worker healthchecks remain plain HTTP (`http://localhost:<port>/health`). The service named `tts-grpc-stub` uses the same FastAPI/Uvicorn HTTP worker edge on port 9002, so its Docker healthcheck is a direct `http://localhost:9002/health` request; there is no HTTP sidecar or gRPC listener in the current Compose service. Compose therefore matches the current stack: orchestrator HTTPS, local worker/dashboard HTTP, and TLS on worker-to-orchestrator client connections.

**Opt-in.** The shared service implementation supports TLS when both server certificate variables are set. Compose sets those variables only for the orchestrator; a deployer can configure additional service listeners with externally managed SAN-correct certificates without changing application code.

### 6. Documentation

**README — new "TLS" subsection** under "Deployment", between "Docker Compose" and "Configuration":

> ### TLS
>
> Acheron services serve TLS when configured. Three env vars control it:
>
> - `ACHERON_TLS_CERT_FILE` + `ACHERON_TLS_KEY_FILE` — server-side; both must be set together
> - `SSL_CERT_FILE` — client-side (used by httpx and stdlib `ssl`); set to the Acheron CA
>
> **Local dev (self-signed).** Run `just certs` to generate a local Acheron CA and per-service certs in `certs/`. The compose file mounts `certs/` into every service and sets the env vars. The CA is trusted by all services via `SSL_CERT_FILE`. A complete `.dev-ca` bundle is reused on later starts; `just certs --force` is the explicit replacement path. Use `acheron certs status` with `ACHERON_ADMIN_TOKEN` to inspect expiry and `acheron certs reload` after installing a valid replacement pair.
>
> **Production.** The development generator is not a production certificate workflow. Generate or obtain externally managed certificates with the required SANs (Let's Encrypt via cert-manager, your CA, etc.), mount them into each service, and set the env vars. No Acheron code change.
>
> **Reverse proxy (optional).** Acheron doesn't ship a proxy. To put nginx, Caddy, or anything else in front, point it at the orchestrator (HTTPS) and dashboard (HTTP) and terminate TLS there. Acheron's `ACHERON_TLS_*` env vars are independent of any proxy you add.
>
> **Disabling TLS.** Leave `ACHERON_TLS_CERT_FILE` and `ACHERON_TLS_KEY_FILE` unset for a service that should retain its existing listener protocol. In Compose, the orchestrator remains HTTPS while local worker edges and the dashboard remain HTTP; the worker clients still use their configured HTTPS orchestrator URL when registering. Useful for local dev without certs on the service being configured.

**Master spec note.** One paragraph added to `docs/superpowers/specs/architecture.md`'s Production Hardening section, plus a status-table update:

> Acheron services support TLS via environment variables; cert provenance and proxying are the deployer's responsibility. See the Layer 7c sub-spec for the env-var contract, dev cert script, and compose integration.

**Roadmap status update.** Layer 7c row updated from `planned` to `done` after implementation.

## Test Strategy

**Unit tests for `src/acheron/tls.py`** (`tests/test_tls.py`):
- `CertificateManager.status()` reports subject, expiry, remaining time, and severity
- threshold monitoring emits the 30-day, 7-day, 1-day, and expiry messages once per manager
- `CertificateManager.reload()` validates replacement material before updating the persistent context
- invalid replacement material leaves the existing context usable
- missing TLS pairs keep the manager disabled

**Unit tests for the dev cert script** (`tests/scripts/test_generate_dev_certs.py`):
- A complete marked bundle is idempotent: running twice does not rewrite managed files
- An unmarked existing bundle is rejected without mutation
- An incomplete marked bundle is rejected without mutation
- `--force` replaces only a complete marked development bundle
- Each cert's SAN includes the service name + `localhost` + `127.0.0.1`
- The CA cert is loadable by `ssl.SSLContext.load_verify_locations`
- A test `ssl.SSLContext` with the cert chain + key successfully completes a handshake against a loopback client that trusts the CA

**Integration tests** (`tests/integration/test_tls.py`):
- Spins up the orchestrator + two HTTP worker-edge stubs (including the historically named `tts-grpc-stub`) with TLS env vars set, mounts `certs/` from a session-scoped fixture that runs `generate_dev_certs.py`
- Verifies:
  - Orchestrator's `/health` and `/version` return 200 over HTTPS
  - Both HTTP worker edges, including the historically named `tts-grpc-stub`, register over HTTPS (worker → orchestrator direction)
  - Certificate replacement and admin reload preserve the orchestrator PID, healthy API, and registered worker connectivity

**Healthcheck script test** (`tests/shell/test_healthcheck.py`):
- Runs the healthcheck Python one-liner against a `pytest-httpserver` instance
- Verifies it returns 0 when the server is up, 1 when down
- Verifies it correctly loads the CA from `SSL_CERT_FILE`

**Coverage target.** Same as the rest of the codebase: 80% minimum on `src/acheron/tls.py` and 95%+ on the cert script.

Per AGENTS.md: tests don't depend on hardcoded paths. The cert path fixture uses `tmp_path` and the cert script's output dir is configurable.

## Files

### New

- `src/acheron/tls.py` — certificate status, expiry monitoring, and persistent reloadable context
- `scripts/generate_dev_certs.py` — non-destructive development certificate generation using `cryptography`
- `tests/test_tls.py` — certificate-manager unit tests
- `tests/scripts/test_generate_dev_certs.py` — unit tests for the cert script
- `tests/integration/test_tls.py` — integration tests against the live TLS stack, including same-process reload

### Modified

- `docker-compose.yml` — `certs/` bind mount, per-service TLS env vars, HTTPS healthchecks, `WORKER_ENDPOINT` URL scheme updates
- `stubs/worker_stub.py` — `uvicorn.run(..., **uvicorn_ssl_kwargs())`
- `stubs/translation_stub.py` — same
- `stubs/grpc_worker_stub.py` — `grpc.aio.server(credentials=grpc_server_credentials())`
- `src/acheron/shell/transports/grpc.py` — `grpc.aio.secure_channel(target, grpc_channel_credentials())` when CA is set
- `Justfile` — `certs` target with safe default reuse and explicit `--force`
- `pyproject.toml` — `cryptography~=46.0` runtime dependency for certificate parsing and generation
- `.gitignore` — `certs/`
- `README.md` — new "TLS" subsection in Deployment; new env vars in Configuration table
- `docs/superpowers/specs/architecture.md` — Production Hardening paragraph; status table update
- `docs/superpowers/specs/roadmap.md` — Layer 7c status: `done`

### Unchanged

- All stores, plans, executors, planner, dashboard — unrelated to TLS

## Dependencies

- **Runtime dependency:** `cryptography~=46.0` is required because the orchestrator parses certificates for status and reload, and the development generator uses the same library. It is declared in the project runtime dependencies and locked in `uv.lock`.
- `uvicorn` (HTTP) and `grpcio` (gRPC) provide the TLS transport primitives; no additional runtime TLS dependency is needed.

## Out of Scope

- **Reverse proxy** (nginx, caddy) — explicitly excluded. Acheron doesn't ship a proxy; deployers wire their own.
- **mTLS / client certs** — single-sided TLS only. Workers authenticate via the existing `ACHERON_REGISTRATION_TOKEN`, not via client certs.
- **ACME / Let's Encrypt client** — use cert-manager, Caddy, Traefik, or your CA of choice. Acheron doesn't speak ACME.
- **Cert rotation automation** — deployer concern.
- **TLS for Redis** — internal Docker network traffic stays plaintext.
- **Dashboard TLS** — dashboard ships in the same node as the orchestrator; the orchestrator's HTTPS endpoint is the public surface.
- **Resource limits** — explicitly deferred per Layer 7b decision; not part of 7c either.
