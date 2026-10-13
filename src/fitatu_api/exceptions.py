"""Exception types exposed by the public API."""


class FitatuApiError(RuntimeError):
    """Raised when an API request fails or a response shape is invalid.

    Attributes:
        status_code: HTTP status code returned by the server, or ``None`` for
            network-level errors (timeout, connection refused, etc.).
        body: Raw response body text, or ``None`` when not available.
    """

    def __init__(self, message: str, status_code: int | None = None, body: str | None = None):
        super().__init__(message)
        self.status_code = status_code
        self.body = body

    def __repr__(self) -> str:
        return f"FitatuApiError(status_code={self.status_code!r}, message={str(self)!r})"


class FitatuPlannerSyncError(FitatuApiError):
    """Raised when the day sync endpoint accepts a request but rejects one or more days.

    ``POST /diet-plan/{user_id}/days`` answers ``202`` even when nothing was saved;
    the reason is only reported per day in the response body.

    Attributes:
        results: The parsed ``[{"date", "revision", "errorMessage"}, ...]`` response.
    """

    def __init__(self, message: str, results: list[dict[str, object]]):
        super().__init__(message, status_code=202)
        self.results = results
