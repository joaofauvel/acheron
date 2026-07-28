from tests.first_run.helpers import EXPECTED_QUICK_START_COMMANDS, FirstRunProject, extract_quick_start_commands


def test_step_1_quick_start_commands_and_environment(prepared_project: FirstRunProject) -> None:
    readme = (prepared_project.checkout / "README.md").read_text()
    assert extract_quick_start_commands(readme) == EXPECTED_QUICK_START_COMMANDS, (
        "step 1: README Quick Start commands changed; update the journey only with an intentional design change"
    )
    assert (prepared_project.checkout / ".env").exists(), "step 1: README environment copy did not create .env"
    assert len(prepared_project.token) == 64, "step 1: generated registration token is not 32 bytes of hex"
    assert prepared_project.env["ACHERON_REGISTRATION_TOKEN"] == prepared_project.token


def test_step_1_deployment_documentation_contract(prepared_project: FirstRunProject) -> None:
    checkout = prepared_project.checkout
    env_example = (checkout / ".env.example").read_text()
    readme = (checkout / "README.md").read_text()

    for variable in (
        "COMPOSE_PROFILES=sim",
        "GRANITE_SPEECH_RUNPOD_ENDPOINT_ID",
        "TRANSLATEGEMMA_RUNPOD_ENDPOINT_ID",
        "ACHERON_WORKER__WORKER_ID",
        "ACHERON_WORKER__WORKER_HOST",
    ):
        assert variable in env_example, f"step 1: .env.example omits {variable}"

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
