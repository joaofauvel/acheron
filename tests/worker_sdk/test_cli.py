"""Tests for the acheron-worker-edge image entrypoint."""

import sys
from types import ModuleType

import pytest

from acheron.worker_sdk.cli import _import_handler


class _StubHandler:  # exposed via a pseudo-module path for the test
    pass


def test_import_handler_loads_class_from_dotted_path(monkeypatch: pytest.MonkeyPatch) -> None:
    fake_mod = ModuleType("fake_worker_pkg.fake_mod")
    fake_mod.MyClass = _StubHandler  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "fake_worker_pkg.fake_mod", fake_mod)

    klass = _import_handler("fake_worker_pkg.fake_mod:MyClass")
    assert klass is _StubHandler


def test_import_handler_raises_on_missing_colon() -> None:
    with pytest.raises(ValueError, match="must be 'module:Class'"):
        _import_handler("somemodule")
