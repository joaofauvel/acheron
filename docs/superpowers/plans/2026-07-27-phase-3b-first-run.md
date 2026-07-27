# Phase 3b First-Run Journey Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add an opt-in, README-verbatim first-run journey harness that boots a fresh Compose checkout, verifies certificates, dashboard binding, worker registration, and registration-token wiring, and runs through `just first-run --step <N>` and CI.

**Architecture:** A pytest plugin local to `tests/first_run/` registers the explicit `--first-run` and `--step` options and skips Docker tests during normal test runs. Session fixtures create a temporary `git archive HEAD` checkout, prepare the README environment, start the real foreground Compose stack with a unique project name, poll host-facing HTTPS/HTTP endpoints, and guarantee Compose teardown. Three step modules assert the README command sequence, startup readiness, and end-to-end success criteria.

**Tech Stack:** Python 3.14, pytest, standard-library `subprocess`/`tarfile`/`urllib`/`ssl`, Docker Compose, Just, GitHub Actions.

## Global Constraints

- Preserve the Phase 3b scope: harness and CI only; do not change production application behavior or Compose services.
- Execute the README Quick Start command sequence: `cp .env.example .env`, `export ACHERON_REGISTRATION_TOKEN="$(openssl rand -hex 32)"`, and `docker compose up --build`.
- Run the journey from a temporary archive of `HEAD`, not the developer working tree.
- Use a unique `COMPOSE_PROJECT_NAME` and always run `docker compose down --volumes --remove-orphans` during cleanup.
- Do not add Python dependencies; use existing pytest and standard-library facilities.
- Keep ordinary `just test` free of Docker startup; first-run tests run only with `--first-run`.
- Preserve production TLS and registration behavior.
- Report step-specific user-journey errors and include captured Compose output for startup failures.
- Use xdist disabled for the first-run command because the Compose lifecycle is session-scoped.
- Use Conventional Commits with concise scopes.

---

## File Map

- Create: `tests/first_run/__init__.py` — package marker for the isolated journey test package.
- Create: `tests/first_run/conftest.py` — pytest options, collection gating, and session fixtures.
- Create: `tests/first_run/helpers.py` — README extraction, temporary checkout, environment setup, Compose lifecycle, HTTP probes, and diagnostics.
- Create: `tests/first_run/test_helpers.py` — pure helper behavior tests that do not require Docker.
- Create: `tests/first_run/test_1_quick_start.py` — README command-sequence and environment-preparation assertions.
- Create: `tests/first_run/test_2_compose_start.py` — Compose startup and readiness assertions.
- Create: `tests/first_run/test_3_success_criteria.py` — dashboard, worker, registration-token, and security-warning assertions.
- Modify: `Dockerfile` — install the dashboard wheel extras without shell glob ambiguity.
- Modify: `docker-compose.yml` — keep generated private keys non-world-readable while allowing the non-root orchestrator to read its key.
- Modify: `Justfile` — add the variadic `first-run` target.
- Modify: `README.md` — document the first-run journey target in the development commands.
- Create: `.github/workflows/first-run.yml` — CI execution of the first-run journey.

## Task 1: Add the opt-in first-run pytest surface and pure helpers

**Files:**
- Create: `tests/first_run/__init__.py`
- Create: `tests/first_run/conftest.py`
- Create: `tests/first_run/helpers.py`
- Test: `tests/first_run/test_helpers.py`

**Interfaces:**
- Produces `extract_quick_start_commands(readme_text: str) -> tuple[str, ...]`.
- Produces pytest options `--first-run` and `--step`, where `--step` accepts `1`, `2`, or `3`.
- Ordinary pytest runs skip items below `tests/first_run/` unless `--first-run` is present.
- With `--step N`, only tests whose names contain `test_step_N` run.

- [ ] **Step 1: Write failing parser and collection-gate tests**

Create `tests/first_run/test_helpers.py` with pure tests for the README parser:

```python
from tests.first_run.helpers import extract_quick_start_commands


def test_extract_quick_start_commands_reads_only_the_quick_start_fence() -> None:
    readme = """# Acheron

## Quick Start

```bash
cp .env.example .env
export ACHERON_REGISTRATION_TOKEN=\"$(openssl rand -hex 32)\"
docker compose up --build
```

## Other Commands

```bash
acheron status
```
"""

    assert extract_quick_start_commands(readme) == (
        "cp .env.example .env",
        'export ACHERON_REGISTRATION_TOKEN="$(openssl rand -hex 32)"',
        "docker compose up --build",
    )


def test_extract_quick_start_commands_rejects_a_missing_section() -> None:
    try:
        extract_quick_start_commands("# No quick start")
    except ValueError as exc:
        assert str(exc) == "README Quick Start section not found"
    else:
        raise AssertionError("expected a missing Quick Start section to fail")
```

Add an option/selection test fixture to `conftest.py` only after the option names are defined; the collection hook must use item names rather than marker registration so `--strict-markers` needs no project-wide configuration changes.

- [ ] **Step 2: Run the focused parser tests to verify they fail**

Run:

```bash
uv run pytest --no-cov tests/first_run/test_helpers.py -q
```

Expected: FAIL with an import or missing-function error for `tests.first_run.helpers.extract_quick_start_commands`.

- [ ] **Step 3: Implement the parser and pytest gating**

In `helpers.py`, implement section extraction with explicit failure messages:

```python
def extract_quick_start_commands(readme_text: str) -> tuple[str, ...]:
    section, separator, remainder = readme_text.partition("## Quick Start")
    if not separator:
        raise ValueError("README Quick Start section not found")
    body = remainder.split("\n## ", 1)[0]
    fence_start = body.find("```bash")
    if fence_start < 0:
        raise ValueError("README Quick Start command fence not found")
    command_body = body[fence_start + len("```bash") :]
    fence_end = command_body.find("```")
    if fence_end < 0:
        raise ValueError("README Quick Start command fence is not closed")
    commands = tuple(line.strip() for line in command_body[:fence_end].splitlines() if line.strip())
    if not commands:
        raise ValueError("README Quick Start command fence is empty")
    return commands
```

In `conftest.py`, register the options and skip/filter only first-run items:

```python
def pytest_addoption(parser: pytest.Parser) -> None:
    parser.addoption("--first-run", action="store_true", help="run the Docker-backed first-run journey")
    parser.addoption("--step", choices=("1", "2", "3"), default=None, help="run one first-run journey step")


def pytest_collection_modifyitems(config: pytest.Config, items: list[pytest.Item]) -> None:
    if config.getoption("--first-run"):
        step = config.getoption("--step")
        if step is not None:
            selected = f"test_step_{step}"
            skip = pytest.mark.skip(reason=f"not selected by --step {step}")
            for item in items:
                if "first_run" in item.path.parts and selected not in item.name:
                    item.add_marker(skip)
        return
    skip = pytest.mark.skip(reason="Docker-backed first-run tests require --first-run")
    for item in items:
        if "first_run" in item.path.parts:
            item.add_marker(skip)
```

Use the existing pytest type imports and keep first-run test files under `tests/first_run/` so the path gate is deterministic.

- [ ] **Step 4: Run focused tests and the ordinary collection gate**

Run:

```bash
uv run pytest --no-cov tests/first_run/test_helpers.py -q
uv run pytest --no-cov tests/first_run --collect-only -q
```

Expected: parser tests PASS; ordinary collection reports first-run tests as skipped when no `--first-run` flag is present.

- [ ] **Step 5: Commit the pytest surface**

```bash
git add tests/first_run
git commit -m "test(first-run): add opt-in journey test surface"
```

## Task 2: Build the isolated checkout and README environment fixture

**Files:**
- Modify: `tests/first_run/conftest.py`
- Modify: `tests/first_run/helpers.py`
- Test: `tests/first_run/test_1_quick_start.py`

**Interfaces:**
- Produces `FirstRunProject` with `checkout: Path`, `token: str`, `env: dict[str, str]`, `compose_project: str`, and `log_path: Path`.
- Produces session fixture `prepared_project` that archives `HEAD`, runs the README copy command, and creates the token environment.
- `prepared_project` must be reusable by later Compose fixtures without rerunning setup.

- [ ] **Step 1: Write the failing environment and command-sequence test**

Create `test_1_quick_start.py`:

```python
from tests.first_run.helpers import EXPECTED_QUICK_START_COMMANDS, extract_quick_start_commands


def test_step_1_quick_start_commands_and_environment(prepared_project) -> None:
    readme = (prepared_project.checkout / "README.md").read_text()
    assert extract_quick_start_commands(readme) == EXPECTED_QUICK_START_COMMANDS, (
        "step 1: README Quick Start commands changed; update the journey only with an intentional design change"
    )
    assert (prepared_project.checkout / ".env").exists(), "step 1: README environment copy did not create .env"
    assert len(prepared_project.token) == 64, "step 1: generated registration token is not 32 bytes of hex"
    assert prepared_project.env["ACHERON_REGISTRATION_TOKEN"] == prepared_project.token
```

- [ ] **Step 2: Run the step-1 test to verify it fails**

Run:

```bash
uv run pytest --no-cov tests/first_run/test_1_quick_start.py --first-run --step 1 -n 0 -q
```

Expected: FAIL because `prepared_project`, `FirstRunProject`, and the checkout helper do not exist.

- [ ] **Step 3: Implement the fresh-checkout and environment helpers**

Define the expected command tuple in `helpers.py`:

```python
EXPECTED_QUICK_START_COMMANDS = (
    "cp .env.example .env",
    'export ACHERON_REGISTRATION_TOKEN="$(openssl rand -hex 32)"',
    "docker compose up --build",
)
```

Implement the checkout using `git archive HEAD` and `tarfile`, and execute the first two README operations in the archive:

```python
@dataclass(frozen=True)
class FirstRunProject:
    checkout: Path
    token: str
    env: dict[str, str]
    compose_project: str
    log_path: Path


def create_checkout(repo_root: Path, destination: Path) -> Path:
    archive_path = destination / "source.tar"
    subprocess.run(["git", "archive", "HEAD", "--output", str(archive_path)], cwd=repo_root, check=True)
    checkout = destination / "checkout"
    checkout.mkdir()
    with tarfile.open(archive_path) as archive:
        archive.extractall(checkout)
    return checkout


def prepare_project(repo_root: Path, destination: Path) -> FirstRunProject:
    checkout = create_checkout(repo_root, destination)
    subprocess.run(["cp", ".env.example", ".env"], cwd=checkout, check=True)
    token = subprocess.run(
        ["openssl", "rand", "-hex", "32"], check=True, capture_output=True, text=True
    ).stdout.strip()
    if len(token) != 64 or any(character not in string.hexdigits for character in token):
        raise AssertionError("step 1: openssl did not produce a 32-byte hexadecimal token")
    env = os.environ | {"ACHERON_REGISTRATION_TOKEN": token}
    compose_project = f"acheron-first-run-{uuid.uuid4().hex[:12]}"
    log_path = destination / "compose.log"
    return FirstRunProject(checkout, token, env, compose_project, log_path)
```

Use `tarfile` extraction only on the trusted archive produced by the local repository. Set `COMPOSE_PROJECT_NAME` in `env` when the fixture returns the project. The `prepared_project` fixture should use `tmp_path_factory.mktemp("first-run")` and resolve the repository root from `Path(__file__).parents[2]`.

- [ ] **Step 4: Run step 1 and verify the normal suite does not start Docker**

Run:

```bash
uv run pytest --no-cov tests/first_run/test_1_quick_start.py --first-run --step 1 -n 0 -q
uv run pytest --no-cov tests/first_run/test_1_quick_start.py -n 0 -q
```

Expected: step 1 PASS; the second command skips the journey test without invoking Docker.

- [ ] **Step 5: Commit the isolated environment task**

```bash
git add tests/first_run
git commit -m "test(first-run): execute quick-start preparation in fresh checkout"
```

## Task 3: Add the real Compose lifecycle and startup assertions

**Files:**
- Modify: `Dockerfile`
- Modify: `docker-compose.yml`
- Modify: `tests/first_run/conftest.py`
- Modify: `tests/first_run/helpers.py`
- Create: `tests/first_run/test_2_compose_start.py`

**Interfaces:**
- Produces session fixture `compose_stack` that starts the exact `docker compose up --build` command and stops it in teardown.
- Produces `ComposeStack` with the `FirstRunProject`, `Popen` handle, log file, and CA certificate path.
- Produces `HttpResponse(status: int, headers: Mapping[str, str], body: bytes)` for status-preserving API assertions.
- `wait_for_http` polls HTTPS with the generated CA and HTTP without TLS.

- [ ] **Step 1: Write the failing Compose startup test**

Create `test_2_compose_start.py`:

```python

def test_step_2_compose_start(compose_stack) -> None:
    orchestrator = compose_stack.get_json("https://localhost:8000/health")
    assert orchestrator == {"status": "ok"}, "step 2: orchestrator did not become healthy"
    dashboard = compose_stack.get_text("http://localhost:8080/")
    assert "Acheron" in dashboard, "step 2: dashboard did not render its index page"
```

- [ ] **Step 2: Run the step-2 test to verify it fails**

Run:

```bash
uv run pytest --no-cov tests/first_run/test_2_compose_start.py --first-run --step 2 -n 0 -q
```

Expected: FAIL because the Compose lifecycle fixture and HTTP probe methods do not exist.

- [ ] **Step 3: Implement packaging prerequisites, Compose startup, polling, and cleanup**

The first real Compose run must pass two packaging prerequisites before the harness can reach readiness: quote the dashboard wheel extra through a positional shell parameter (`RUN set -- ./*.whl && pip install --no-cache-dir \"$1[dashboard]\" && rm \"$1\"`), and keep generated key files at mode `0640` with the orchestrator in supplementary group `0`. These changes preserve non-root containers and prevent world-readable private keys.

In `helpers.py`, use a foreground process and capture its log. Launching and readiness are separate so the fixture owns the process even when readiness fails:

```python
def launch_compose(project: FirstRunProject) -> ComposeStack:
    log_file = project.log_path.open("w")
    process = subprocess.Popen(
        ["docker", "compose", "up", "--build"],
        cwd=project.checkout,
        env=project.env | {"COMPOSE_PROJECT_NAME": project.compose_project},
        stdout=log_file,
        stderr=subprocess.STDOUT,
        start_new_session=True,
    )
    return ComposeStack(project, process, log_file)
```

In `conftest.py`, own readiness and teardown in a session fixture:

```python
@pytest.fixture(scope="session")
def compose_stack(prepared_project: FirstRunProject) -> Iterator[ComposeStack]:
    stack = launch_compose(prepared_project)
    try:
        try:
            stack.wait_until_ready(timeout_seconds=240)
        except Exception as exc:
            raise AssertionError(
                f"step 2: Compose startup failed; see {prepared_project.log_path}\\n{stack.log_tail()}"
            ) from exc
        yield stack
    finally:
        stop_compose_best_effort(stack)
```

`wait_until_ready` must poll both `https://localhost:8000/health` using `ssl.create_default_context(cafile=checkout / "certs" / "acheron-ca.crt")` and `http://localhost:8080/`, checking the child process each iteration. Sleep one second between attempts and raise after 240 seconds. `get_json` and `get_text` should use `urllib.request.urlopen` with a five-second timeout and return decoded response data.

`ComposeStack.request` must return `HttpResponse` without raising on HTTP status errors, so the success step can assert both 401 and 201 responses:

```python
@dataclass(frozen=True)
class HttpResponse:
    status: int
    headers: Mapping[str, str]
    body: bytes
```

Teardown must preserve an earlier test exception:

```python
def stop_compose_best_effort(stack: ComposeStack) -> None:
    try:
        try:
            os.killpg(stack.process.pid, signal.SIGINT)
            stack.process.wait(timeout=30)
        except (OSError, subprocess.TimeoutExpired):
            try:
                stack.process.kill()
            except OSError:
                pass
            try:
                stack.process.wait(timeout=10)
            except (OSError, subprocess.TimeoutExpired):
                pass
        try:
            stack.log_file.close()
        except OSError:
            pass
        subprocess.run(
            ["docker", "compose", "down", "--volumes", "--remove-orphans"],
            cwd=stack.project.checkout,
            env=stack.project.env | {"COMPOSE_PROJECT_NAME": stack.project.compose_project},
            check=False,
            timeout=60,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
    except (OSError, subprocess.SubprocessError):
        pass
```

The fixture must call `stop_compose_best_effort` from `finally`; cleanup exceptions must be suppressed in the fixture teardown so they cannot replace a test assertion or startup error. Use `-n 0` in all first-run invocations so the session fixture is not duplicated.

- [ ] **Step 4: Run step 2 and verify cleanup**

Run:

```bash
uv run pytest --no-cov tests/first_run/test_2_compose_start.py --first-run --step 2 -n 0 -q
```

Expected: PASS on a Docker-capable host, with the temporary Compose project removed afterward. If Docker is unavailable, the failure must include the step label and Compose log path.

- [ ] **Step 5: Commit the Compose lifecycle task**

```bash
git add tests/first_run
git commit -m "test(first-run): exercise fresh Compose startup"
```

## Task 4: Assert the complete first-run success criteria

**Files:**
- Create: `tests/first_run/test_3_success_criteria.py`
- Modify: `tests/first_run/helpers.py`

**Interfaces:**
- `ComposeStack.request(path, headers, method, body)` returns status, headers, and decoded bytes for host-facing HTTP/HTTPS calls.
- The success test uses the existing `/workers` and `/partials/status` endpoints; it does not add an application route.

- [ ] **Step 1: Write the failing success-criteria test**

Create `test_3_success_criteria.py`:

```python

def test_step_3_first_run_success_criteria(compose_stack) -> None:
    auth = {"Authorization": f"Bearer {compose_stack.project.token}"}
    status_body = compose_stack.get_text("http://localhost:8080/partials/status")
    assert "dot-green" in status_body, "step 3: dashboard cannot reach the orchestrator"

    workers = compose_stack.get_json("https://localhost:8000/workers", headers=auth)["workers"]
    assert any(worker["status"] == "healthy" for worker in workers), (
        "step 3: no healthy worker registered with the orchestrator"
    )

    probe = {
        "worker_id": "first-run-token-probe",
        "endpoint": "http://first-run-token-probe:8001",
        "transport": "http",
        "capabilities": {
            "worker_type": "tts",
            "supported_languages_in": ["en"],
            "supported_languages_out": ["es"],
        },
    }
    rejected = compose_stack.request("https://localhost:8000/workers", method="POST", body=probe, headers={"Authorization": "Bearer invalid"})
    assert rejected.status == 401, "step 3: invalid registration token was accepted"
    accepted = compose_stack.request("https://localhost:8000/workers", method="POST", body=probe, headers=auth)
    assert accepted.status == 201, "step 3: generated registration token was rejected"

    log = compose_stack.log_tail()
    assert "ACHERON_REGISTRATION_TOKEN is unset" not in log, "step 3: startup reported an unset registration token"
    assert "ACHERON_OPEN_REGISTRATION=1" not in log, "step 3: startup reported open registration"
```

- [ ] **Step 2: Run step 3 to verify it fails before the probes exist**

Run:

```bash
uv run pytest --no-cov tests/first_run/test_3_success_criteria.py --first-run --step 3 -n 0 -q
```

Expected: FAIL with missing request/probe helpers or a missing success-criteria assertion.

- [ ] **Step 3: Implement request and diagnostic helpers**

Implement `ComposeStack.request` with `urllib.request.Request`, JSON encoding for bodies, the per-stack CA context for HTTPS, and an `HTTPStatusError`-free response object that preserves 401/201 statuses for assertions. Decode JSON only in `get_json`; keep response text available for dashboard/log assertions.

Use the known registration schema above. The invalid-token request must use the same valid body as the accepted request so a 401 proves authentication rather than body validation. The accepted probe may remain registered until Compose teardown; it must not be used as the healthy-worker assertion.

- [ ] **Step 4: Run focused success tests and the whole journey**

Run:

```bash
uv run pytest --no-cov tests/first_run --first-run --step 3 -n 0 -q
uv run pytest --no-cov tests/first_run --first-run -n 0 -q
```

Expected: step 3 PASS; all three steps PASS in one Compose lifecycle.

- [ ] **Step 5: Commit the success-criteria task**

```bash
git add tests/first_run
git commit -m "test(first-run): verify registration and dashboard readiness"
```

## Task 5: Add the Just, README, and CI interfaces

**Files:**
- Modify: `Justfile`
- Modify: `README.md`
- Create: `.github/workflows/first-run.yml`

**Interfaces:**
- `just first-run` runs every first-run step.
- `just first-run --step 1`, `--step 2`, and `--step 3` run only the selected step.
- CI runs the same `just first-run` command from a clean checkout.

- [ ] **Step 1: Write the command-interface check**

Add a small shell-level test in the plan execution session before editing the Justfile:

```bash
just --dry-run first-run --step 1
```

Expected before implementation: failure because the recipe does not exist. This is the command contract that the new recipe must satisfy.

- [ ] **Step 2: Add the Just target**

Append to `Justfile`:

```just
# Run the Phase 3b README-verbatim first-run journey. Requires Docker.
first-run *args:
    uv run pytest tests/first_run --first-run -n 0 -q {{args}}
```

The variadic argument preserves the documented `just first-run --step <N>` interface while allowing future pytest filters without another Just target.

- [ ] **Step 3: Document the target in README development commands**

Add one concise bullet beside the existing validation commands:

```markdown
- `just first-run [--step N]` — run the fresh-checkout README deployment journey in Docker.
```

Do not alter the Quick Start command block; the first-run parser intentionally treats it as the source of truth.

- [ ] **Step 4: Add the CI workflow**

Create `.github/workflows/first-run.yml`:

```yaml
name: First-run journey

on:
  push:
    branches: [master]
  pull_request:
    paths:
      - README.md
      - .env.example
      - Dockerfile
      - docker-compose.yml
      - Justfile
      - scripts/generate_dev_certs.py
      - tests/first_run/**
      - .github/workflows/first-run.yml
      - pyproject.toml
      - uv.lock

jobs:
  first-run:
    runs-on: ubuntu-latest
    timeout-minutes: 5
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with:
          python-version: '3.14'
      - name: Install uv
        run: pip install uv
      - name: Install just
        uses: taiki-e/install-action@just
      - name: Install project dependencies
        run: uv sync --all-extras --all-packages
      - name: Run first-run journey
        run: just first-run
```

The workflow must not provide RunPod or Hugging Face secrets; the journey is intentionally local-only.

- [ ] **Step 5: Verify the command interface and documentation diff**

Run:

```bash
just --dry-run first-run
just --dry-run first-run --step 2
git diff --check
```

Expected: both dry runs show pytest with `--first-run -n 0`, and the second includes `--step 2`; documentation contains the target without changing the Quick Start commands.

- [ ] **Step 6: Commit the interfaces and CI workflow**

```bash
git add Justfile README.md .github/workflows/first-run.yml
git commit -m "ci(first-run): add README journey command and workflow"
```

## Task 6: Run the complete Phase 3b verification matrix

**Files:**
- No planned source changes; fix only failures directly attributable to the Phase 3b implementation.

**Interfaces:**
- Consumes all first-run harness, Justfile, README, and CI changes.
- Produces passing local evidence for each step, the aggregate journey, project gates, and UX validation.

- [ ] **Step 1: Run the ordinary project gate**

Run:

```bash
just validate
```

Expected: lint, import boundaries, both type checkers, and the ordinary test suite pass; first-run Docker tests remain skipped unless explicitly enabled.

- [ ] **Step 2: Run each first-run step independently**

Run:

```bash
just first-run --step 1
just first-run --step 2
just first-run --step 3
```

Expected: each command exits zero and reports the step-specific journey test as passed; each invocation removes its temporary Compose project.

- [ ] **Step 3: Run the aggregate journey**

Run:

```bash
just first-run
```

Expected: all three steps pass in one Compose lifecycle within five minutes.

- [ ] **Step 4: Run the UX rubric gate and inspect cleanup**

Run:

```bash
just ux-validate
git diff --check
docker compose ls --format json
git status --short --branch
```

Expected: UX validation passes, no first-run Compose project remains, and only intended Phase 3b changes are present.

- [ ] **Step 5: Commit any direct verification corrections atomically**

If a correction is required, use a separate concise Conventional Commit that names the failing interface, for example:

```bash
git add <corrected-files>
git commit -m "fix(first-run): preserve compose failure diagnostics"
```

Do not weaken assertions or skip a step to make the matrix pass.
