"""Tests for the safe atomic input store."""

from __future__ import annotations

from collections.abc import AsyncIterator
from pathlib import Path

import pytest

from acheron.shell.input_store import (
    InputPathError,
    InputStore,
    InputTooLargeError,
    StoredInput,
)


class TestSave:
    @pytest.mark.asyncio
    async def test_save_writes_generated_relative_path_and_metadata(self, tmp_path: Path) -> None:
        store = InputStore(tmp_path)

        async def chunks() -> AsyncIterator[bytes]:
            yield b"first"
            yield b" second"

        result = await store.save("nested/book.epub", "application/epub+zip", chunks())

        assert isinstance(result, StoredInput)
        assert result.filename == "book.epub"
        assert result.source_path.startswith("inputs/")
        assert result.source_path.endswith("/book.epub")
        assert result.size_bytes == 12
        assert result.content_type == "application/epub+zip"
        assert (tmp_path / result.source_path).read_bytes() == b"first second"

    @pytest.mark.asyncio
    async def test_save_strips_client_directories_from_filename(self, tmp_path: Path) -> None:
        store = InputStore(tmp_path)

        async def chunks() -> AsyncIterator[bytes]:
            yield b"data"

        result = await store.save("../escape/passwd", "text/plain", chunks())
        assert result.filename == "passwd"
        assert (tmp_path / result.source_path).read_bytes() == b"data"

    @pytest.mark.asyncio
    async def test_save_uses_input_for_empty_basename(self, tmp_path: Path) -> None:
        store = InputStore(tmp_path)

        async def chunks() -> AsyncIterator[bytes]:
            yield b"data"

        result = await store.save("/", "text/plain", chunks())
        assert result.filename == "input"
        assert (tmp_path / result.source_path).read_bytes() == b"data"

    @pytest.mark.asyncio
    async def test_save_creates_distinct_subdirs_per_call(self, tmp_path: Path) -> None:
        store = InputStore(tmp_path)

        async def chunks() -> AsyncIterator[bytes]:
            yield b"data"

        first = await store.save("a.bin", None, chunks())
        second = await store.save("a.bin", None, chunks())
        assert first.source_path != second.source_path
        assert (tmp_path / first.source_path).read_bytes() == b"data"
        assert (tmp_path / second.source_path).read_bytes() == b"data"

    @pytest.mark.asyncio
    async def test_save_returns_posix_relative_source_path(self, tmp_path: Path) -> None:
        store = InputStore(tmp_path)

        async def chunks() -> AsyncIterator[bytes]:
            yield b"x"

        result = await store.save("book.epub", None, chunks())
        assert "\\" not in result.source_path
        assert result.source_path.startswith("inputs/")

    @pytest.mark.asyncio
    async def test_save_rejects_oversize_stream_and_leaves_no_input_file(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr("acheron.shell.input_store.MAX_INPUT_BYTES", 4)
        store = InputStore(tmp_path)

        async def chunks() -> AsyncIterator[bytes]:
            yield b"hello"

        with pytest.raises(InputTooLargeError):
            await store.save("book.epub", "text/plain", chunks())

        inputs_dir = tmp_path / "inputs"
        if inputs_dir.exists():
            files_under_inputs = [p for p in inputs_dir.rglob("*") if p.is_file()]
            assert files_under_inputs == []

    @pytest.mark.asyncio
    async def test_save_cleans_temp_file_when_chunk_iterator_raises(self, tmp_path: Path) -> None:
        store = InputStore(tmp_path)

        async def chunks() -> AsyncIterator[bytes]:
            yield b"first"
            raise RuntimeError("boom")

        with pytest.raises(RuntimeError, match="boom"):
            await store.save("book.epub", "text/plain", chunks())

        tmp_dir = tmp_path / ".inputs-tmp"
        if tmp_dir.exists():
            for entry in tmp_dir.rglob("*"):
                assert not entry.is_file()

    @pytest.mark.asyncio
    async def test_save_accepts_exact_max_bytes(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr("acheron.shell.input_store.MAX_INPUT_BYTES", 5)
        store = InputStore(tmp_path)

        async def chunks() -> AsyncIterator[bytes]:
            yield b"hello"

        result = await store.save("ok.bin", None, chunks())
        assert result.size_bytes == 5
        assert (tmp_path / result.source_path).read_bytes() == b"hello"

    @pytest.mark.asyncio
    async def test_save_strips_windows_client_directories_from_filename(self, tmp_path: Path) -> None:
        store = InputStore(tmp_path)

        async def chunks() -> AsyncIterator[bytes]:
            yield b"data"

        result = await store.save("C:\\fakepath\\book.epub", "application/epub+zip", chunks())
        assert result.filename == "book.epub"
        assert (tmp_path / result.source_path).read_bytes() == b"data"

    @pytest.mark.asyncio
    async def test_save_rejects_storage_root_symlink_escape_inputs(self, tmp_path: Path) -> None:
        """A pre-existing ``data_dir/inputs -> ../outside`` symlink must not redirect writes outside ``data_dir``."""
        data_dir = tmp_path / "data"
        data_dir.mkdir()
        outside = tmp_path / "outside"
        outside.mkdir()

        inputs_link = data_dir / "inputs"
        inputs_link.symlink_to(outside)

        store = InputStore(data_dir)

        async def chunks() -> AsyncIterator[bytes]:
            yield b"leaked"

        with pytest.raises(InputPathError):
            await store.save("book.epub", "text/plain", chunks())

        assert list(outside.iterdir()) == []
        assert inputs_link.is_symlink()

    @pytest.mark.asyncio
    async def test_save_rejects_storage_root_symlink_escape_temp(self, tmp_path: Path) -> None:
        """A pre-existing ``data_dir/.inputs-tmp -> ../outside`` symlink must not redirect temp writes."""
        data_dir = tmp_path / "data"
        data_dir.mkdir()
        outside = tmp_path / "outside"
        outside.mkdir()

        temp_link = data_dir / ".inputs-tmp"
        temp_link.symlink_to(outside)

        store = InputStore(data_dir)

        async def chunks() -> AsyncIterator[bytes]:
            yield b"leaked"

        with pytest.raises(InputPathError):
            await store.save("book.epub", "text/plain", chunks())

        assert list(outside.iterdir()) == []
        assert temp_link.is_symlink()

    @pytest.mark.asyncio
    async def test_save_rejects_internal_storage_root_symlink(self, tmp_path: Path) -> None:
        """An internal ``data_dir/inputs -> data_dir/elsewhere`` symlink must be rejected.

        Allowing such a symlink would silently redirect writes to
        ``data_dir/elsewhere/<id>/<name>`` and return a source path outside
        the public ``inputs/<id>/<name>`` layout promised to API callers.
        """
        data_dir = tmp_path / "data"
        data_dir.mkdir()
        elsewhere = data_dir / "elsewhere"
        elsewhere.mkdir()

        inputs_link = data_dir / "inputs"
        inputs_link.symlink_to(elsewhere)

        store = InputStore(data_dir)

        async def chunks() -> AsyncIterator[bytes]:
            yield b"data"

        with pytest.raises(InputPathError):
            await store.save("book.epub", "text/plain", chunks())

        assert list(elsewhere.iterdir()) == []
        assert inputs_link.is_symlink()

    @pytest.mark.asyncio
    async def test_save_cleans_temp_file_when_replace_fails(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        store = InputStore(tmp_path)

        async def chunks() -> AsyncIterator[bytes]:
            yield b"data"

        def fail_replace(src: Path, dst: Path) -> None:
            raise OSError("replace failed")

        monkeypatch.setattr("acheron.shell.input_store.os.replace", fail_replace)

        with pytest.raises(OSError, match="replace failed"):
            await store.save("book.epub", "text/plain", chunks())

        tmp_dir = tmp_path / ".inputs-tmp"
        if tmp_dir.exists():
            files_under_tmp = [p for p in tmp_dir.rglob("*") if p.is_file()]
            assert files_under_tmp == []

        inputs_dir = tmp_path / "inputs"
        if inputs_dir.exists():
            files_under_inputs = [p for p in inputs_dir.rglob("*") if p.is_file()]
            assert files_under_inputs == []


class TestResolveSourcePath:
    @pytest.mark.asyncio
    async def test_resolve_source_path_returns_regular_file(self, tmp_path: Path) -> None:
        store = InputStore(tmp_path)

        async def chunks() -> AsyncIterator[bytes]:
            yield b"hello"

        result = await store.save("a/b.txt", "text/plain", chunks())
        resolved = store.resolve_source_path(result.source_path)
        assert resolved == (tmp_path / result.source_path).resolve()
        assert resolved.is_file()
        assert resolved.read_bytes() == b"hello"

    def test_resolve_source_path_rejects_absolute(self, tmp_path: Path) -> None:
        store = InputStore(tmp_path)
        with pytest.raises(InputPathError):
            store.resolve_source_path("/etc/passwd")

    def test_resolve_source_path_rejects_empty(self, tmp_path: Path) -> None:
        store = InputStore(tmp_path)
        with pytest.raises(InputPathError):
            store.resolve_source_path("")

    def test_resolve_source_path_rejects_parent_escape(self, tmp_path: Path) -> None:
        store = InputStore(tmp_path)
        with pytest.raises(InputPathError):
            store.resolve_source_path("../outside.epub")

    def test_resolve_source_path_rejects_missing_file(self, tmp_path: Path) -> None:
        store = InputStore(tmp_path)
        with pytest.raises(InputPathError):
            store.resolve_source_path("inputs/missing.epub")

    def test_resolve_source_path_rejects_directory(self, tmp_path: Path) -> None:
        store = InputStore(tmp_path)
        (tmp_path / "inputs" / "subdir").mkdir(parents=True)
        with pytest.raises(InputPathError):
            store.resolve_source_path("inputs/subdir")

    def test_resolve_source_path_rejects_symlink_escape(self, tmp_path: Path) -> None:
        store = InputStore(tmp_path)
        outside = tmp_path.parent / f"outside-{tmp_path.name}.txt"
        outside.write_text("data")
        try:
            inputs_subdir = tmp_path / "inputs" / "abc"
            inputs_subdir.mkdir(parents=True)
            link = inputs_subdir / "link.txt"
            link.symlink_to(outside)
            with pytest.raises(InputPathError):
                store.resolve_source_path("inputs/abc/link.txt")
        finally:
            outside.unlink(missing_ok=True)

    def test_resolve_source_path_error_includes_data_dir(self, tmp_path: Path) -> None:
        store = InputStore(tmp_path)
        with pytest.raises(InputPathError) as exc_info:
            store.resolve_source_path("/etc/passwd")
        assert str(tmp_path.resolve()) in str(exc_info.value)

    def test_resolve_source_path_error_includes_requested_path(self, tmp_path: Path) -> None:
        store = InputStore(tmp_path)
        with pytest.raises(InputPathError) as exc_info:
            store.resolve_source_path("../escape.epub")
        assert "../escape.epub" in str(exc_info.value)
