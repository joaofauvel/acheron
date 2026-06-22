"""Smoke test proving the worker_sdk subpackage imports cleanly."""


def test_package_importable() -> None:
    import acheron.worker_sdk  # noqa: F401
