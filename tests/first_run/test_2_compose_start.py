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


def _assert_edge_override(
    services: dict[str, dict[str, object]],
    service_name: str,
    worker_id: str,
    worker_host: str,
) -> None:
    environment = cast("dict[str, str]", services[service_name]["environment"])
    assert environment["ACHERON_WORKER__WORKER_ID"] == worker_id
    assert environment["ACHERON_WORKER__WORKER_HOST"] == worker_host


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
    _assert_edge_override(asr_services, "granite-speech-edge", "asr-edge-2", "custom-asr-host")

    tts_services = _compose_config(
        prepared_project,
        "runpod-tts",
        COMPOSE_PROFILES="",
        ACHERON_WORKER__WORKER_ID="tts-edge-2",
        ACHERON_WORKER__WORKER_HOST="custom-tts-host",
    )
    _assert_edge_override(tts_services, "qwen3tts-edge", "tts-edge-2", "custom-tts-host")

    translation_services = _compose_config(
        prepared_project,
        "runpod-translation",
        COMPOSE_PROFILES="",
        ACHERON_WORKER__WORKER_ID="translation-edge-2",
        ACHERON_WORKER__WORKER_HOST="custom-translation-host",
    )
    _assert_edge_override(
        translation_services,
        "translategemma-edge",
        "translation-edge-2",
        "custom-translation-host",
    )

    sim_services = _compose_config(prepared_project, "sim", COMPOSE_PROFILES="sim")
    assert "tts-runpod-stub" in sim_services
    assert "translation-runpod-stub" in sim_services
