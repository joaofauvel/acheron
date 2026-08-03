"""Pytest configuration for the opt-in first-run journey."""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest

from tests.first_run.helpers import (
    ComposeStack,
    FirstRunProject,
    launch_compose,
    prepare_project,
    stop_compose_best_effort,
)


@pytest.fixture(scope="session")
def prepared_project(tmp_path_factory: pytest.TempPathFactory) -> FirstRunProject:
    """Prepare one temporary checkout for the selected first-run steps."""
    repo_root = Path(__file__).parents[2]
    destination = tmp_path_factory.mktemp("first-run")
    return prepare_project(repo_root, destination)


@pytest.fixture(scope="session")
def file_backed_project(tmp_path_factory: pytest.TempPathFactory) -> FirstRunProject:
    """Prepare a Compose project with no explicit registration token."""
    repo_root = Path(__file__).parents[2]
    destination = tmp_path_factory.mktemp("first-run-file-backed")
    return prepare_project(repo_root, destination, file_backed_token=True)


@pytest.fixture(scope="session")
def compose_stack(prepared_project: FirstRunProject) -> Iterator[ComposeStack]:
    """Start one Compose stack for the selected journey steps."""
    stack = launch_compose(prepared_project)
    try:
        try:
            stack.wait_until_ready(timeout_seconds=240)
        except Exception as exc:
            message = f"step 2: Compose startup failed; see {prepared_project.log_path}\n{stack.log_tail()}"
            raise AssertionError(message) from exc
        yield stack
    finally:
        stop_compose_best_effort(stack)


@pytest.fixture(scope="session")
def file_backed_compose_stack(file_backed_project: FirstRunProject) -> Iterator[ComposeStack]:
    """Start one Compose stack without an explicit registration token."""
    stack = launch_compose(file_backed_project)
    try:
        try:
            stack.wait_until_ready(timeout_seconds=240)
        except Exception as exc:
            message = (
                f"step 3: file-backed Compose startup failed; see {file_backed_project.log_path}\n{stack.log_tail()}"
            )
            raise AssertionError(message) from exc
        yield stack
    finally:
        stop_compose_best_effort(stack)


def pytest_addoption(parser: pytest.Parser) -> None:
    parser.addoption("--first-run", action="store_true", help="run the Docker-backed first-run journey")
    parser.addoption("--step", choices=("1", "2", "3"), default=None, help="run one first-run journey step")


def pytest_collection_modifyitems(config: pytest.Config, items: list[pytest.Item]) -> None:
    first_run_items = [item for item in items if "first_run" in item.path.parts]
    if config.getoption("--first-run"):
        step = config.getoption("--step")
        if step is None:
            return
        selected = f"test_step_{step}"
        skip = pytest.mark.skip(reason=f"not selected by --step {step}")
        for item in first_run_items:
            if selected not in item.name:
                item.add_marker(skip)
        return

    skip = pytest.mark.skip(reason="Docker-backed first-run tests require --first-run")
    for item in first_run_items:
        item.add_marker(skip)
