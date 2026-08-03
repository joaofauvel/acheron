from tests.first_run.helpers import (
    EXPECTED_QUICK_START_COMMANDS,
    FirstRunProject,
    extract_quick_start_commands,
    file_backed_environment,
)


def test_step_1_quick_start_commands_and_environment(prepared_project: FirstRunProject) -> None:
    readme = (prepared_project.checkout / "README.md").read_text()
    assert extract_quick_start_commands(readme) == EXPECTED_QUICK_START_COMMANDS, (
        "step 1: README Quick Start commands changed; update the journey only with an intentional design change"
    )
    assert (prepared_project.checkout / ".env").exists(), "step 1: README environment copy did not create .env"
    assert "export ACHERON_REGISTRATION_TOKEN" not in readme, (
        "step 1: Quick Start must persist the Compose token instead of requiring a shell-only export"
    )
    assert len(prepared_project.token) == 64, "step 1: generated registration token is not 32 bytes of hex"
    assert prepared_project.env["ACHERON_REGISTRATION_TOKEN"] == prepared_project.token


def test_step_1_deployment_documentation_contract(prepared_project: FirstRunProject) -> None:
    checkout = prepared_project.checkout
    env_example = (checkout / ".env.example").read_text()
    readme = (checkout / "README.md").read_text()

    for variable in (
        "COMPOSE_PROFILES=sim",
        "ACHERON_ADMIN_TOKEN=",
        "GRANITE_SPEECH_RUNPOD_ENDPOINT_ID",
        "TRANSLATEGEMMA_RUNPOD_ENDPOINT_ID",
        "ACHERON_WORKER__WORKER_ID",
        "ACHERON_WORKER__WORKER_HOST",
    ):
        assert variable in env_example, f"step 1: .env.example omits {variable}"

    assert "ACHERON_ADMIN_TOKEN" in readme, "step 1: README omits the separate admin token"
    assert "named `acheron-data` volume" in readme, "step 1: README omits the Compose token volume"
    assert "/data/jobs/.registration_token" in readme, "step 1: README omits the persisted token path"
    assert "ACHERON_WORKER__REGISTRATION_TOKEN_FILE" in readme, (
        "step 1: README omits the reload-aware worker token-file source"
    )
    assert "acheron token status" in readme, "step 1: README omits token status guidance"
    assert "update/restart workers externally" in readme, (
        "step 1: README omits static environment-token rotation remediation"
    )
    compose = (checkout / "docker-compose.yml").read_text()
    assert "ACHERON_ADMIN_TOKEN: ${ACHERON_ADMIN_TOKEN:-}" in compose
    assert "ghcr.io/<owner>/<repo>/" in readme, "step 1: README uses an ambiguous GHCR image placeholder"
    for worker_readme in (
        "workers/qwen3tts/README.md",
        "workers/granite_speech/README.md",
        "workers/translategemma/README.md",
    ):
        text = (checkout / worker_readme).read_text()
        assert "runpodctl serverless create" in text, f"step 1: {worker_readme} omits endpoint creation"
        assert "ghcr.io/<owner>/<repo>/" in text, f"step 1: {worker_readme} uses an incomplete GHCR path"
        assert "ACHERON_REGISTRATION_TOKEN" in text, f"DEPLOY-015: step 1: {worker_readme} omits Compose token mapping"
        assert "ACHERON_WORKER__REGISTRATION_TOKEN" in text, f"step 1: {worker_readme} omits SDK token mapping"


def test_step_1_file_backed_mode_survives_shell_restart(prepared_project: FirstRunProject) -> None:
    first_shell = file_backed_environment(prepared_project.env)
    second_shell = file_backed_environment(prepared_project.env)
    assert "ACHERON_REGISTRATION_TOKEN" not in first_shell
    assert "ACHERON_REGISTRATION_TOKEN" not in second_shell
    assert first_shell == second_shell
    readme = (prepared_project.checkout / "README.md").read_text()
    assert "reuses that token" in readme, "step 1: README omits cross-shell token reuse guidance"
