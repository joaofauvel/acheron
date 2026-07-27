"""Helpers for the README first-run journey."""

from __future__ import annotations

import os
import string
import subprocess
import tarfile
import uuid
from dataclasses import dataclass
from pathlib import Path

EXPECTED_QUICK_START_COMMANDS = (
    "cp .env.example .env",
    'export ACHERON_REGISTRATION_TOKEN="$(openssl rand -hex 32)"',
    "docker compose up --build",
)


@dataclass(frozen=True)
class FirstRunProject:
    """Temporary checkout and environment for one first-run journey."""

    checkout: Path
    token: str
    env: dict[str, str]
    compose_project: str
    log_path: Path


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


def prepare_project(repo_root: Path, destination: Path) -> FirstRunProject:
    """Prepare the README environment in a fresh checkout."""
    checkout = create_checkout(repo_root, destination)
    subprocess.run(["cp", ".env.example", ".env"], cwd=checkout, check=True)
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
            "ACHERON_REGISTRATION_TOKEN": token,
            "COMPOSE_PROJECT_NAME": compose_project,
        }
    )
    return FirstRunProject(checkout, token, env, compose_project, destination / "compose.log")
