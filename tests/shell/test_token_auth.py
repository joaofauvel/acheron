from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]


def _readme() -> str:
    return (REPO_ROOT / "README.md").read_text(encoding="utf-8")


def test_token_source_matrix_is_explicit_and_distinct() -> None:
    readme = _readme().casefold()
    expected = (
        "acheron_registration_token",
        "static, externally managed",
        "file-backed auto-mint",
        ".registration_token",
        "acheron_worker__registration_token_file",
        "reload-aware",
    )
    for phrase in expected:
        assert phrase in readme, f"token source contract omits {phrase!r}"


def test_token_status_contract_names_source_without_plaintext() -> None:
    readme = _readme().casefold()
    assert "source=environment" in readme
    assert "source=file" in readme
    assert "fingerprint" in readme
    assert "status output never contains the token" in readme
    assert "0123456789abcdef" not in readme


def test_environment_rotation_contract_has_external_remediation() -> None:
    readme = _readme().casefold()
    assert "acheron token rotate --reason" in readme
    assert "exits nonzero" in readme
    assert "update/restart workers externally" in readme
