"""Domain exception hierarchy for Acheron."""


class AcheronError(Exception):
    """Base exception for all Acheron errors."""


class PlanError(AcheronError):
    """Errors related to plan compilation or validation."""


class InvalidLanguagePathError(PlanError):
    """Requested source/target language pair is not supported."""


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
