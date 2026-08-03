import re
import time
from typing import cast

from tests.first_run.helpers import ComposeStack, read_file_backed_token


def _healthy_worker_ids(compose_stack: ComposeStack, token: str) -> set[str]:
    payload = cast(
        "dict[str, object]",
        compose_stack.get_json("https://localhost:8000/workers", headers={"Authorization": f"Bearer {token}"}),
    )
    workers = payload.get("workers")
    assert isinstance(workers, list)
    return {
        worker["worker_id"]
        for worker in workers
        if isinstance(worker, dict) and worker.get("status") == "healthy" and isinstance(worker.get("worker_id"), str)
    }


def test_step_3_first_run_auto_mints_and_registers_all_workers(file_backed_compose_stack: ComposeStack) -> None:
    token = read_file_backed_token(file_backed_compose_stack.project)
    expected = {
        "tts-local-stub",
        "asr-local-stub",
        "translation-local-stub",
        "tts-runpod-stub",
        "translation-runpod-stub",
        "tts-grpc-stub",
    }
    deadline = time.monotonic() + 60
    healthy_ids: set[str] = set()
    while time.monotonic() < deadline:
        healthy_ids = _healthy_worker_ids(file_backed_compose_stack, token)
        if expected <= healthy_ids:
            break
        time.sleep(2)
    assert expected <= healthy_ids
    assert "ACHERON_REGISTRATION_TOKEN is unset" not in file_backed_compose_stack.log_text()


def test_step_3_first_run_success_criteria(file_backed_compose_stack: ComposeStack) -> None:
    token = read_file_backed_token(file_backed_compose_stack.project)
    auth = {"Authorization": f"Bearer {token}"}
    status_body = file_backed_compose_stack.get_text("http://localhost:8080/partials/status")
    assert "dot-red" not in status_body, "step 3: dashboard cannot reach the orchestrator"
    assert "Disconnected" not in status_body, "step 3: dashboard cannot reach the orchestrator"
    assert (
        status_body == '<span class="dot dot-yellow"></span> Waiting for workers (0/0 service workers healthy)'
        or re.fullmatch(
            r'<span class="dot dot-(?:yellow"></span> Waiting|green"></span> Ready) '
            r"\([a-z]+ \d+/\d+(?:, [a-z]+ \d+/\d+)*\)",
            status_body,
        )
    ), f"step 3: dashboard returned an invalid readiness fragment: {status_body!r}"

    worker_payload = file_backed_compose_stack.get_json("https://localhost:8000/workers", headers=auth)
    assert isinstance(worker_payload, dict), "step 3: worker listing was not a JSON object"
    workers = worker_payload.get("workers")
    assert isinstance(workers, list), "step 3: worker listing did not contain a workers array"
    assert any(isinstance(worker, dict) and worker.get("status") == "healthy" for worker in workers), (
        "step 3: no healthy worker registered with the orchestrator"
    )

    probe = {
        "worker_id": "first-run-token-probe",
        "endpoint": "http://first-run-token-probe:8001",
        "transport": "http",
        "capabilities": {
            "worker_type": "tts",
            "supported_languages_in": ["en"],
            "supported_languages_out": ["es"],
        },
    }
    rejected = file_backed_compose_stack.request(
        "https://localhost:8000/workers",
        method="POST",
        body=probe,
        headers={"Authorization": "Bearer invalid"},
    )
    assert rejected.status == 401, "step 3: invalid registration token was accepted"
    accepted = file_backed_compose_stack.request(
        "https://localhost:8000/workers", method="POST", body=probe, headers=auth
    )
    assert accepted.status == 201, f"step 3: generated registration token was rejected: {accepted.body.decode()}"

    log = file_backed_compose_stack.log_text()
    assert "ACHERON_REGISTRATION_TOKEN is unset" not in log, "step 3: startup reported an unset registration token"
    assert "ACHERON_OPEN_REGISTRATION=1" not in log, "step 3: startup reported open registration"
