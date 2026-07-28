import re

from tests.first_run.helpers import ComposeStack


def test_step_3_first_run_success_criteria(compose_stack: ComposeStack) -> None:
    auth = {"Authorization": f"Bearer {compose_stack.project.token}"}
    status_body = compose_stack.get_text("http://localhost:8080/partials/status")
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

    worker_payload = compose_stack.get_json("https://localhost:8000/workers", headers=auth)
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
    rejected = compose_stack.request(
        "https://localhost:8000/workers",
        method="POST",
        body=probe,
        headers={"Authorization": "Bearer invalid"},
    )
    assert rejected.status == 401, "step 3: invalid registration token was accepted"
    accepted = compose_stack.request("https://localhost:8000/workers", method="POST", body=probe, headers=auth)
    assert accepted.status == 201, f"step 3: generated registration token was rejected: {accepted.body.decode()}"

    log = compose_stack.log_text()
    assert "ACHERON_REGISTRATION_TOKEN is unset" not in log, "step 3: startup reported an unset registration token"
    assert "ACHERON_OPEN_REGISTRATION=1" not in log, "step 3: startup reported open registration"
