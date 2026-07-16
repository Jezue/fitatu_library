"""Exception types exposed by the public API."""

from __future__ import annotations

import json


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

    @property
    def codename(self) -> str | None:
        """Machine-readable error code from the response body, if the server sent one.

        Seen in the wild: ``NUMBER_OF_USES_EXCEEDED`` (AI quota), ``BLOCKED_BY_MODERATION``
        and ``NOT_FOOD_DETECTED``.
        """
        if not self.body:
            return None
        try:
            payload = json.loads(self.body)
        except ValueError:
            return None
        value = payload.get("codename") if isinstance(payload, dict) else None
        return value if isinstance(value, str) else None

    def __repr__(self) -> str:
        return f"{type(self).__name__}(status_code={self.status_code!r}, message={str(self)!r})"


class FitatuConnectionError(FitatuApiError):
    """Raised when the request never got an HTTP response (DNS, timeout, reset...)."""


class FitatuAuthError(FitatuApiError):
    """Raised on 401/403: the token could not be refreshed or the API key/secret was refused."""


class FitatuRateLimitError(FitatuApiError):
    """Raised on 429 once retries are exhausted."""


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
