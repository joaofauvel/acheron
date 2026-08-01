"""Single canonical serialiser for :class:`WorkerCapabilities`."""

from __future__ import annotations

import re
from typing import TYPE_CHECKING, cast

from acheron.core.errors import sanitise_public_message

if TYPE_CHECKING:
    from acheron.core.models import JsonValue, WorkerCapabilities


def caps_to_dict(caps: WorkerCapabilities) -> dict[str, JsonValue]:
    """Serialise ``caps`` to the wire format shared by the edge and registration paths."""
    return {
        "worker_type": caps.worker_type.value,
        "supported_languages_in": cast("list[JsonValue]", sorted(caps.supported_languages_in)),
        "supported_languages_out": cast("list[JsonValue]", sorted(caps.supported_languages_out)),
        "supported_formats_in": cast("list[JsonValue]", sorted(caps.supported_formats_in)),
        "supported_formats_out": cast("list[JsonValue]", sorted(caps.supported_formats_out)),
        "max_payload_bytes": caps.max_payload_bytes,
        "batch_capable": caps.batch_capable,
        "model_source": caps.model_source,
        "max_input_tokens": caps.max_input_tokens,
        "metadata": dict(caps.metadata),
    }


_PUBLIC_METADATA_KEYS = frozenset({"default_speaker", "health_endpoint_id", "health_provider", "speakers", "voice"})
_SAFE_SPEAKER_RE = re.compile(r"^[\w .'-]{1,64}$", re.UNICODE)
_SAFE_ENDPOINT_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
_SAFE_LANGUAGE_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.+-]{0,31}$")
_SAFE_FORMAT_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9.+_-]{0,31}(?:/[A-Za-z0-9][A-Za-z0-9.+_-]{0,31})?$")
_INVALID_PUBLIC_VALUE = "__invalid_public_value__"


def _safe_text(value: object, *, pattern: re.Pattern[str]) -> str | None:
    if not isinstance(value, str) or not pattern.fullmatch(value):
        return None
    safe = sanitise_public_message(value, fallback=_INVALID_PUBLIC_VALUE)
    return None if safe == _INVALID_PUBLIC_VALUE else safe


def _safe_public_metadata(metadata: dict[str, JsonValue]) -> dict[str, JsonValue]:  # noqa: C901, PLR0912
    safe: dict[str, JsonValue] = {}
    for key, value in metadata.items():
        if key not in _PUBLIC_METADATA_KEYS:
            continue
        if key == "health_provider":
            if value == "runpod":
                safe[key] = value
        elif key == "health_endpoint_id":
            endpoint_id = _safe_text(value, pattern=_SAFE_ENDPOINT_ID_RE)
            if endpoint_id is not None:
                safe[key] = endpoint_id
        elif key == "speakers":
            if not isinstance(value, list):
                continue
            speakers: list[JsonValue] = []
            for item in value:
                speaker = _safe_text(item, pattern=_SAFE_SPEAKER_RE)
                if speaker is None:
                    break
                speakers.append(speaker)
            else:
                safe[key] = speakers
        else:
            speaker = _safe_text(value, pattern=_SAFE_SPEAKER_RE)
            if speaker is not None:
                safe[key] = speaker
    return safe


def public_caps_to_dict(caps: WorkerCapabilities) -> dict[str, JsonValue]:
    """Serialise capabilities with only validated metadata required by public clients."""
    result = caps_to_dict(caps)
    model_source = _safe_text(caps.model_source, pattern=re.compile(r"^[\w./:@-]{1,256}$"))
    result["model_source"] = model_source
    result["supported_languages_in"] = [
        value
        for value in sorted(caps.supported_languages_in)
        if _safe_text(value, pattern=_SAFE_LANGUAGE_RE) is not None
    ]
    result["supported_languages_out"] = [
        value
        for value in sorted(caps.supported_languages_out)
        if _safe_text(value, pattern=_SAFE_LANGUAGE_RE) is not None
    ]
    result["supported_formats_in"] = [
        value for value in sorted(caps.supported_formats_in) if _safe_text(value, pattern=_SAFE_FORMAT_RE) is not None
    ]
    result["supported_formats_out"] = [
        value for value in sorted(caps.supported_formats_out) if _safe_text(value, pattern=_SAFE_FORMAT_RE) is not None
    ]
    result["metadata"] = _safe_public_metadata(caps.metadata)
    return result
