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
