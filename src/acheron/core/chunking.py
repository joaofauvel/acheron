import nltk

from acheron.core.models import Chunk


def chunk_text(text: str, chapter_id: str, max_length: int = 250) -> tuple[Chunk, ...]:
    """Split text into chunks suitable for TTS synthesis."""
    text = " ".join(text.split())
    if not text:
        return ()

    sentences = nltk.sent_tokenize(text)
    raw_chunks: list[str] = []

    for sentence in sentences:
        if len(sentence) <= max_length:
            raw_chunks.append(sentence)
        else:
            raw_chunks.extend(_split_long(sentence, max_length))

    return tuple(
        Chunk(chapter_id=chapter_id, sequence_id=i, text=chunk) for i, chunk in enumerate(raw_chunks) if chunk.strip()
    )


def _split_long(text: str, max_length: int) -> list[str]:
    parts = _split_on_punctuation(text, max_length)
    result: list[str] = []
    for part in parts:
        if len(part) <= max_length:
            result.append(part)
        else:
            result.extend(_hard_split(part, max_length))
    return result


def _split_on_punctuation(text: str, max_length: int) -> list[str]:
    for sep in [", ", "; ", " — ", " – ", " - "]:  # noqa: RUF001
        if sep in text:
            parts = text.split(sep)
            merged = _merge_parts(parts, sep, max_length)
            if all(len(p) <= max_length for p in merged):
                return merged
    return [text]


def _merge_parts(parts: list[str], sep: str, max_length: int) -> list[str]:
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
    return merged


def _hard_split(text: str, max_length: int) -> list[str]:
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
