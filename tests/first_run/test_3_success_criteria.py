def test_step_3_first_run_success_criteria(compose_stack) -> None:
    auth = {"Authorization": f"Bearer {compose_stack.project.token}"}
    status_body = compose_stack.get_text("http://localhost:8080/partials/status")
    assert "dot-green" in status_body, "step 3: dashboard cannot reach the orchestrator"

    workers = compose_stack.get_json("https://localhost:8000/workers", headers=auth)["workers"]
    assert any(worker["status"] == "healthy" for worker in workers), (
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
    assert accepted.status == 201, (
        f"step 3: generated registration token was rejected: {accepted.body.decode()}"
    )

    log = compose_stack.log_tail()
    assert "ACHERON_REGISTRATION_TOKEN is unset" not in log, "step 3: startup reported an unset registration token"
    assert "ACHERON_OPEN_REGISTRATION=1" not in log, "step 3: startup reported open registration"
