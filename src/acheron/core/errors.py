class AcheronError(Exception):
    pass


class PlanError(AcheronError):
    pass


class InvalidLanguagePathError(PlanError):
    pass


class PlanValidationError(PlanError):
    pass


class WorkerError(AcheronError):
    pass


class WorkerUnavailableError(WorkerError):
    pass


class WorkerTimeoutError(WorkerError):
    pass


class CacheError(AcheronError):
    pass


class CacheMissError(CacheError):
    pass


class CacheCorruptedError(CacheError):
    pass
