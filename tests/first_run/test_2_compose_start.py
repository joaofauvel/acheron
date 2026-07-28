import json
import subprocess
from typing import cast

from tests.first_run.helpers import ComposeStack, FirstRunProject


def _compose_config(project: FirstRunProject, *profiles: str, **overrides: str) -> dict[str, dict[str, object]]:
    command = ["docker", "compose"]
    for profile in profiles:
        command.extend(("--profile", profile))
    command.extend(("config", "--format", "json"))
    environment = project.env | overrides
    result = subprocess.run(command, cwd=project.checkout, env=environment, check=True, capture_output=True, text=True)
    config = json.loads(result.stdout)
    return cast("dict[str, dict[str, object]]", config["services"])


def test_step_2_compose_start(compose_stack: ComposeStack) -> None:
    orchestrator = compose_stack.get_json("https://localhost:8000/health")
    assert orchestrator == {"status": "ok"}, "step 2: orchestrator did not become healthy"
    dashboard = compose_stack.get_text("http://localhost:8080/")
    assert "Acheron" in dashboard, "step 2: dashboard did not render its index page"


def test_step_2_runpod_profile_contract(prepared_project: FirstRunProject) -> None:
    asr_services = _compose_config(
        prepared_project,
        "runpod-asr",
        COMPOSE_PROFILES="",
        ACHERON_WORKER__WORKER_ID="asr-edge-2",
        ACHERON_WORKER__WORKER_HOST="custom-asr-host",
    )
    assert "granite-speech-edge" in asr_services
    assert "tts-runpod-stub" not in asr_services
    assert "translation-runpod-stub" not in asr_services
    granite_environment = cast("dict[str, str]", asr_services["granite-speech-edge"]["environment"])
    assert granite_environment["ACHERON_WORKER__WORKER_ID"] == "asr-edge-2"
    assert granite_environment["ACHERON_WORKER__WORKER_HOST"] == "custom-asr-host"

    sim_services = _compose_config(prepared_project, "sim", COMPOSE_PROFILES="sim")
    assert "tts-runpod-stub" in sim_services
    assert "translation-runpod-stub" in sim_services
