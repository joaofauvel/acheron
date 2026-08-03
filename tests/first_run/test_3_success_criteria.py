import io
import json
import re
import subprocess
import time
import zipfile
from typing import cast

from tests.first_run.helpers import ComposeStack, read_file_backed_token

_SUPPORTED_EDGES = {
    "tts-local-stub": "http://localhost:8001",
    "asr-local-stub": "http://localhost:8002",
    "translation-local-stub": "http://localhost:8003",
    "tts-runpod-stub": "http://localhost:8006",
    "translation-runpod-stub": "http://localhost:8007",
    "tts-grpc-stub": "http://localhost:9002",
}


def _edge_identities(compose_stack: ComposeStack) -> dict[str, str]:
    identities: dict[str, str] = {}
    for service in _SUPPORTED_EDGES:
        result = subprocess.run(
            ["docker", "compose", "ps", "-q", service],
            cwd=compose_stack.project.checkout,
            env=compose_stack.project.env,
            capture_output=True,
            text=True,
            check=True,
            timeout=30,
        )
        container_id = result.stdout.strip()
        assert container_id, f"step 3: Compose did not report container for {service}"
        identities[service] = container_id
    return identities


def _edge_auth_checks(compose_stack: ComposeStack, token: str) -> dict[str, int]:
    body = {
        "job_id": "token-edge-probe",
        "job_type": "tts",
        "payload": {"chunks": [{"chapter_id": "probe", "sequence_id": 0}]},
        "chapter_id": "probe",
        "sequence_ids": [0],
    }
    checks: dict[str, int] = {}
    for service, endpoint in _SUPPORTED_EDGES.items():
        try:
            response = compose_stack.request(
                f"{endpoint}/execute",
                method="POST",
                body=body,
                headers={"Authorization": f"Bearer {token}"},
            )
        except OSError:
            checks[service] = 0
        else:
            checks[service] = response.status
    return checks


def _minimal_epub() -> bytes:
    files = {
        "META-INF/container.xml": (
            '<?xml version="1.0"?><container version="1.0" '
            'xmlns="urn:oasis:names:tc:opendocument:xmlns:container">'
            '<rootfiles><rootfile full-path="OEBPS/content.opf" '
            'media-type="application/oebps-package+xml"/></rootfiles></container>'
        ),
        "OEBPS/content.opf": (
            '<?xml version="1.0" encoding="UTF-8"?><package version="2.0" '
            'xmlns="http://www.idpf.org/2007/opf"><manifest><item id="ch1" '
            'href="chapter.xhtml" media-type="application/xhtml+xml"/></manifest>'
            '<spine><itemref idref="ch1"/></spine></package>'
        ),
        "OEBPS/chapter.xhtml": "<html><body><p>This is a token rotation dispatch probe.</p></body></html>",
    }
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w", zipfile.ZIP_DEFLATED) as archive:
        for name, content in files.items():
            archive.writestr(name, content)
    return output.getvalue()


def _wait_for_rotation(
    compose_stack: ComposeStack, current_token: str, old_token: str
) -> tuple[dict[str, int], dict[str, int]]:
    deadline = time.monotonic() + 60
    current_checks = _edge_auth_checks(compose_stack, current_token)
    old_checks = _edge_auth_checks(compose_stack, old_token)
    while time.monotonic() < deadline:
        if all(status == 200 for status in current_checks.values()) and all(
            status == 401 for status in old_checks.values()
        ):
            break
        time.sleep(2)
        current_checks = _edge_auth_checks(compose_stack, current_token)
        old_checks = _edge_auth_checks(compose_stack, old_token)
    return current_checks, old_checks


def _dispatch_rotation_probe(compose_stack: ComposeStack, token: str, old_token: str) -> None:
    uploaded = compose_stack.upload_input(
        _minimal_epub(),
        filename="rotation-probe.epub",
        content_type="application/epub+zip",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert uploaded.status == 201, uploaded.body.decode()
    uploaded_payload = cast("dict[str, object]", json.loads(uploaded.body))
    input_id = uploaded_payload.get("input_id")
    source_path = uploaded_payload.get("source_path")
    assert isinstance(input_id, str)
    assert isinstance(source_path, str)
    submitted = compose_stack.request(
        "https://localhost:8000/jobs",
        method="POST",
        body={
            "source_type": "epub",
            "source_path": source_path,
            "source_language": "en",
            "target_language": "es",
            "input_id": input_id,
        },
        headers={"Authorization": f"Bearer {token}"},
        timeout_seconds=30,
    )
    assert submitted.status == 201, submitted.body.decode()
    submitted_payload = cast("dict[str, object]", json.loads(submitted.body))
    job_id = submitted_payload.get("job_id")
    assert isinstance(job_id, str)
    deadline = time.monotonic() + 60
    completed: dict[str, object] = submitted_payload
    while time.monotonic() < deadline:
        job = compose_stack.request(
            f"https://localhost:8000/jobs/{job_id}", headers={"Authorization": f"Bearer {token}"}
        )
        assert job.status == 200, job.body.decode()
        completed = cast("dict[str, object]", json.loads(job.body))
        if completed.get("status") in {"completed", "failed", "partial"}:
            break
        time.sleep(2)
    assert completed.get("status") == "completed", completed.get("errors")
    assert completed.get("outputs"), completed
    output = json.dumps(completed)
    assert old_token not in output
    assert token not in output


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


def test_step_3_file_backed_token_rotation_updates_workers_and_audit(
    file_backed_compose_stack: ComposeStack,
) -> None:
    project = file_backed_compose_stack.project
    admin_token = project.env["ACHERON_ADMIN_TOKEN"]
    admin_headers = {"Authorization": f"Bearer {admin_token}"}
    old_token = read_file_backed_token(project)
    identities_before = _edge_identities(file_backed_compose_stack)

    status = file_backed_compose_stack.request("https://localhost:8000/admin/token/status", headers=admin_headers)
    assert status.status == 200
    status_payload = cast("dict[str, object]", json.loads(status.body))
    assert status_payload["source"] == "file"
    assert old_token not in status.body.decode()

    rotation = file_backed_compose_stack.request(
        "https://localhost:8000/admin/token/rotate",
        method="POST",
        body={"reason": "first-run rotation"},
        headers=admin_headers,
        timeout_seconds=30,
    )
    assert rotation.status == 200, rotation.body.decode()
    rotation_payload = cast("dict[str, object]", json.loads(rotation.body))
    assert rotation_payload["rotated"] is True
    assert old_token not in rotation.body.decode()

    new_token = read_file_backed_token(project)
    assert new_token != old_token
    current_checks, old_checks = _wait_for_rotation(file_backed_compose_stack, new_token, old_token)
    assert current_checks == dict.fromkeys(_SUPPORTED_EDGES, 200)
    assert old_checks == dict.fromkeys(_SUPPORTED_EDGES, 401)
    assert _edge_identities(file_backed_compose_stack) == identities_before

    cli_status = subprocess.run(
        ["docker", "compose", "exec", "-T", "orchestrator", "acheron", "token", "status"],
        cwd=project.checkout,
        env=project.env,
        capture_output=True,
        text=True,
        check=False,
        timeout=30,
    )
    assert cli_status.returncode == 0, cli_status.stderr
    assert "source=file" in cli_status.stdout
    assert new_token not in cli_status.stdout

    cli_rotation = subprocess.run(
        [
            "docker",
            "compose",
            "exec",
            "-T",
            "orchestrator",
            "acheron",
            "token",
            "rotate",
            "--reason",
            "test",
        ],
        cwd=project.checkout,
        env=project.env,
        capture_output=True,
        text=True,
        check=False,
        timeout=60,
    )
    assert cli_rotation.returncode == 0, cli_rotation.stderr
    assert "rollout=success" in cli_rotation.stdout
    assert old_token not in cli_rotation.stdout
    assert new_token not in cli_rotation.stdout

    cli_token = read_file_backed_token(project)
    assert cli_token not in {old_token, new_token}
    current_checks, old_checks = _wait_for_rotation(file_backed_compose_stack, cli_token, new_token)
    assert current_checks == dict.fromkeys(_SUPPORTED_EDGES, 200)
    assert old_checks == dict.fromkeys(_SUPPORTED_EDGES, 401)
    assert _edge_identities(file_backed_compose_stack) == identities_before

    history = file_backed_compose_stack.request("https://localhost:8000/admin/token/status", headers=admin_headers)
    assert history.status == 200
    history_payload = cast("dict[str, object]", json.loads(history.body))
    history_entries = history_payload["history"]
    assert isinstance(history_entries, list)
    reasons = {entry["reason"] for entry in history_entries if isinstance(entry, dict)}
    assert {"first-run rotation", "test"} <= reasons
    history_text = history.body.decode()
    assert old_token not in history_text
    assert new_token not in history_text
    assert cli_token not in history_text

    _dispatch_rotation_probe(file_backed_compose_stack, cli_token, new_token)


def test_step_3_file_backed_token_authenticates_worker_execute(file_backed_compose_stack: ComposeStack) -> None:
    token = read_file_backed_token(file_backed_compose_stack.project)
    body = {
        "job_id": "file-backed-token-probe",
        "job_type": "tts",
        "payload": {"chunks": [{"chapter_id": "probe", "sequence_id": 0}]},
        "chapter_id": "probe",
        "sequence_ids": [0],
    }
    current = file_backed_compose_stack.request(
        "http://localhost:8001/execute",
        method="POST",
        body=body,
        headers={"Authorization": f"Bearer {token}"},
    )
    assert current.status == 200, f"step 3: current token was rejected: {current.body.decode()}"
    wrong = file_backed_compose_stack.request(
        "http://localhost:8001/execute",
        method="POST",
        body=body,
        headers={"Authorization": "Bearer wrong-token"},
    )
    assert wrong.status == 401, "step 3: wrong token was accepted by worker /execute"
    output = current.body + wrong.body + file_backed_compose_stack.log_text().encode()
    assert token.encode() not in output, "step 3: generated token appeared in worker output"


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
