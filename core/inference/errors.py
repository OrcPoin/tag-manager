"""User-facing managed inference errors."""


class BackendError(RuntimeError):
    """Base error for a managed inference backend."""


class ExecutableNotFoundError(BackendError):
    pass


class ModelNotFoundError(BackendError):
    pass


class MmprojNotFoundError(BackendError):
    pass


class PortUnavailableError(BackendError):
    pass


class BackendStartupTimeout(BackendError):
    pass


class BackendOutOfMemoryError(BackendError):
    pass
