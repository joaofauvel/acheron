import subprocess
from pathlib import Path
from typing import cast

import pytest

from tests.first_run.helpers import ComposeStack, FirstRunProject, extract_quick_start_commands


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
