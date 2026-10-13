"""Exception types exposed by the public API."""

from __future__ import annotations

import json


def body_field(body: str | None, key: str) -> str | None:
    """Return ``key`` from a JSON object error body when it is a string, else ``None``."""
    if not body:
        return None
    try:
        payload = json.loads(body)
    except ValueError:
        return None
    value = payload.get(key) if isinstance(payload, dict) else None
    return value if isinstance(value, str) else None


class FitatuApiError(RuntimeError):
    """Raised when an API request fails or a response shape is invalid.

    For HTTP errors the message reads ``"{METHOD} {path} -> {status} {codename}: {server
    message}"``, so it is useful on its own when a tool layer only shows ``str(exc)``.

    Attributes:
        status_code: HTTP status code returned by the server, or ``None`` for
            network-level errors (timeout, connection refused, etc.) and local checks.
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
        return body_field(self.body, "codename")

    def __repr__(self) -> str:
        return f"{type(self).__name__}(status_code={self.status_code!r}, message={str(self)!r})"


class FitatuConnectionError(FitatuApiError):
    """Raised when the request never got an HTTP response (DNS, timeout, reset...)."""


class FitatuAuthError(FitatuApiError):
    """Raised on 401: the session is gone and refreshing the token did not help.

    :meth:`FitatuApiClient.login` raises it too when Fitatu rejects the e-mail or
    password (422, or 401).

    403 is not mapped here because the API also uses it for premium-only features and
    exhausted AI quotas (check ``codename``), and a wrong API key/secret.

    Attributes:
        hint: What to do when logging in again is not the fix (set for a rejected
            login), else ``None``; :func:`describe_error` reports it.
    """

    def __init__(self, message: str, status_code: int | None = None, body: str | None = None, hint: str | None = None):
        super().__init__(message, status_code=status_code, body=body)
        self.hint = hint


class FitatuValidationError(FitatuApiError, ValueError):
    """Raised before any request is sent when an argument is invalid.

    It is also a ``ValueError``, so both ``except FitatuApiError`` and
    ``except ValueError`` catch it.
    """


class FitatuNotFoundError(FitatuValidationError):
    """Raised when an id, meal or row the caller named is not there."""


class FitatuRateLimitError(FitatuApiError):
    """Raised on 429 once retries are exhausted.

    Attributes:
        retry_after: Seconds the server asked to wait (``Retry-After``), if it said.
    """

    def __init__(
        self,
        message: str,
        status_code: int | None = None,
        body: str | None = None,
        retry_after: float | None = None,
    ):
        super().__init__(message, status_code=status_code, body=body)
        self.retry_after = retry_after


class FitatuMaintenanceError(FitatuApiError):
    """Raised when Fitatu answers with its ``technical-break`` header (planned maintenance).

    The app stops calling the API until the break is over; retrying, refreshing the
    token or logging in again does not help.

    Attributes:
        display_from: Start of the break as the server sent it (``displayFromDate``).
        display_to: End of the break as the server sent it (``displayToDate``).
    """

    def __init__(
        self,
        message: str,
        status_code: int | None = None,
        body: str | None = None,
        display_from: str | None = None,
        display_to: str | None = None,
    ):
        super().__init__(message, status_code=status_code, body=body)
        self.display_from = display_from
        self.display_to = display_to


class FitatuPlannerSyncError(FitatuApiError):
    """Raised when the day sync endpoint accepts a request but rejects one or more days.

    ``POST /diet-plan/{user_id}/days`` answers ``202`` even when nothing was saved;
    the reason is only reported per day in the response body.

    Attributes:
        results: The parsed ``[{"date", "revision", "errorMessage"}, ...]`` response.
    """

    def __init__(self, message: str, results: list[dict[str, object]] | None = None):
        # results must stay optional: unpickling rebuilds the error from args, i.e. (message,)
        super().__init__(message, status_code=202)
        self.results = results if results is not None else []


def describe_error(exc: BaseException) -> dict[str, object]:
    """Return a JSON-safe summary of *exc* for tool results and logs.

    ``error_type`` is one of ``auth``, ``rate_limit``, ``maintenance``, ``network``,
    ``sync_rejected``, ``validation``, ``not_found``, ``forbidden``, ``server`` or
    ``unexpected``. ``retryable`` says whether the same call can succeed later without
    any change (after ``retry_after`` seconds when that is set); ``hint`` says what to
    do instead when it cannot.
    """
    status = getattr(exc, "status_code", None)
    error_type, retryable, hint = "unexpected", False, None
    if isinstance(exc, FitatuNotFoundError) or (type(exc) is FitatuApiError and status == 404):
        error_type, hint = "not_found", "Check the id, meal or date; read the day again to get current row ids."
    elif isinstance(exc, ValueError | TypeError):  # FitatuValidationError is a ValueError
        error_type = "validation"
    elif isinstance(exc, FitatuAuthError):
        error_type, hint = "auth", exc.hint or (
            "The session is no longer valid: run `fitatu-api login` (`fitatu-api import-session` "
            "for an account without a password) and retry."
        )
    elif isinstance(exc, FitatuRateLimitError):
        error_type, retryable = "rate_limit", True
    elif isinstance(exc, FitatuMaintenanceError):
        error_type, hint = "maintenance", f"Fitatu is down for maintenance until {exc.display_to or 'further notice'}."
    elif isinstance(exc, FitatuConnectionError):
        error_type, retryable = "network", True
    elif isinstance(exc, FitatuPlannerSyncError):
        error_type, hint = "sync_rejected", "Nothing was saved for the rejected day; fix the rows and send them again."
    elif isinstance(exc, FitatuApiError):
        if status == 403:
            error_type, hint = "forbidden", "Premium-only feature, used-up AI quota (see codename) or a wrong API key."
        elif status in (400, 409, 422):
            error_type = "validation"
        else:
            # No status: a reply the library could not read, which a retry would not fix.
            error_type, retryable = "server", status is not None and status >= 500
    return {
        "error_type": error_type,
        "message": str(exc)[:500],
        "status_code": status,
        "codename": getattr(exc, "codename", None),
        "retryable": retryable,
        "retry_after": getattr(exc, "retry_after", None),
        "hint": hint,
    }
