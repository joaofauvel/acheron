import subprocess
from pathlib import Path
from typing import cast

import pytest

from tests.first_run.helpers import (
    ComposeStack,
    FirstRunProject,
    cleanup_project_best_effort,
    extract_quick_start_commands,
    stop_compose_best_effort,
)


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
    with pytest.raises(ValueError, match="README Quick Start section not found"):
        extract_quick_start_commands("# No quick start")


def test_compose_log_text_includes_lines_beyond_the_diagnostic_tail(tmp_path: Path) -> None:
    log_path = tmp_path / "compose.log"
    log_path.write_text("security warning\n" + "\n".join(str(index) for index in range(100)))
    project = FirstRunProject(tmp_path, "token", {}, "project", log_path)
    with log_path.open("a") as log_file:
        stack = ComposeStack(project, cast("subprocess.Popen[bytes]", object()), log_file)
        assert stack.log_text().startswith("security warning\n")


def test_project_cleanup_removes_certificates_before_teardown(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[list[str]] = []
    monkeypatch.setattr("tests.first_run.helpers.subprocess.run", lambda command, **_kwargs: calls.append(command))
    project = FirstRunProject(tmp_path, "token", {}, "project", tmp_path / "compose.log")
    (tmp_path / "certs").mkdir()
    (tmp_path / "certs" / "root-owned.crt").write_text("certificate")

    cleanup_project_best_effort(project)

    assert calls[0][0:4] == ["docker", "compose", "run", "--rm"]
    assert calls[1][0:4] == ["docker", "compose", "down", "--volumes"]
    assert not (tmp_path / "certs").exists()


def test_compose_cleanup_removes_certificates_before_teardown(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[list[str]] = []
    monkeypatch.setattr("tests.first_run.helpers.os.killpg", lambda *_args: None)
    monkeypatch.setattr("tests.first_run.helpers.subprocess.run", lambda command, **_kwargs: calls.append(command))

    class FakeProcess:
        pid = 1

        def wait(self, **_kwargs: object) -> None:
            return None

        def kill(self) -> None:
            return None

    project = FirstRunProject(tmp_path, "token", {}, "project", tmp_path / "compose.log")
    with project.log_path.open("w") as log_file:
        process = cast("subprocess.Popen[bytes]", FakeProcess())
        stack = ComposeStack(project, process, log_file)
        stop_compose_best_effort(stack)

    assert calls[0][0:4] == ["docker", "compose", "run", "--rm"]
    assert calls[1][0:4] == ["docker", "compose", "down", "--volumes"]
