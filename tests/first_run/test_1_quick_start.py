from tests.first_run.helpers import EXPECTED_QUICK_START_COMMANDS, extract_quick_start_commands


def test_step_1_quick_start_commands_and_environment(prepared_project) -> None:
    readme = (prepared_project.checkout / "README.md").read_text()
    assert extract_quick_start_commands(readme) == EXPECTED_QUICK_START_COMMANDS, (
        "step 1: README Quick Start commands changed; update the journey only with an intentional design change"
    )
    assert (prepared_project.checkout / ".env").exists(), "step 1: README environment copy did not create .env"
    assert len(prepared_project.token) == 64, "step 1: generated registration token is not 32 bytes of hex"
    assert prepared_project.env["ACHERON_REGISTRATION_TOKEN"] == prepared_project.token
