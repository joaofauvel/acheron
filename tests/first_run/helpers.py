"""Helpers for the README first-run journey."""

from __future__ import annotations

import contextlib
import json
import os
import shutil
import signal
import ssl
import string
import subprocess
import tarfile
import time
import urllib.error
import urllib.request
import uuid
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import TextIO

EXPECTED_QUICK_START_COMMANDS = (
    "cp .env.example .env",
    "docker compose up --build",
)


@dataclass(frozen=True)
class HttpResponse:
    """HTTP response with status preserved for journey assertions."""

    status: int
    headers: Mapping[str, str]
    body: bytes


@dataclass(frozen=True)
class FirstRunProject:
    """Temporary checkout and environment for one first-run journey."""

    checkout: Path
    token: str
    env: dict[str, str]
    compose_project: str
    log_path: Path


@dataclass
class ComposeStack:
    """A running Compose stack and its first-run diagnostics."""

    project: FirstRunProject
    process: subprocess.Popen[bytes]
    log_file: TextIO

    @property
    def ca_file(self) -> Path:
        return self.project.checkout / "certs" / "acheron-ca.crt"

    def _ssl_context(self, url: str) -> ssl.SSLContext | None:
        if not url.startswith("https://"):
            return None
        return ssl.create_default_context(cafile=self.ca_file)

    def request(
        self,
        url: str,
        *,
        method: str = "GET",
        body: object | None = None,
        headers: Mapping[str, str] | None = None,
    ) -> HttpResponse:
        """Send an HTTP request while preserving non-success statuses."""
        data = None if body is None else json.dumps(body).encode()
        request_headers = dict(headers or {})
        if body is not None:
            request_headers.setdefault("Content-Type", "application/json")
        request = urllib.request.Request(url, data=data, headers=request_headers, method=method)
        try:
            response = urllib.request.urlopen(request, context=self._ssl_context(url), timeout=5)
        except urllib.error.HTTPError as error:
            return HttpResponse(error.code, dict(error.headers.items()), error.read())
        with response:
            return HttpResponse(response.status, dict(response.headers.items()), response.read())

    def get_text(self, url: str, headers: Mapping[str, str] | None = None) -> str:
        """Fetch a successful text endpoint using the stack's generated CA when needed."""
        response = self.request(url, headers=headers)
        if not 200 <= response.status < 300:
            message = f"HTTP {response.status} from {url}"
            raise OSError(message)
        return response.body.decode()

    def get_json(self, url: str, headers: Mapping[str, str] | None = None) -> object:
        """Fetch and decode a successful JSON endpoint."""
        return json.loads(self.get_text(url, headers))

    def log_text(self) -> str:
        """Return the complete flushed Compose log."""
        self.log_file.flush()
        if not self.project.log_path.exists():
            return "<Compose log is unavailable>"
        return self.project.log_path.read_text()

    def log_tail(self, lines: int = 80) -> str:
        """Return the most recent Compose log lines."""
        return "".join(self.log_text().splitlines(keepends=True)[-lines:])

    def wait_until_ready(self, timeout_seconds: float) -> None:
        """Wait for orchestrator HTTPS and dashboard HTTP readiness."""
        deadline = time.monotonic() + timeout_seconds
        last_error: Exception | None = None
        while time.monotonic() < deadline:
            if self.process.poll() is not None:
                status = self.process.returncode
                log = self.log_tail()
                message = f"Compose exited with status {status}\n{log}"
                raise RuntimeError(message)
            try:
                health = self.get_json("https://localhost:8000/health")
                self.get_text("http://localhost:8080/")
            except (OSError, TimeoutError, ValueError, ssl.SSLError, urllib.error.URLError) as exc:
                last_error = exc
            else:
                if health == {"status": "ok"}:
                    return
                last_error = ValueError("orchestrator health response was not ready")
            time.sleep(1)
        log = self.log_tail()
        message = f"services did not become ready: {last_error}\n{log}"
        raise TimeoutError(message)


def file_backed_environment(environment: Mapping[str, str]) -> dict[str, str]:
    """Remove the explicit registration token for Compose file-backed mode."""
    return {key: value for key, value in environment.items() if key != "ACHERON_REGISTRATION_TOKEN"}


def compose_config_for_file_backed_mode(project: FirstRunProject) -> subprocess.CompletedProcess[str]:
    """Render Compose without an environment token for the file-backed contract."""
    token_directory = project.checkout / ".first-run-data" / "jobs"
    token_directory.mkdir(parents=True, exist_ok=True)
    (token_directory / ".registration_token").write_text("persisted-test-token\n", encoding="utf-8")
    environment = file_backed_environment(project.env)
    return subprocess.run(
        ["docker", "compose", "config"],
        cwd=project.checkout,
        env=environment,
        capture_output=True,
        text=True,
        check=False,
    )


def extract_quick_start_commands(readme_text: str) -> tuple[str, ...]:
    """Extract commands from the README Quick Start bash fence."""
    _, separator, remainder = readme_text.partition("## Quick Start")
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


def create_checkout(repo_root: Path, destination: Path) -> Path:
    """Create a checkout from the repository's current HEAD archive."""
    archive_path = destination / "source.tar"
    subprocess.run(
        ["git", "archive", "HEAD", "--output", str(archive_path)],
        cwd=repo_root,
        check=True,
    )
    checkout = destination / "checkout"
    checkout.mkdir()
    with tarfile.open(archive_path) as archive:
        archive.extractall(checkout)
    return checkout


def prepare_project(
    repo_root: Path,
    destination: Path,
    *,
    file_backed_token: bool = False,
) -> FirstRunProject:
    """Prepare the README environment in a fresh checkout."""
    checkout = create_checkout(repo_root, destination)
    subprocess.run(["cp", ".env.example", ".env"], cwd=checkout, check=True)
    token = ""
    if not file_backed_token:
        token = subprocess.run(
            ["openssl", "rand", "-hex", "32"],
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip()
        if len(token) != 64 or any(character not in string.hexdigits for character in token):
            raise AssertionError("step 1: openssl did not produce a 32-byte hexadecimal token")
    compose_project = f"acheron-first-run-{uuid.uuid4().hex[:12]}"
    env = dict(os.environ)
    env.update(
        {
            "COMPOSE_PROFILES": "sim",
            "COMPOSE_PROJECT_NAME": compose_project,
        }
    )
    if token:
        env["ACHERON_REGISTRATION_TOKEN"] = token
    else:
        env.pop("ACHERON_REGISTRATION_TOKEN", None)
    return FirstRunProject(checkout, token, env, compose_project, destination / "compose.log")


def launch_compose(project: FirstRunProject) -> ComposeStack:
    """Launch the README Compose command and capture its output."""
    log_file = project.log_path.open("w")
    try:
        process = subprocess.Popen(
            ["docker", "compose", "up", "--build"],
            cwd=project.checkout,
            env=project.env,
            stdout=log_file,
            stderr=subprocess.STDOUT,
            start_new_session=True,
        )
    except OSError:
        log_file.close()
        raise
    return ComposeStack(project, process, log_file)


def read_file_backed_token(project: FirstRunProject) -> str:
    """Read the generated token from the running orchestrator volume."""
    result = subprocess.run(
        ["docker", "compose", "exec", "-T", "orchestrator", "cat", "/data/jobs/.registration_token"],
        cwd=project.checkout,
        env=project.env,
        check=True,
        capture_output=True,
        text=True,
        timeout=30,
    )
    token = result.stdout.strip()
    if not token:
        raise AssertionError("step 3: file-backed token file was empty")
    return token


def stop_compose_best_effort(stack: ComposeStack, *, remove_certs: bool = True) -> None:
    """Stop Compose and remove its resources without masking test failures."""
    try:
        try:
            os.killpg(stack.process.pid, signal.SIGINT)
            stack.process.wait(timeout=30)
        except OSError, subprocess.TimeoutExpired:
            with contextlib.suppress(OSError):
                stack.process.kill()
            with contextlib.suppress(OSError, subprocess.TimeoutExpired):
                stack.process.wait(timeout=10)
        with contextlib.suppress(OSError, ValueError):
            stack.log_file.close()
        if remove_certs:
            # Remove root-owned bind-mounted certificate files before tearing down
            # the Compose project. Running `compose run` after `down` recreates the
            # project network and leaves it behind.
            with contextlib.suppress(OSError, subprocess.SubprocessError):
                subprocess.run(
                    [
                        "docker",
                        "compose",
                        "run",
                        "--rm",
                        "--no-deps",
                        "--entrypoint",
                        "sh",
                        "certs-init",
                        "-c",
                        "rm -rf /certs/*",
                    ],
                    cwd=stack.project.checkout,
                    env=stack.project.env,
                    check=False,
                    timeout=60,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                )
        with contextlib.suppress(OSError, subprocess.SubprocessError):
            subprocess.run(
                ["docker", "compose", "down", "--volumes", "--remove-orphans"],
                cwd=stack.project.checkout,
                env=stack.project.env,
                check=False,
                timeout=60,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
        if remove_certs:
            shutil.rmtree(stack.project.checkout / "certs", ignore_errors=True)
    except OSError, subprocess.SubprocessError:
        pass
