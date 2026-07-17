"""Structured errors shared by the ingestion core and HTTP adapter."""


class IngestError(ValueError):
    """A safe, stable ingestion failure that can be returned to API clients."""

    def __init__(self, code: str, message: str, *, status_code: int = 400) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.status_code = status_code
