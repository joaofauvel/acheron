"""Tests for the worker registry."""

from acheron.core.models import WorkerCapabilities, WorkerType
from acheron.shell.registry import WorkerRegistry


def _tts_caps(
    langs_in: frozenset[str] = frozenset({"en"}), langs_out: frozenset[str] = frozenset({"es"})
) -> WorkerCapabilities:
    return WorkerCapabilities(
        worker_type=WorkerType.TTS,
        supported_languages_in=langs_in,
        supported_languages_out=langs_out,
        supported_formats_in=frozenset({"text"}),
        supported_formats_out=frozenset({"wav"}),
        max_payload_bytes=None,
        batch_capable=True,
        model_source=None,
    )


def _asr_caps() -> WorkerCapabilities:
    return WorkerCapabilities(
        worker_type=WorkerType.ASR,
        supported_languages_in=frozenset({"en", "es"}),
        supported_languages_out=frozenset({"en", "es"}),
        supported_formats_in=frozenset({"mp3", "wav"}),
        supported_formats_out=frozenset({"text"}),
        max_payload_bytes=None,
        batch_capable=False,
        model_source=None,
    )


class TestWorkerRegistry:
    def test_register_and_get(self) -> None:
        reg = WorkerRegistry()
        reg.register("w-1", "http://localhost:8001", "http", _tts_caps())
        w = reg.get("w-1")
        assert w is not None
        assert w.worker_id == "w-1"
        assert w.endpoint == "http://localhost:8001"
        assert w.transport == "http"

    def test_get_nonexistent(self) -> None:
        reg = WorkerRegistry()
        assert reg.get("nope") is None

    def test_unregister(self) -> None:
        reg = WorkerRegistry()
        reg.register("w-1", "http://localhost:8001", "http", _tts_caps())
        reg.unregister("w-1")
        assert reg.get("w-1") is None

    def test_unregister_nonexistent(self) -> None:
        reg = WorkerRegistry()
        reg.unregister("nope")

    def test_list_all(self) -> None:
        reg = WorkerRegistry()
        reg.register("w-1", "http://a", "http", _tts_caps())
        reg.register("w-2", "http://b", "http", _asr_caps())
        workers = reg.list_all()
        assert len(workers) == 2
        ids = {w.worker_id for w in workers}
        assert ids == {"w-1", "w-2"}

    def test_reregistration_overwrites(self) -> None:
        reg = WorkerRegistry()
        reg.register("w-1", "http://old", "http", _tts_caps())
        reg.register("w-1", "http://new", "http", _tts_caps())
        w = reg.get("w-1")
        assert w is not None
        assert w.endpoint == "http://new"

    def test_find_by_type(self) -> None:
        reg = WorkerRegistry()
        reg.register("tts-1", "http://a", "http", _tts_caps())
        reg.register("asr-1", "http://b", "http", _asr_caps())
        reg.register("tts-2", "http://c", "http", _tts_caps())
        tts_workers = reg.find_by_type(WorkerType.TTS)
        assert len(tts_workers) == 2
        asr_workers = reg.find_by_type(WorkerType.ASR)
        assert len(asr_workers) == 1

    def test_find_by_language(self) -> None:
        reg = WorkerRegistry()
        reg.register("w-1", "http://a", "http", _tts_caps(frozenset({"en"}), frozenset({"es"})))
        reg.register("w-2", "http://b", "http", _tts_caps(frozenset({"en"}), frozenset({"fr"})))
        reg.register("w-3", "http://c", "http", _tts_caps(frozenset({"es"}), frozenset({"en"})))
        en_to_es = reg.find_by_language("en", "es")
        assert len(en_to_es) == 1
        assert en_to_es[0].worker_id == "w-1"

    def test_find_by_language_no_match(self) -> None:
        reg = WorkerRegistry()
        reg.register("w-1", "http://a", "http", _tts_caps(frozenset({"en"}), frozenset({"es"})))
        result = reg.find_by_language("ja", "ko")
        assert len(result) == 0


class TestHealthTracking:
    def test_health_success_resets_counter(self) -> None:
        reg = WorkerRegistry()
        reg.register("w-1", "http://a", "http", _tts_caps())
        reg.record_health_failure("w-1")
        reg.record_health_failure("w-1")
        w = reg.get("w-1")
        assert w is not None
        assert w.consecutive_failures == 2
        reg.record_health_success("w-1")
        w = reg.get("w-1")
        assert w is not None
        assert w.consecutive_failures == 0

    def test_health_failure_increments(self) -> None:
        reg = WorkerRegistry()
        reg.register("w-1", "http://a", "http", _tts_caps())
        removed = reg.record_health_failure("w-1")
        assert not removed
        w = reg.get("w-1")
        assert w is not None
        assert w.consecutive_failures == 1

    def test_removed_after_max_failures(self) -> None:
        reg = WorkerRegistry()
        reg.register("w-1", "http://a", "http", _tts_caps())
        reg.record_health_failure("w-1")
        reg.record_health_failure("w-1")
        removed = reg.record_health_failure("w-1")
        assert removed
        assert reg.get("w-1") is None

    def test_health_failure_nonexistent(self) -> None:
        reg = WorkerRegistry()
        removed = reg.record_health_failure("nope")
        assert not removed

    def test_health_success_nonexistent(self) -> None:
        reg = WorkerRegistry()
        reg.record_health_success("nope")
