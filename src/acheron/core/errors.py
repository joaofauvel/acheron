"""Domain exception hierarchy for Acheron."""

import re


class AcheronError(Exception):
    """Base exception for all Acheron errors."""

    def __init__(self, message: str, *, remediation: str | None = None) -> None:
        super().__init__(message)
        self.remediation = remediation


class PathNotAllowedError(AcheronError):
    """A path submitted for filesystem access resolves outside the configured allowlist."""


class PlanError(AcheronError):
    """Errors related to plan compilation or validation."""


class InvalidLanguagePathError(PlanError):
    """Requested source/target language pair is not supported."""


class VoiceSelectionError(PlanError):
    """Requested voices cannot be served by one registered TTS worker."""


class ChunkingTooLongForWorkerError(PlanError):
    """Chunking step's max_chunk_length exceeds a text-input worker's max_input_tokens.

    Raised at plan compile time so misconfigurations fail fast, before any GPU time.
    """


class WorkerError(AcheronError):
    """Errors related to worker communication or execution."""


class InsecureBearerTransportError(WorkerError):
    """A bearer credential would be sent over plaintext transport."""


class WorkerUnavailableError(WorkerError):
    """Worker is not reachable or has been removed from the registry."""


class CacheError(AcheronError):
    """Errors related to step output caching."""


class CacheMissError(CacheError):
    """Expected cached output does not exist."""


class CacheCorruptedError(CacheError):
    """Cached output failed integrity validation."""


class ChunkingError(AcheronError):
    """Text chunking failed or produced invalid output."""


class PipelineError(AcheronError):
    """Unexpected failures during streaming pipeline execution.

    Reserved for executor-internal invariants (cache, sentinel protocol,
    unexpected stage failures). Worker-dispatch failures continue to be
    represented by ``WorkerError`` subclasses.
    """


class JobError(AcheronError):
    """Errors related to tracked job lifecycle operations."""


class JobNotFoundError(JobError):
    """Requested tracked job does not exist."""


class JobAlreadyRunningError(JobError):
    """Requested tracked job is already active in this orchestrator."""


class NoPlanToResumeError(JobError):
    """Requested tracked job has no saved plan to resume."""


class JobNotResumableError(JobError):
    """Requested tracked job is not an incomplete terminal job."""


class JobNotCancellableError(JobError):
    """Requested tracked job cannot be cancelled in its current state."""


class InvalidationTargetError(JobError):
    """Requested cache invalidation target does not exist in the saved plan."""


_CREDENTIAL_PATTERN = re.compile(
    r"(?ix)"
    r"(?<![A-Za-z0-9_-])"
    r"([\"']?(?:(?:client[_-]?(?:id|secret)|private[_-]?key|refresh[_-]?token|access[_-]?token|id[_-]?token|"
    r"aws[_-]?(?:secret[_-]?access[_-]?key|access[_-]?key[_-]?id)|access[_-]?key[_-]?id)|"
    r"password|passwd|secret|token|(?:x[_-]?)?api[_ -]?key|(?:x[_-]?)?authorization|credential)[\"']?)"
    r"\s*(?:=|:)\s*(?:[\"'][^\"']*[\"']|[^\s,;}\]]+)"
)
_BARE_CREDENTIAL_PATTERN = re.compile(
    r"(?ix)"
    r"(?<![A-Za-z0-9_-])"
    r"(?:client[_-]?(?:id|secret)|private[_-]?key|refresh[_-]?token|access[_-]?token|id[_-]?token|"
    r"aws[_-]?(?:secret[_-]?access[_-]?key|access[_-]?key[_-]?id)|"
    r"password|passwd|secret|token|(?:x[_-]?)?api[_ -]?key|(?:x[_-]?)?authorization|credential)"
    r"\s+(?:Bearer\s+)?(?!header\b|missing\b|invalid\b|provided\b|required\b|is\b|was\b|not\b)[^\s,;}\]]+"
)
_BEARER_PATTERN = re.compile(r"(?i)\bBearer\s+[^\s,;}\]]+")
_URI_PATTERN = re.compile(r"\b[A-Za-z][A-Za-z0-9+.-]*://[^\s'\"<>]+")
_TRACEBACK_PATTERN = re.compile(r"\bTraceback\b", re.IGNORECASE)
_FILE_FRAGMENT_PATTERN = re.compile(r"\bFile\s+(?:['\"]|/|[A-Za-z]:[\\/])", re.IGNORECASE)
_WINDOWS_PATH_PATTERN = re.compile(r"\b[A-Za-z]:[\\/][^\s'\"<>]*")
_ROOTED_WINDOWS_PATH_PATTERN = re.compile(r"(?<![A-Za-z0-9])\\{1,2}(?=[^\\/\s'\"<>])[^\s'\"<>]*")
_ABSOLUTE_PATH_PATTERN = re.compile(r"(?<![A-Za-z0-9])/(?:[^/\s'\"<>]+(?:/[^/\s'\"<>]*)*)?")
_TRAVERSAL_PATH_PATTERN = re.compile(r"(?<![A-Za-z0-9])[^\s]*\.\.[\\/][^\s]*")
_SAFE_FALLBACK = "request failed"
_MAX_PUBLIC_MESSAGE_LENGTH = 4096
_CONTROL_CHARACTER_PATTERN = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")
_REMEDIATION_COMMAND_PARTS = 4
_UNSAFE_PATTERNS = (
    _CREDENTIAL_PATTERN,
    _BARE_CREDENTIAL_PATTERN,
    _BEARER_PATTERN,
    _URI_PATTERN,
    _TRACEBACK_PATTERN,
    _FILE_FRAGMENT_PATTERN,
    _WINDOWS_PATH_PATTERN,
    _ROOTED_WINDOWS_PATH_PATTERN,
    _ABSOLUTE_PATH_PATTERN,
    _TRAVERSAL_PATH_PATTERN,
)


def _scrub_credentials(text: str) -> str:
    """Strip ``key=secret`` style credential patterns from ``text``."""
    return _CREDENTIAL_PATTERN.sub(r"\1=<redacted>", text)


def _contains_unsafe_content(text: str) -> bool:
    return _CONTROL_CHARACTER_PATTERN.search(text) is not None or any(
        pattern.search(text) is not None for pattern in _UNSAFE_PATTERNS
    )


def _safe_fallback(fallback: str) -> str:
    text = " ".join(line.strip() for line in fallback.splitlines() if line.strip())
    return text if text and not _contains_unsafe_content(text) else _SAFE_FALLBACK


def sanitise_public_message(message: str, *, fallback: str = _SAFE_FALLBACK) -> str:
    """Return a safe message or a stable fallback for sensitive text."""
    safe_fallback = _safe_fallback(fallback)
    if len(message) > _MAX_PUBLIC_MESSAGE_LENGTH or _contains_unsafe_content(message):
        return safe_fallback
    text = " ".join(line.strip() for line in message.splitlines() if line.strip())
    return text[:_MAX_PUBLIC_MESSAGE_LENGTH] if text else safe_fallback


def sanitise_public_remediation(remediation: str) -> str:
    """Sanitise a remediation while hiding identifiers embedded in job commands."""
    safe = sanitise_public_message(remediation)
    parts = safe.split()
    if (
        len(parts) >= _REMEDIATION_COMMAND_PARTS
        and parts[:2] == ["acheron", "job"]
        and parts[2] in {"status", "cancel", "retry"}
    ):
        return f"acheron job {parts[2]} <job-id>"
    return safe


def sanitise_exc_message(exc: BaseException) -> str:
    """Return a public-safe ``"<ClassName>: <first line>"`` summary of ``exc``.

    The raw ``str(exc)`` may embed file paths, internal URLs, library
    internals, or user-submitted text. For API/response surfaces we keep the
    exception class so operators can still distinguish a ``WorkerError`` from
    a generic ``RuntimeError`` while stripping the message body. Traceback
    fragments (``File ...`` / ``Traceback (most recent call last):``) and
    credential-shaped ``key=value`` patterns are also stripped — those
    belong in ``logger.exception`` output, not in machine-readable error
    fields.
    """
    safe_message = sanitise_public_message(str(exc), fallback="<no message>")
    return f"{type(exc).__name__}: {safe_message}"
