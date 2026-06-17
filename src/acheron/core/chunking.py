"""Text chunking engine for splitting chapters into TTS-sized segments."""

import re

import nltk

from acheron.core.errors import ChunkingError
from acheron.core.models import Chunk


def chunk_text(text: str, chapter_id: str, max_length: int = 250) -> tuple[Chunk, ...]:
    """Split text into chunks suitable for TTS synthesis.

    Uses NLTK sentence tokenization with fallback splitting on punctuation
    and hard splits for sentences exceeding max_length.

    Raises:
        ChunkingError: If max_length is invalid or chunking produces invalid output.
    """
    if max_length < 1:
        msg = f"max_length must be >= 1, got {max_length}"
        raise ChunkingError(msg)

    text = " ".join(text.split())
    if not text:
        return ()

    try:
        sentences = nltk.sent_tokenize(text)
    except Exception as exc:
        msg = f"NLTK sentence tokenization failed: {exc}"
        raise ChunkingError(msg) from exc

    raw_chunks: list[str] = []

    for sentence in sentences:
        if len(sentence) <= max_length:
            raw_chunks.append(sentence)
        else:
            raw_chunks.extend(_split_long(sentence, max_length))

    chunks = tuple(
        Chunk(chapter_id=chapter_id, sequence_id=i, text=chunk) for i, chunk in enumerate(raw_chunks) if chunk.strip()
    )

    _validate_chunks(chunks, text)
    return chunks


def _validate_chunks(chunks: tuple[Chunk, ...], original: str) -> None:
    """Verify chunk output integrity.

    Raises:
        ChunkingError: If chunks have gaps in sequence IDs, empty text,
            or fail to cover the original text.
    """
    for i, chunk in enumerate(chunks):
        if chunk.sequence_id != i:
            msg = f"Sequence gap: expected {i}, got {chunk.sequence_id}"
            raise ChunkingError(msg)
        if not chunk.text.strip():
            msg = f"Empty chunk at sequence {i}"
            raise ChunkingError(msg)

    rejoined = " ".join(c.text for c in chunks)
    if _normalize_words(original) != _normalize_words(rejoined):
        raise ChunkingError("Content mismatch: chunk output does not cover original text")


def _normalize_words(text: str) -> str:
    """Normalize text for content comparison by removing whitespace and punctuation."""
    return re.sub(r"[\s\W]+", "", text)


def _split_long(text: str, max_length: int) -> list[str]:
    """Split a long sentence using punctuation fallback then hard split."""
    parts = _split_on_punctuation(text, max_length)
    result: list[str] = []
    for part in parts:
        if len(part) <= max_length:
            result.append(part)
        else:
            result.extend(_hard_split(part, max_length))
    return result


def _split_on_punctuation(text: str, max_length: int) -> list[str]:
    """Try splitting on comma, semicolon, or dash separators."""
    for sep in [", ", "; ", " — ", " – ", " - "]:  # noqa: RUF001
        if sep in text:
            parts = text.split(sep)
            merged = _merge_parts(parts, sep, max_length)
            if all(len(p) <= max_length for p in merged):
                return merged
    return [text]


def _merge_parts(parts: list[str], sep: str, max_length: int) -> list[str]:
    """Recombine split parts up to max_length boundaries."""
    merged: list[str] = []
    current = ""

    for part in parts:
        candidate = f"{current}{sep}{part}" if current else part
        if len(candidate) <= max_length:
            current = candidate
        else:
            if current:
                merged.append(current)
            current = part

    if current:
        merged.append(current)
    return [p for p in merged if p.strip()]


def _hard_split(text: str, max_length: int) -> list[str]:
    """Split text at whitespace boundaries, falling back to character split."""
    parts: list[str] = []
    remaining = text

    while len(remaining) > max_length:
        split_at = remaining.rfind(" ", 0, max_length)
        if split_at == -1:
            split_at = max_length
        parts.append(remaining[:split_at])
        remaining = remaining[split_at:].lstrip()

    if remaining:
        parts.append(remaining)
    return parts
