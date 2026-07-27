"""Pure helpers for the README first-run journey."""


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
