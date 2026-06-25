"""Domain exception hierarchy for Acheron."""


class AcheronError(Exception):
    """Base exception for all Acheron errors."""


class PathNotAllowedError(AcheronError):
    """A path submitted for filesystem access resolves outside the configured allowlist."""


class PlanError(AcheronError):
    """Errors related to plan compilation or validation."""


class InvalidLanguagePathError(PlanError):
    """Requested source/target language pair is not supported."""


class ChunkingTooLongForWorkerError(PlanError):
    """Chunking step's max_chunk_length exceeds a text-input worker's max_input_tokens.

    Raised at plan compile time so misconfigurations fail fast, before any GPU time.
    """


class PlanValidationError(PlanError):
    """Plan failed structural validation."""


class WorkerError(AcheronError):
    """Errors related to worker communication or execution."""


class WorkerUnavailableError(WorkerError):
    """Worker is not reachable or has been removed from the registry."""


class WorkerTimeoutError(WorkerError):
    """Worker did not respond within the configured timeout."""


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


def sanitise_exc_message(exc: BaseException) -> str:
    """Return a public-safe ``"<ClassName>: <first line>"`` summary of ``exc``.

    The raw ``str(exc)`` may embed file paths, internal URLs, library
    internals, or user-submitted text. For API/response surfaces we keep the
    exception class so operators can still distinguish a ``WorkerError`` from
    a generic ``RuntimeError`` while stripping the message body. Traceback
    fragments (``File ...`` / ``Traceback (most recent call last):``) are
    also stripped — those belong in ``logger.exception`` output, not in
    machine-readable error fields.
    """
    raw = str(exc)
    first_line = ""
    for line in raw.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        if stripped.startswith(("File ", "Traceback")):
            continue
        first_line = stripped
        break
    if not first_line:
        first_line = "<no message>"
    return f"{type(exc).__name__}: {first_line}"
