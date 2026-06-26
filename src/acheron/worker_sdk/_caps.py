"""Single canonical serialiser for :class:`WorkerCapabilities`."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from acheron.core.models import WorkerCapabilities


def caps_to_dict(caps: WorkerCapabilities) -> dict[str, Any]:
    """Serialise ``caps`` to the wire format shared by the edge and registration paths."""
    return {
        "worker_type": caps.worker_type.value,
        "supported_languages_in": sorted(caps.supported_languages_in),
        "supported_languages_out": sorted(caps.supported_languages_out),
        "supported_formats_in": sorted(caps.supported_formats_in),
        "supported_formats_out": sorted(caps.supported_formats_out),
        "max_payload_bytes": caps.max_payload_bytes,
        "batch_capable": caps.batch_capable,
        "model_source": caps.model_source,
        "max_input_tokens": caps.max_input_tokens,
        "metadata": dict(caps.metadata),
    }
