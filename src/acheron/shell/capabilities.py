"""Aggregates language-pair capabilities from registered workers."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from acheron.core.models import WorkerType

if TYPE_CHECKING:
    from acheron.shell.registry import RegisteredWorker
    from acheron.shell.stores.base import WorkerStore


@dataclass(frozen=True)
class LanguagePair:
    """A supported source→target language pair with supporting workers."""

    src: str
    dst: str
    workers: tuple[str, ...]


def _collect_worker_caps(
    workers: tuple[RegisteredWorker, ...],
) -> tuple[set[str], set[tuple[str, str]]]:
    """Extract TTS output languages and translation pairs from registered workers."""
    tts_langs: set[str] = set()
    translation_pairs: set[tuple[str, str]] = set()
    for w in workers:
        match w.capabilities.worker_type:
            case WorkerType.TTS:
                tts_langs.update(w.capabilities.supported_languages_out)
            case WorkerType.TRANSLATION:
                for lang_in in w.capabilities.supported_languages_in:
                    for lang_out in w.capabilities.supported_languages_out:
                        translation_pairs.add((lang_in, lang_out))
    return tts_langs, translation_pairs


def _pair_is_achievable(
    lang_in: str,
    lang_out: str,
    src_filter: str | None,
    dst_filter: str | None,
    requirements: tuple[set[str], set[tuple[str, str]]],
) -> bool:
    """Check if a language pair can be fulfilled by the planner."""
    tts_langs, translation_pairs = requirements
    if src_filter and lang_in != src_filter:
        return False
    if dst_filter and lang_out != dst_filter:
        return False
    if lang_out not in tts_langs:
        return False
    return lang_in == lang_out or (lang_in, lang_out) in translation_pairs


class CapabilityAggregator:
    """Aggregates language pair support from a worker registry."""

    def __init__(self, registry: WorkerStore) -> None:
        self._registry = registry

    async def get_capabilities(
        self,
        src: str | None = None,
        dst: str | None = None,
    ) -> list[LanguagePair]:
        """Aggregate language pairs achievable by the planner.

        Only includes pairs where all required worker types are registered:
        TTS for the target language, and a TRANSLATION worker when src != dst.
        """
        workers = await self._registry.list_all()
        requirements = _collect_worker_caps(workers)
        pairs: dict[tuple[str, str], list[str]] = {}

        for w in workers:
            for lang_in in w.capabilities.supported_languages_in:
                for lang_out in w.capabilities.supported_languages_out:
                    if not _pair_is_achievable(lang_in, lang_out, src, dst, requirements):
                        continue
                    key = (lang_in, lang_out)
                    if key not in pairs:
                        pairs[key] = []
                    pairs[key].append(w.worker_id)

        return [LanguagePair(src=k[0], dst=k[1], workers=tuple(v)) for k, v in pairs.items()]
