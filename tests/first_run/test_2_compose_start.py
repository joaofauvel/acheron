import json
import subprocess
from pathlib import Path
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


def _run_certs_init(project: FirstRunProject) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["docker", "compose", "run", "--rm", "--no-deps", "certs-init"],
        cwd=project.checkout,
        env=project.env,
        check=False,
        capture_output=True,
        text=True,
        timeout=60,
    )


def _mutate_cert_material(project: FirstRunProject, command: str) -> None:
    result = subprocess.run(
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
            command,
        ],
        cwd=project.checkout,
        env=project.env,
        check=False,
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert result.returncode == 0, result.stderr


def _restore_marked_certificate_bundle(project: FirstRunProject) -> None:
    _mutate_cert_material(project, "rm -f /certs/* /certs/.[!.]* /certs/..?*")
    result = _run_certs_init(project)
    assert result.returncode == 0, result.stderr


def test_step_2_compose_reuses_marked_development_bundle(
    compose_stack: ComposeStack,
    prepared_project: FirstRunProject,
) -> None:
    certs = prepared_project.checkout / "certs"
    ca_file = certs / "acheron-ca.crt"
    marker = certs / ".dev-ca"
    before_bytes = ca_file.read_bytes()
    before_mtime = ca_file.stat().st_mtime_ns
    assert marker.exists(), "step 2: certs-init did not create the development marker"

    result = _run_certs_init(prepared_project)

    assert result.returncode == 0, result.stderr
    assert ca_file.read_bytes() == before_bytes
    assert ca_file.stat().st_mtime_ns == before_mtime


def test_step_2_compose_rejects_unmarked_certificate_material(prepared_project: FirstRunProject) -> None:
    initial = _run_certs_init(prepared_project)
    assert initial.returncode == 0, initial.stderr
    sentinel = b"operator-owned-ca"
    _mutate_cert_material(
        prepared_project,
        "rm -f /certs/.dev-ca && printf operator-owned-ca > /certs/acheron-ca.crt && "
        "printf operator-owned-key > /certs/acheron-ca.key",
    )
    try:
        result = _run_certs_init(prepared_project)

        assert result.returncode != 0
        assert (prepared_project.checkout / "certs" / "acheron-ca.crt").read_bytes() == sentinel
    finally:
        _restore_marked_certificate_bundle(prepared_project)


def test_step_2_just_certs_rejects_shell_expression_without_execution(
    prepared_project: FirstRunProject,
    tmp_path: Path,
) -> None:
    marker = tmp_path / "substitution-executed"
    result = subprocess.run(
        ["just", "certs", f"$(touch {marker})"],
        cwd=prepared_project.checkout,
        env=prepared_project.env,
        check=False,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 2
    assert not marker.exists()
    assert "usage: just certs [--force]" in result.stderr


def _run_orchestrator_startup(project: FirstRunProject) -> subprocess.CompletedProcess[str]:
    try:
        return subprocess.run(
            [
                "docker",
                "compose",
                "up",
                "--build",
                "--abort-on-container-exit",
                "--no-log-prefix",
                "orchestrator",
            ],
            cwd=project.checkout,
            env=project.env,
            check=False,
            capture_output=True,
            text=True,
            timeout=180,
        )
    finally:
        subprocess.run(
            ["docker", "compose", "down", "--volumes", "--remove-orphans"],
            cwd=project.checkout,
            env=project.env,
            check=False,
            capture_output=True,
            text=True,
            timeout=60,
        )


def test_step_2_compose_dependency_gate_blocks_orchestrator_startup(
    prepared_project: FirstRunProject,
) -> None:
    initial = _run_certs_init(prepared_project)
    assert initial.returncode == 0, initial.stderr
    _mutate_cert_material(
        prepared_project,
        "rm -f /certs/.dev-ca && printf operator-owned-ca > /certs/acheron-ca.crt && "
        "printf operator-owned-key > /certs/acheron-ca.key",
    )
    try:
        result = _run_orchestrator_startup(prepared_project)
        output = result.stdout + result.stderr

        assert result.returncode != 0
        assert 'service "certs-init" didn\'t complete successfully' in output
        assert "orchestrator" not in [line for line in output.splitlines() if " Started" in line]
    finally:
        _restore_marked_certificate_bundle(prepared_project)


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
