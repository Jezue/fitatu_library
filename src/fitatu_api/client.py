"""Low-level Fitatu HTTP client."""

from __future__ import annotations

import base64
import json
import logging
import os
import re
import threading
import time
import uuid
from collections.abc import Callable, Iterator, Sequence
from contextlib import contextmanager
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any
from urllib.parse import quote

import requests
from urllib3.exceptions import LocationParseError
from urllib3.util import parse_url

from ._shapes import as_dict, as_dict_list, read_reply, write_reply
from ._validation import (
    as_date,
    validate_non_negative_int,
    validate_numeric_id,
    validate_positive_int,
)
from .auth import FitatuAuthContext, FitatuTokenStore, jwt_user_id, resolve_session_path
from .constants import DEFAULT_FITATU_API_BASE_URL
from .exceptions import (
    FitatuApiError,
    FitatuAuthError,
    FitatuConnectionError,
    FitatuMaintenanceError,
    FitatuNotFoundError,
    FitatuRateLimitError,
    FitatuValidationError,
    body_field,
)
from .planner import PlannerModule
from .service_modules import (
    ActivitiesModule,
    DietPlanModule,
    FastingModule,
    FoodsModule,
    HabitsModule,
    MeasurementsModule,
    ResourcesModule,
    SummaryModule,
    UserSettingsModule,
    WaterModule,
)

logger = logging.getLogger(__name__)

_SAFE_METHODS = frozenset({"GET", "HEAD", "OPTIONS"})
# 520-524: Cloudflare in front of the API could not reach it or got no answer in time.
_RETRY_STATUSES = frozenset({429, 500, 502, 503, 504, 520, 521, 522, 523, 524})
MAX_RETRY_AFTER_SECONDS = 30.0
# Under a time limit, a retry needs this long after its wait (or its own timeout, if shorter):
# with less it would only time out, and the error reported would be that instead of the real one.
_MIN_RETRY_SECONDS = 2.0
# Refresh this long before the bearer token's exp, so a call does not start with a 401.
REFRESH_MARGIN = timedelta(seconds=60)
# describe_error's hint for a rejected login, where "log in again" is no advice.
_LOGIN_REJECTED_HINT = (
    "Check the e-mail and password. Accounts created with Apple or Facebook have no password: "
    "copy the session from the web app with `fitatu-api import-session` instead "
    "(steps: `fitatu-api import-session --help`)."
)


_SECRET_KEY = re.compile(r"token|secret|password|authorization", re.IGNORECASE)


def _serialize_log_value(value: Any) -> Any:
    """Recursively coerce a value to a JSON-safe type for structured logging.

    Values under keys that look secret are replaced; flags such as ``has_bearer_token``
    stay readable.
    """
    if isinstance(value, dict):
        return {
            str(key): (
                "[redacted]"
                if _SECRET_KEY.search(str(key)) and not isinstance(inner, bool | None)
                else _serialize_log_value(inner)
            )
            for key, inner in value.items()
        }
    if isinstance(value, list):
        return [_serialize_log_value(item) for item in value]
    if isinstance(value, tuple):
        return [_serialize_log_value(item) for item in value]
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    return str(value)


def _log_event(event: str, level: int = logging.INFO, **fields: Any) -> None:
    """Emit a structured JSON log line for a named event (secret-looking keys redacted)."""
    if logger.isEnabledFor(level):
        payload = _serialize_log_value({"event": event, **fields})
        logger.log(level, json.dumps(payload, ensure_ascii=True, sort_keys=True))


def _wait_before_retry(delay: float, **fields: Any) -> None:
    """Log the retry (``attempt`` is the one that failed, ``reason`` why) and sleep ``delay`` seconds."""
    _log_event("fitatu.request.retry", logging.DEBUG, delay_seconds=delay, **fields)
    time.sleep(delay)


def _base_url_for_locale(storage_locale: str) -> str:
    """The API host of a storage locale: ``pl_PL`` -> ``https://pl-pl.fitatu.com/api``."""
    if re.fullmatch(r"[a-z]{2}_[A-Z]{2}", storage_locale):
        return f"https://{storage_locale.lower().replace('_', '-')}.fitatu.com/api"
    return DEFAULT_FITATU_API_BASE_URL


def _is_fitatu_url(url: str) -> bool:
    """True for https URLs on fitatu.com or one of its subdomains, without userinfo.

    The host is read with urllib3's parser, which is what requests connects with:
    ``urlsplit`` reads ``https://evil.example\\@pl-pl.fitatu.com`` as a Fitatu host.
    """
    try:
        parts = parse_url(url)
    except LocationParseError:
        return False
    return (
        parts.scheme == "https"
        and parts.auth is None
        and re.fullmatch(r"(?:[a-z0-9-]+\.)*fitatu\.com", parts.host or "") is not None
    )


def _new_correlation_id() -> str:
    """Return a fresh random hex correlation id for request tracing."""
    return uuid.uuid4().hex


def _error_message(method: str, path: str, status_code: int, body: str) -> str:
    """``GET /path -> 404 CODENAME: server message``, the part of an error a model gets to see."""
    message = f"{method} {path} -> {status_code}"
    codename = body_field(body, "codename")
    if codename:
        message += f" {codename}"
    detail = body_field(body, "message") or body.strip()
    if detail:
        message += f": {detail[:200]}"
    return message


def _retry_after_seconds(header: str | None) -> float | None:
    """Parse a numeric ``Retry-After`` header; HTTP dates and garbage count as absent."""
    try:
        value = float(header) if header else None
    except ValueError:
        return None
    return value if value is not None and value >= 0 else None


def _raise_on_technical_break(response: requests.Response, method: str, path: str) -> None:
    """Raise FitatuMaintenanceError when the response carries the ``technical-break`` header.

    The header is base64 JSON (``displayFromDate``, ``displayToDate``), sometimes quoted.
    """
    raw = response.headers.get("technical-break")
    if not raw:
        return
    value = raw.strip().strip('"')
    info: dict[str, Any] = {}
    try:
        decoded = json.loads(base64.b64decode(value + "=" * (-len(value) % 4), altchars=b"-_"))
    except ValueError:
        decoded = None
    if isinstance(decoded, dict):
        info = decoded
    display_from, display_to = info.get("displayFromDate"), info.get("displayToDate")
    window = f" from {display_from} to {display_to}" if display_from or display_to else ""
    raise FitatuMaintenanceError(
        f"{method} {path} -> Fitatu is in a technical break{window}; try again after it ends",
        status_code=response.status_code,
        body=raw,
        display_from=display_from,
        display_to=display_to,
    )


class FitatuApiClient:
    """Main low-level API client for Fitatu.

    ``base_url`` defaults to the session's ``base_url``, else the host of the account's
    storage locale (``pl_PL`` -> ``https://pl-pl.fitatu.com/api``). ``token_store_path``
    names a JSON file that refreshed tokens are merged into; :meth:`from_session` uses
    the session file itself.
    """

    def __init__(
        self,
        auth: FitatuAuthContext,
        base_url: str | None = None,
        timeout_seconds: int = 25,
        retry_max_attempts: int = 3,
        retry_base_delay_seconds: float = 0.5,
        token_store_path: str | Path | None = None,
        persist_tokens: bool = True,
    ) -> None:
        base_url = base_url or auth.base_url or _base_url_for_locale(auth.app_storage_locale)
        if not _is_fitatu_url(base_url):
            raise FitatuValidationError(
                f"base_url must be an https://<region>.fitatu.com/api URL, got {base_url!r}"
            )
        self.auth = auth
        self.base_url = auth.base_url = base_url.rstrip("/")
        self.timeout_seconds = timeout_seconds
        self.retry_max_attempts = max(1, int(retry_max_attempts))
        self.retry_base_delay_seconds = max(0.0, float(retry_base_delay_seconds))
        self.persist_tokens = persist_tokens
        self.token_store = FitatuTokenStore(Path(token_store_path)) if token_store_path else None
        self._http = requests.Session()
        self._refresh_lock = threading.Lock()
        # Per thread: the time.monotonic() by which a time_limit block's requests must be done.
        self._deadline = threading.local()
        # A refresh token /token/refresh rejected; no automatic refresh until it changes.
        self._rejected_refresh_token: str | None = None
        # Off once a token fresh from /token/refresh looked expired: the local clock is off.
        self._refresh_ahead = True
        # The (bearer, refresh) pair the token store held when this client last read or wrote it.
        self._stored_tokens: tuple[str | None, str | None] = (None, None)
        self._load_tokens_from_store()
        self.planner = PlannerModule(self)
        self.user_settings = UserSettingsModule(self)
        self.diet_plan = DietPlanModule(self)
        self.water = WaterModule(self)
        self.activities = ActivitiesModule(self)
        self.resources = ResourcesModule(self)
        self.measurements = MeasurementsModule(self)
        self.summary = SummaryModule(self)
        self.foods = FoodsModule(self)
        self.fasting = FastingModule(self)
        self.habits = HabitsModule(self)

    @classmethod
    def from_session(cls, path: str | Path | None = None, **kwargs: Any) -> FitatuApiClient:
        """Load the client from a session file and keep that file up to date.

        The file is ``path``, else ``$FITATU_SESSION`` (or ``$FITATU_SESSION_PATH``),
        else ``~/.fitatu/session.json``, which is where ``fitatu-api login`` writes it.
        It doubles as the token store, so refreshed tokens are merged back into it.

        Without ``path``, ``$FITATU_SESSION_JSON`` holding the session JSON itself (``fitatu-api
        login --print``) comes first, for containers and cron jobs without a writable file;
        tokens refreshed from it stay in memory.

        Args:
            path: Session file to read; see above for the default.
            **kwargs: Forwarded to :meth:`__init__` (e.g. ``timeout_seconds``).

        Raises:
            FitatuAuthError: When the file is missing or unreadable, or
                ``$FITATU_SESSION_JSON`` is not a session; run ``fitatu-api login``.
        """
        session_path = resolve_session_path(path)
        inline = os.environ.get("FITATU_SESSION_JSON") if path is None else None
        where = "$FITATU_SESSION_JSON" if inline else str(session_path)
        try:
            data = json.loads(inline or session_path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            raise FitatuAuthError(
                f"no Fitatu session at {session_path}; run `fitatu-api login` to create one"
            ) from None
        except (OSError, ValueError) as exc:
            raise FitatuAuthError(
                f"cannot read the Fitatu session at {where} ({exc}); run `fitatu-api login`"
            ) from exc
        if not isinstance(data, dict):
            raise FitatuAuthError(f"{where} is not a Fitatu session; run `fitatu-api login`")
        if inline:
            kwargs.setdefault("persist_tokens", False)
        else:
            kwargs.setdefault("token_store_path", session_path)
        return cls(FitatuAuthContext.from_session_data(data), **kwargs)

    @classmethod
    def login(
        cls,
        email: str,
        password: str,
        base_url: str | None = None,
        **kwargs: Any,
    ) -> FitatuApiClient:
        """Authenticate with email and password and return a ready-to-use client.

        Calls ``POST /login`` with the supplied credentials (through :meth:`request`, so
        with the same headers and error mapping as every other call), decodes the user id
        from the returned JWT and reads ``GET /users/{id}`` for the account's locales and
        timezone. With a ``token_store_path`` the whole session is merged into that file.

        Args:
            email: Fitatu account e-mail address.
            password: Fitatu account password.
            base_url: API host. By default login goes to ``https://pl-pl.fitatu.com/api``
                and the client then switches to the host of the account's storage
                locale (e.g. ``de_DE`` -> ``https://de-de.fitatu.com/api``).
            **kwargs: Forwarded verbatim to :meth:`__init__` (e.g. ``timeout_seconds``).

        Raises:
            FitatuAuthError: When the server rejects the e-mail or password (422, or 401);
                its ``hint`` points accounts without a password to ``fitatu-api import-session``.
            FitatuConnectionError: When the login request gets no HTTP response.
            FitatuApiError: For any other failure, e.g. a response without a token field.
        """
        client = cls(auth=FitatuAuthContext(), base_url=base_url or DEFAULT_FITATU_API_BASE_URL, **kwargs)
        try:
            data = client.request(
                "POST",
                "/login",
                json_data={"_username": email, "_password": password},
                include_auth=False,
                allow_refresh_on_401=False,
            )
        except FitatuApiError as exc:
            # Fitatu answers a wrong or unknown e-mail or password with 422, not 401 (checked live).
            if exc.status_code not in (401, 422) or isinstance(exc, FitatuMaintenanceError):
                raise
            raise FitatuAuthError(
                f"POST /login -> {exc.status_code}: the e-mail or password is wrong",
                status_code=exc.status_code,
                body=exc.body,
                hint=_LOGIN_REJECTED_HINT,
            ) from None
        reply = as_dict(data) or {}
        token = reply.get("token")
        if not token:
            raise FitatuApiError("POST /login -> the response did not contain a token")
        refresh = reply.get("refreshToken") or reply.get("refresh_token")
        # Set after __init__, which may have seeded older tokens from a token store.
        client.auth.bearer_token = str(token)
        client.auth.refresh_token = str(refresh) if refresh else None
        client.auth.fitatu_user_id = jwt_user_id(client.auth.bearer_token)
        _log_event("fitatu.auth.login", user_id=client.auth.fitatu_user_id)
        if client.auth.fitatu_user_id:
            client._read_profile(client.auth.fitatu_user_id, pick_host=base_url is None)
        client._persist_tokens_to_store(**client.auth.to_session_data())
        return client

    def _read_profile(self, user_id: str, *, pick_host: bool) -> None:
        """Take the account's locales and timezone from ``GET /users/{id}`` and, with
        ``pick_host``, switch to the host of its storage locale.

        Shared by :meth:`login` and ``fitatu-api import-session``, so both sessions get the
        same fields.
        """
        profile = self.user_settings.get_profile(user_id)
        for attribute, key in (
            ("app_locale", "locale"),
            ("app_search_locale", "searchLocale"),
            ("app_storage_locale", "storageLocale"),
            ("app_timezone", "timezone"),
        ):
            if isinstance(profile.get(key), str) and profile[key]:
                setattr(self.auth, attribute, profile[key])
        if pick_host:
            self.base_url = self.auth.base_url = _base_url_for_locale(self.auth.app_storage_locale)

    def _load_tokens_from_store(self) -> None:
        """Seed tokens (and a missing user id) from the on-disk token store."""
        if self.token_store is None:
            return
        token_data = self.token_store.load()
        bearer = token_data.get("bearer_token")
        refresh = token_data.get("refresh_token")
        self._stored_tokens = (bearer, refresh)
        if bearer:
            self.auth.bearer_token = bearer
        if refresh:
            self.auth.refresh_token = refresh
        if not self.auth.fitatu_user_id:
            self.auth.fitatu_user_id = token_data.get("fitatu_user_id")

    def _take_tokens_saved_elsewhere(self) -> bool:
        """Adopt a token pair that another client (e.g. another process sharing the session
        file) saved to the token store since this client last read or wrote it.

        A pair whose bearer belongs to another account (someone logged in to another account
        with the same file) is not taken, and this client stops writing to the store, so it
        neither acts for that account nor overwrites its login.
        """
        if self.token_store is None:
            return False
        token_data = self.token_store.load()
        stored = (token_data.get("bearer_token"), token_data.get("refresh_token"))
        if not stored[0] or stored[0] == self.auth.bearer_token or stored == self._stored_tokens:
            return False
        self._stored_tokens = stored
        owner = jwt_user_id(stored[0])
        if owner and self.auth.fitatu_user_id and owner != self.auth.fitatu_user_id:
            self.persist_tokens = False
            _log_event("fitatu.auth.store_other_account", logging.WARNING, user_id=self.auth.fitatu_user_id)
            return False
        self.auth.bearer_token, self.auth.refresh_token = stored
        return True

    def _persist_tokens_to_store(self, **fields: Any) -> None:
        """Merge the current token pair (and other session ``fields``) into the token store."""
        if not self.persist_tokens or self.token_store is None:
            return
        self.token_store.save(
            bearer_token=self.auth.bearer_token,
            refresh_token=self.auth.refresh_token,
            **fields,
        )
        self._stored_tokens = (self.auth.bearer_token, self.auth.refresh_token)

    def close(self) -> None:
        """Close the pooled HTTP connections; the client is also a context manager."""
        self._http.close()

    @contextmanager
    def time_limit(self, seconds: float | None) -> Iterator[None]:
        """Make the requests sent in this block (by this thread) end within ``seconds`` in all.

        Retries and their waits (``Retry-After`` included) count: a retry that would start less
        than 2 seconds before the limit is not made and the last error is raised as it is, each
        request's timeout is cut to the time left, and a request with no time left is not sent
        (:class:`FitatuConnectionError`). ``None`` sets no limit. :class:`FitatuTools` puts every
        tool call in one, so that it ends before an MCP host gives up on it.
        """
        previous = getattr(self._deadline, "at", None)
        self._deadline.at = None if seconds is None else time.monotonic() + seconds
        try:
            yield
        finally:
            self._deadline.at = previous

    def _has_time_for(self, delay: float) -> bool:
        """Whether a retry after ``delay`` seconds would still have time to get an answer within the time limit."""
        deadline = getattr(self._deadline, "at", None)
        return deadline is None or time.monotonic() + delay + min(self.timeout_seconds, _MIN_RETRY_SECONDS) < deadline

    def __enter__(self) -> FitatuApiClient:
        """Use the client as a context manager for deterministic cleanup."""
        return self

    def __exit__(self, exc_type: object, exc: object, traceback: object) -> None:
        """Close managed resources when leaving a context block."""
        self.close()

    def describe_auth_state(self) -> dict[str, Any]:
        """Return a non-secret snapshot of the current client auth state."""
        state = self.auth.snapshot()
        state.update(
            {
                "base_url": self.base_url,
                "retry_max_attempts": self.retry_max_attempts,
                "persist_tokens": self.persist_tokens and self.token_store is not None,
                "has_token_store": self.token_store is not None,
            }
        )
        return state

    def clear_auth(self, *, clear_token_store: bool = True) -> None:
        """Clear in-memory auth and optionally the persisted token store."""
        correlation_id = _new_correlation_id()
        _log_event(
            "fitatu.auth.clear",
            correlation_id=correlation_id,
            clear_token_store=clear_token_store,
            state_before=self.describe_auth_state(),
        )
        self.auth.bearer_token = None
        self.auth.refresh_token = None
        if clear_token_store and self.token_store is not None:
            self.token_store.clear()
        _log_event("fitatu.auth.cleared", correlation_id=correlation_id, state_after=self.describe_auth_state())

    def _backoff_delay_seconds(self, attempt: int) -> float:
        """Exponential delay before retry number ``attempt``."""
        return self.retry_base_delay_seconds * (2 ** max(0, attempt - 1))

    def _headers(self, *, include_auth: bool, has_json: bool) -> dict[str, str]:
        """Build the request headers.

        Only ``API-Key``, ``API-Secret`` and, on user routes, ``Authorization`` are
        required. ``APP-SearchLocale`` (together with the ``locale`` query param) changes
        search results, and the other locale and timezone headers describe the user. The
        rest (``app-uuid``, ``api-cluster``, ``app-os``, ``app-version``, user agent) only
        identify the client, always as the web app (``WEB_CLIENT_PROFILE``).
        """
        headers = {
            "accept": "application/json, text/plain, */*",
            "api-key": self.auth.api_key,
            "api-secret": self.auth.api_secret,
            "app-uuid": self.auth.app_uuid,
            "api-cluster": self.auth.api_cluster,
            "app-locale": self.auth.app_locale,
            "app-searchlocale": self.auth.app_search_locale,
            "app-storagelocale": self.auth.app_storage_locale,
            "app-timezone": self.auth.app_timezone,
            "app-os": self.auth.app_os,
            "app-version": self.auth.app_version,
            "user-agent": self.auth.user_agent,
        }
        if has_json:
            headers["content-type"] = "application/json;charset=UTF-8"
        if include_auth and self.auth.bearer_token:
            headers["authorization"] = f"Bearer {self.auth.bearer_token}"
        return headers

    def _url(self, path: str) -> str:
        """Resolve a relative path or absolute URL against the configured base_url.

        Absolute URLs must be https on a fitatu.com host; anything else raises
        FitatuValidationError, so a URL taken from model output cannot leak the session.
        """
        if "://" in path:
            if not _is_fitatu_url(path):
                raise FitatuValidationError(f"refusing to call a non-Fitatu URL: {path!r}")
            return path
        if path.startswith("/"):
            return f"{self.base_url}{path}"
        return f"{self.base_url}/{path}"

    def request(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        json_data: Any | None = None,
        include_auth: bool = True,
        allow_refresh_on_401: bool = True,
        raw: bool = False,
        idempotent: bool = False,
    ) -> Any:
        """Perform a Fitatu HTTP request with retries and optional auto-refresh.

        A bearer token that expires within 60 seconds (JWT ``exp``) is refreshed before
        the call; a 401 still triggers one refresh and one resend.

        Network errors, 429 and 5xx are retried for GET, HEAD and OPTIONS, and for other
        methods only with ``idempotent=True`` (a write that is safe to repeat, such as a
        PUT of an absolute value). The server's ``Retry-After`` is honoured up to
        30 seconds; a longer wait raises :class:`FitatuRateLimitError` with
        ``retry_after`` set instead of blocking. Inside :meth:`time_limit`, a retry is made
        only when its wait ends within the limit.

        With ``raw=True`` the response body is returned as ``bytes`` (``None`` when
        empty) instead of being decoded as JSON, e.g. for file downloads.

        Redirects are not followed: a 3xx raises :class:`FitatuApiError`, so a request
        body and the API headers never reach a host the URL check did not see.
        """
        correlation_id = _new_correlation_id()
        started = time.monotonic()
        method_upper = method.upper()
        url = self._url(path)
        retryable = idempotent or method_upper in _SAFE_METHODS
        # The bearer token only goes to the API itself (not e.g. the CMS on www.fitatu.com).
        include_auth = include_auth and any(
            url.startswith(f"{root}/") for root in (self.base_url, "https://fitatu.com/api")
        )
        can_refresh = include_auth and allow_refresh_on_401
        _log_event(
            "fitatu.request.start",
            logging.DEBUG,
            correlation_id=correlation_id,
            method=method_upper,
            path=path,
            include_auth=include_auth,
            params=params,
            has_json=json_data is not None,
        )
        token = self.auth.bearer_token
        expires_at = self.auth.bearer_expires_at
        if can_refresh and self._refresh_ahead and expires_at is not None and expires_at < datetime.now(UTC) + REFRESH_MARGIN:
            try:
                self._auto_refresh(token, correlation_id)
            except FitatuMaintenanceError:
                raise
            except FitatuApiError:
                pass  # best effort: the token may still be valid, and a 401 refreshes again

        for attempt in range(1, self.retry_max_attempts + 1):
            sent_token = self.auth.bearer_token
            try:
                response = self._send(method_upper, url, path, params, json_data, include_auth)
                if response.status_code == 401 and can_refresh and self._auto_refresh(sent_token, correlation_id):
                    response = self._send(method_upper, url, path, params, json_data, include_auth)
            except requests.exceptions.InvalidHeader:
                # Not retried, and its text is dropped: it quotes the header value, e.g. the bearer token.
                raise FitatuValidationError(
                    f"{method_upper} {path} -> a header value contains a line break or leading "
                    "whitespace; check the tokens and locales in the session file"
                ) from None
            except requests.RequestException as exc:
                # The exception's type only: its text can quote the URL with its query (search phrases).
                reason = f"network error: {type(exc).__name__}"
                delay = self._backoff_delay_seconds(attempt)
                if retryable and attempt < self.retry_max_attempts and self._has_time_for(delay):
                    _wait_before_retry(
                        delay,
                        correlation_id=correlation_id,
                        method=method_upper,
                        path=path,
                        attempt=attempt,
                        reason=reason,
                    )
                    continue
                _log_event(
                    "fitatu.request.error",
                    logging.WARNING,
                    correlation_id=correlation_id,
                    method=method_upper,
                    path=path,
                    attempt=attempt,
                    elapsed_ms=round((time.monotonic() - started) * 1000),
                    reason=reason,
                )
                raise FitatuConnectionError(
                    message=f"{method_upper} {path} -> network error: {exc}",
                    body=str(exc),
                ) from exc

            status = response.status_code
            retry_after = _retry_after_seconds(response.headers.get("retry-after"))
            delay = retry_after if retry_after is not None else self._backoff_delay_seconds(attempt)
            if (
                status in _RETRY_STATUSES
                and retryable
                and attempt < self.retry_max_attempts
                and (retry_after is None or retry_after <= MAX_RETRY_AFTER_SECONDS)
                and self._has_time_for(delay)
            ):
                _wait_before_retry(
                    delay,
                    correlation_id=correlation_id,
                    method=method_upper,
                    path=path,
                    attempt=attempt,
                    reason=f"status {status}",
                )
                continue

            if status >= 300:  # redirects are not followed, so a 3xx is an error too
                message = _error_message(method_upper, path, status, response.text)
                _log_event(
                    "fitatu.request.error",
                    # A failure that retrying is for (and did not fix); a 4xx is the caller's to handle, e.g. a 404.
                    logging.WARNING if status == 429 or status >= 500 else logging.DEBUG,
                    correlation_id=correlation_id,
                    method=method_upper,
                    path=path,
                    status_code=status,
                    attempt=attempt,
                    elapsed_ms=round((time.monotonic() - started) * 1000),
                    body=response.text[:300],
                )
                if status == 429:
                    if retry_after is not None:
                        message += f" (retry after {retry_after:g} s)"
                    raise FitatuRateLimitError(message, status_code=status, body=response.text, retry_after=retry_after)
                # 403 is also used for premium/quota refusals, so only 401 means "log in again".
                error_type = {401: FitatuAuthError, 404: FitatuNotFoundError}.get(status, FitatuApiError)
                raise error_type(message, status_code=status, body=response.text)

            _log_event(
                "fitatu.request.ok",
                logging.DEBUG,
                correlation_id=correlation_id,
                method=method_upper,
                path=path,
                status_code=status,
                attempt=attempt,
                elapsed_ms=round((time.monotonic() - started) * 1000),
            )
            if raw:
                return response.content or None
            if not response.text:
                return None
            try:
                return response.json()
            except ValueError:
                return response.text

        raise AssertionError("unreachable: the last attempt returns or raises")

    def _send(
        self,
        method: str,
        url: str,
        path: str,
        params: dict[str, Any] | None,
        json_data: Any | None,
        include_auth: bool,
    ) -> requests.Response:
        """Send one HTTP request on the pooled session, within the time limit (:meth:`time_limit`)."""
        timeout: float = self.timeout_seconds
        deadline = getattr(self._deadline, "at", None)
        if deadline is not None:
            timeout = min(timeout, deadline - time.monotonic())
            if timeout <= 0:
                raise FitatuConnectionError(f"{method} {path} -> not sent: the time limit of this call is up")
        response = self._http.request(
            method=method,
            url=url,
            headers=self._headers(include_auth=include_auth, has_json=json_data is not None),
            params=params,
            json=json_data,
            timeout=timeout,
            # A redirect would resend the body (password, refresh token) and API-Secret to any host.
            allow_redirects=False,
        )
        # Checked before the status: during a break neither refresh nor retry helps.
        _raise_on_technical_break(response, method, path)
        return response

    def _auto_refresh(self, sent_token: str | None, correlation_id: str) -> bool:
        """Refresh a bearer token that is expiring or got a 401; True when there is a new one.

        One refresh at a time: a call that waited for another one's refresh finds the
        token already changed and just uses it. Tokens another process saved to the token
        store (e.g. after logging in again) are used the same way. Fitatu does not rotate
        refresh tokens (checked 2026-10-13), but none of this relies on that.

        False means the session is gone: no refresh token, or /token/refresh rejected it
        (400/401). Any other refresh failure (network, 429, 5xx) is raised as it is, so a
        short outage does not look like a need to log in again.
        """
        with self._refresh_lock:
            if self.auth.bearer_token != sent_token or self._take_tokens_saved_elsewhere():
                return True
            refresh_token = self.auth.refresh_token
            if not refresh_token or refresh_token == self._rejected_refresh_token:
                return False
            try:
                self._refresh(correlation_id)
            except FitatuApiError as exc:
                if exc.status_code in {400, 401}:
                    # Another process may have logged in again and saved a pair that still works.
                    return self._take_tokens_saved_elsewhere()
                raise
            return True

    def refresh_access_token(self, *, correlation_id: str | None = None) -> dict[str, Any]:
        """Get a new bearer token for the refresh token (``POST /token/refresh``).

        Fitatu answers with the same refresh token (it does not rotate them, checked
        2026-10-13); the client takes a different one if the reply ever carries it.

        Returns :meth:`describe_auth_state` after the refresh. Tokens are never part of
        the result; they are in ``client.auth`` and, with a token store, on disk.

        Raises:
            FitatuAuthError: When the session has no refresh token or Fitatu rejects it
                (400 or 401). Automatic refresh then stops until the client gets a
                different one (log in again, load a newer session, or another process
                saves newer tokens to the token store).
            FitatuApiError: Any other failure (network, 429, 5xx, maintenance, a reply
                without a token), as its subclass; the session may still be fine.
        """
        self._refresh(correlation_id or _new_correlation_id())
        return self.describe_auth_state()

    def _refresh(self, correlation_id: str) -> None:
        """``POST /token/refresh`` and take the tokens it returns; raises as :meth:`refresh_access_token` documents."""
        refresh_token = self.auth.refresh_token
        _log_event("fitatu.auth.refresh.start", correlation_id=correlation_id, state_before=self.describe_auth_state())
        try:
            if not refresh_token:
                raise FitatuAuthError("POST /token/refresh -> missing refresh_token")
            # The app sends exactly this body; the server takes no other shape.
            data = self.request(
                "POST",
                "/token/refresh",
                json_data={"refresh_token": refresh_token},
                include_auth=False,
                allow_refresh_on_401=False,
            )
            reply = as_dict(data) or {}
            if not reply.get("token"):
                raise FitatuApiError("POST /token/refresh -> token not found in refresh response")
        except FitatuApiError as exc:
            if exc.status_code in {400, 401}:
                self._rejected_refresh_token = refresh_token
            _log_event(
                "fitatu.auth.refresh.error",
                correlation_id=correlation_id,
                message=str(exc),
                status_code=exc.status_code,
            )
            if exc.status_code == 400 and type(exc) is FitatuApiError:
                # Fitatu rejects a dead refresh token with 400 as well as 401: both mean "log in again".
                raise FitatuAuthError(str(exc), status_code=400, body=exc.body) from None
            raise
        self.auth.bearer_token = str(reply["token"])
        new_refresh = reply.get("refreshToken") or reply.get("refresh_token")
        if new_refresh:
            self.auth.refresh_token = str(new_refresh)
        self._persist_tokens_to_store()
        _log_event("fitatu.auth.refresh.ok", correlation_id=correlation_id, state=self.describe_auth_state())
        expires_at = self.auth.bearer_expires_at
        if self._refresh_ahead and expires_at is not None and expires_at < datetime.now(UTC) + REFRESH_MARGIN:
            # Otherwise every request would start with a refresh; a 401 still refreshes.
            self._refresh_ahead = False
            _log_event(
                "fitatu.auth.clock_skew",
                logging.WARNING,
                bearer_expires_at=expires_at.isoformat(),
                local_time=datetime.now(UTC).isoformat(),
                message="a token fresh from Fitatu looks expired, so this computer's clock is probably "
                "ahead; refreshing before expiry is off for this client",
            )

    @staticmethod
    def normalize_recipe_items(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """Normalize recipe items to the payload shape accepted by Fitatu."""
        normalized: list[dict[str, Any]] = []
        for item in items:
            if "itemId" in item and "measureId" in item:
                normalized.append(
                    {
                        "type": item.get("type", "PRODUCT"),
                        "itemId": item["itemId"],
                        "measureId": item["measureId"],
                        "measureQuantity": item.get("measureQuantity", 1),
                    }
                )
                continue
            food_id = item.get("foodId")
            measure_id = item.get("measureId")
            if food_id is None or measure_id is None:
                continue
            normalized.append(
                {
                    "type": "PRODUCT",
                    "itemId": food_id,
                    "measureId": measure_id,
                    "measureQuantity": item.get("measureQuantity", item.get("quantity", 1)),
                }
            )
        return normalized

    def search_food(
        self,
        phrase: str,
        *,
        locale: str | None = None,
        limit: int = 5,
        page: int = 1,
    ) -> list[dict[str, Any]]:
        """Search the Fitatu food catalog and normalize common response shapes.

        Each result row identifies the product via a ``foodId`` field (not ``id``
        or ``productId``).  Use it directly for follow-up calls::

            results = client.search_food("banan")
            details = client.get_product_details(results[0]["foodId"])
        """
        validate_positive_int(limit, "limit")
        validate_positive_int(page, "page")
        data = self.request(
            "GET",
            "/search/new/food",
            params={
                "phrase": phrase,
                "page": page,
                "locale": locale or self.auth.app_search_locale,
                "limit": limit,
                # PHP-style list key, as the app sends it; a bare repeated key is not the same param.
                "accessType[]": ["FREE", "PREMIUM"],
            },
        )
        if isinstance(data, dict):
            nested = data.get("data")
            nested_items = nested.get("items") if isinstance(nested, dict) else None
            data = data.get("items") or nested_items or data.get("results")
        return as_dict_list(data)

    def get_product_details(self, product_id: int | str) -> dict[str, Any]:
        """Fetch full product details including available measures.

        Only for ``type="PRODUCT"`` search hits; RECIPE hits are read with
        :meth:`get_recipe`. Product and recipe ids are separate number spaces that overlap,
        so a recipe id here is a 404 or an unrelated product with the same number: go by
        the hit's ``type``.

        Measure field names in the returned dict differ by sub-key:

        - ``result["measures"]`` — list of dicts with ``id`` and ``name``
        - ``result["simpleMeasures"]`` — list of dicts with ``id``, ``name``,
          ``weight``, ``capacity``, etc.
        - ``result["initialMeasure"]`` — single dict with ``key``, ``weight``,
          ``unitKey``

        Note: ``measures[n]["id"]`` (or ``simpleMeasures[n]["id"]``) is the value to pass
        as ``measureId`` to the planner add/update helpers. The ``measureId`` key itself
        only appears in search-result rows (``measure.measureId``).
        """
        pid = validate_numeric_id(product_id, "product_id")
        return read_reply(self.request("GET", f"/products/{pid}"), f"GET /products/{pid}")

    def create_recipe(
        self,
        *,
        name: str,
        items: list[dict[str, Any]],
        meal_schema: list[str] | None = None,
        categories: list[Any] | None = None,
        cooking_time: int | None = 2,
        preparation_time: str = "",
        recipe_description: str = "1. automation",
        serving: str = "1",
        shared: bool = False,
        tags: list[Any] | None = None,
    ) -> dict[str, Any]:
        """Create a recipe from normalized product items."""
        body = self._recipe_body(
            name=name, items=items, meal_schema=meal_schema, categories=categories, cooking_time=cooking_time,
            preparation_time=preparation_time, recipe_description=recipe_description, serving=serving, shared=shared, tags=tags,
        )
        return write_reply(self.request("POST", "/recipes", json_data=body))

    def update_recipe(
        self,
        recipe_id: int | str,
        *,
        name: str,
        items: list[dict[str, Any]],
        meal_schema: list[str] | None = None,
        categories: list[Any] | None = None,
        cooking_time: int | None = None,
        preparation_time: str | None = None,
        recipe_description: str = "",
        serving: str = "1",
        shared: bool = False,
        tags: list[Any] | None = None,
    ) -> dict[str, Any]:
        """Replace a recipe the user created (``PUT /recipes/{id}``) with :meth:`create_recipe`'s body.

        The app sends the whole recipe, so every field left out here is replaced by its default:
        pass what :meth:`get_recipe` returned for the fields to keep. Beta: the app's route and
        body, not checked against the live API yet.
        """
        rid = validate_numeric_id(recipe_id, "recipe_id")
        body = self._recipe_body(
            name=name, items=items, meal_schema=meal_schema, categories=categories, cooking_time=cooking_time,
            preparation_time=preparation_time, recipe_description=recipe_description, serving=serving, shared=shared, tags=tags,
        )
        return write_reply(self.request("PUT", f"/recipes/{rid}", json_data=body))

    def _recipe_body(
        self,
        *,
        name: str,
        items: list[dict[str, Any]],
        meal_schema: list[str] | None,
        categories: list[Any] | None,
        cooking_time: int | None,
        preparation_time: str | None,
        recipe_description: str,
        serving: str,
        shared: bool,
        tags: list[Any] | None,
    ) -> dict[str, Any]:
        """The JSON body of a recipe create or update (the app's UpdatedRecipeRequest)."""
        normalized_items = self.normalize_recipe_items(items)
        if not normalized_items:
            raise FitatuValidationError("a recipe needs at least one item with foodId+measureId or itemId+measureId")
        return {
            "name": name,
            "categories": categories,
            "cookingTime": cooking_time,
            "items": normalized_items,
            "mealSchema": meal_schema if meal_schema is not None else [],
            "preparationTime": preparation_time,
            "recipeDescription": recipe_description,
            "serving": serving,
            "shared": shared,
            "tags": tags or [],
        }

    def create_product(
        self,
        *,
        name: str,
        brand: str,
        energy: float | int,
        protein: float | int,
        fat: float | int,
        carbohydrate: float | int,
        producer: str | None = None,
        portion_weight: float | int | None = None,
        fiber: float | int | None = None,
        sugars: float | int | None = None,
        sodium: float | int | None = None,
        saturated_fat: float | int | None = None,
        salt: float | int | None = None,
    ) -> dict[str, Any]:
        """Create a custom product in the Fitatu catalog.

        Note: the ``measures`` field is intentionally absent — including it in the
        POST /products payload causes a 404 from the backend.  Custom measures cannot
        be set at creation time via the current API surface.
        """
        payload: dict[str, Any] = {
            "name": name,
            "brand": brand,
            "energy": energy,
            "protein": protein,
            "fat": fat,
            "carbohydrate": carbohydrate,
        }
        if producer is not None:
            payload["producer"] = producer
        if portion_weight is not None:
            payload["portionWeight"] = portion_weight
        if fiber is not None:
            payload["fiber"] = fiber
        if sugars is not None:
            payload["sugars"] = sugars
        if sodium is not None:
            payload["sodium"] = sodium
        if saturated_fat is not None:
            payload["saturatedFat"] = saturated_fat
        if salt is not None:
            payload["salt"] = salt
        return write_reply(self.request("POST", "/products", json_data=payload))

    def search_user_food(
        self,
        user_id: str,
        phrase: str,
        day: date | str,
        *,
        page: int = 1,
        limit: int = 50,
    ) -> list[dict[str, Any]]:
        """Search the authenticated user's food catalog entries.

        Each result row uses ``foodId`` as the product identifier.
        Pass it to ``get_product_details(row["foodId"])`` for full details.
        """
        uid = validate_numeric_id(user_id, "user_id")
        validate_positive_int(page, "page")
        validate_positive_int(limit, "limit")
        day_value = as_date(day).isoformat()
        data = self.request(
            "GET",
            f"/search/food/user/{uid}",
            params={
                "date": day_value,
                "phrase": phrase,
                "page": page,
                "limit": limit,
            },
        )
        if isinstance(data, dict):
            data = data.get("items") or data.get("results") or data.get("data")
        return as_dict_list(data)

    def delete_product(self, product_id: int | str) -> dict[str, Any]:
        """Delete a user-created product from the Fitatu catalog."""
        pid = validate_numeric_id(product_id, "product_id")
        return write_reply(self.request("DELETE", f"/products/{pid}"))

    def delete_recipe(self, recipe_id: int | str) -> dict[str, Any]:
        """Delete a recipe the user created.

        Fitatu deletes it softly (checked live): :meth:`get_recipe` still returns it, with
        ``"deleted": true``, and it no longer shows up in the user's searches.
        """
        rid = validate_numeric_id(recipe_id, "recipe_id")
        return write_reply(self.request("DELETE", f"/recipes/{rid}"))

    _PROPOSAL_SUPPORTED_PROPERTIES: frozenset[str] = frozenset({"rawIngredients"})

    def set_product_proposal(
        self,
        product_id: int | str,
        *,
        property_name: str,
        property_value: str,
    ) -> dict[str, Any]:
        """Set a product proposal property.

        The backend only accepts ``propertyName=rawIngredients``. All other
        values return a 400 validation error (confirmed via exhaustive probe
        against 22 candidates). Passing an unsupported name raises immediately
        without making a network call.
        """
        pid = validate_numeric_id(product_id, "product_id")
        if property_name not in self._PROPOSAL_SUPPORTED_PROPERTIES:
            raise FitatuValidationError(
                f"set_product_proposal: unsupported propertyName={property_name!r}. "
                f"Supported: {sorted(self._PROPOSAL_SUPPORTED_PROPERTIES)}"
            )
        data = self.request(
            "POST",
            f"/products/{pid}/proposals",
            json_data={
                "propertyName": property_name,
                "propertyValue": property_value,
            },
        )
        return write_reply(data)

    def set_product_raw_ingredients(
        self,
        product_id: int | str,
        raw_ingredients: str | list[str],
    ) -> dict[str, Any]:
        """Set the rawIngredients proposal text for a product."""
        value = ", ".join(raw_ingredients) if isinstance(raw_ingredients, list) else raw_ingredients
        return self.set_product_proposal(
            product_id,
            property_name="rawIngredients",
            property_value=value,
        )

    @staticmethod
    def nutrition_values_match(
        existing_value: Any,
        expected_value: Any,
        *,
        tolerance: float = 0.001,
    ) -> bool:
        """Return True when two nutrition values match within a relative tolerance."""
        if expected_value in {None, "N/A", ""}:
            return True
        if existing_value in {None, "N/A", ""}:
            return True
        try:
            existing = float(existing_value)
            expected = float(expected_value)
        except (TypeError, ValueError):
            return True
        allowed_delta = abs(float(tolerance)) * max(abs(existing), abs(expected), 1.0)
        return abs(existing - expected) <= allowed_delta

    @classmethod
    def product_nutrition_matches(
        cls,
        product: dict[str, Any],
        expected: dict[str, Any],
        *,
        fields: tuple[str, ...] = ("energy", "protein", "fat", "carbohydrate"),
        tolerance: float = 0.001,
    ) -> bool:
        """Return True when selected product nutrition fields match expected values."""
        return all(
            cls.nutrition_values_match(product.get(field), expected.get(field), tolerance=tolerance)
            for field in fields
        )

    def find_matching_user_product(
        self,
        user_id: str,
        phrase: str,
        day: date | str,
        *,
        nutrition: dict[str, Any],
        brand: str | None = None,
        fields: tuple[str, ...] = ("energy", "protein", "fat", "carbohydrate"),
        tolerance: float = 0.001,
        page: int = 1,
        limit: int = 50,
    ) -> dict[str, Any] | None:
        """Find the first user-food product matching optional brand and nutrition values.

        ``nutrition`` is a required dict of macro field names to expected values::

            client.find_matching_user_product(
                user_id, "banan", date.today(),
                nutrition={"energy": 89, "protein": 1.1, "fat": 0.3, "carbohydrate": 23},
            )

        Returns the first matching product dict, or ``None`` if nothing matches.
        """
        products = self.search_user_food(user_id, phrase, day, page=page, limit=limit)
        for product in products:
            if brand is not None and product.get("brand") != brand:
                continue
            if self.product_nutrition_matches(
                product,
                nutrition,
                fields=fields,
                tolerance=tolerance,
            ):
                return product
        return None

    def cleanup_duplicate_user_products(
        self,
        user_id: str,
        phrase: str,
        day: date | str,
        *,
        brand: str | None = None,
        keep_product_id: int | str | None = None,
        predicate: Callable[[dict[str, Any]], bool] | None = None,
        page: int = 1,
        limit: int = 50,
        dry_run: bool = True,
        max_deletions: int = 10,
    ) -> dict[str, Any]:
        """Delete duplicate user-food products selected by brand or a caller-supplied predicate.

        Deleting a catalog product cannot be undone, so the guards are on by default:

        - Pass ``brand`` or ``predicate`` so unrelated products returned for the search
          phrase are left alone. Only rows of ``type == "PRODUCT"`` are considered.
        - ``dry_run=True`` (the default) only reports ``to_delete_product_ids``; pass
          ``dry_run=False`` to delete.
        - With more than one match, ``keep_product_id`` must name the one to keep.
        - More than ``max_deletions`` deletions raise instead of deleting anything.
        - Auth, rate-limit and maintenance errors stop the run and are raised; other
          per-product failures are collected in ``errors``.

        Raises:
            FitatuValidationError: On a missing filter, an ambiguous or unknown
                ``keep_product_id``, or too many deletions.
        """
        if brand is None and predicate is None:
            raise FitatuValidationError("cleanup requires brand or predicate filter")
        validate_non_negative_int(max_deletions, "max_deletions")

        matched_ids: list[str] = []
        for product in self.search_user_food(user_id, phrase, day, page=page, limit=limit):
            product_id = product.get("foodId") or product.get("id")
            if product.get("type") != "PRODUCT" or product_id is None:
                continue
            if brand is not None and product.get("brand") != brand:
                continue
            if predicate is not None and not predicate(product):
                continue
            matched_ids.append(str(product_id))

        if len(matched_ids) > 1 and keep_product_id is None:
            raise FitatuValidationError(
                f"{len(matched_ids)} products match {phrase!r} ({matched_ids}); "
                "pass keep_product_id to say which one to keep"
            )
        keep_id = str(keep_product_id) if keep_product_id is not None else next(iter(matched_ids), None)
        if matched_ids and keep_id not in matched_ids:
            raise FitatuValidationError(f"keep_product_id {keep_id} is not one of the matches {matched_ids}")
        to_delete = [product_id for product_id in matched_ids if product_id != keep_id]

        deleted_ids: list[str] = []
        errors: list[dict[str, Any]] = []
        if not dry_run and len(to_delete) > max_deletions:
            raise FitatuValidationError(
                f"{len(to_delete)} products would be deleted, more than max_deletions={max_deletions}; "
                "narrow the filter or raise max_deletions"
            )
        for product_id in to_delete if not dry_run else []:
            try:
                self.delete_product(product_id)
            except (FitatuAuthError, FitatuRateLimitError, FitatuMaintenanceError):
                raise  # the rest would fail the same way
            except FitatuApiError as exc:
                errors.append(
                    {
                        "product_id": product_id,
                        "message": str(exc),
                        "status_code": exc.status_code,
                    }
                )
            else:
                deleted_ids.append(product_id)

        return {
            "ok": not errors,
            "dry_run": dry_run,
            "phrase": phrase,
            "matched_count": len(matched_ids),
            "kept_product_id": keep_id,
            "to_delete_product_ids": to_delete,
            "deleted_count": len(deleted_ids),
            "deleted_product_ids": deleted_ids,
            "errors": errors,
        }

    def get_recipes_catalog(self) -> Any:
        """Fetch the recipes catalog root payload.

        The /recipes-catalog endpoint does not accept query parameters — any params
        cause a 400 Bad Request.
        """
        return self.request("GET", "/recipes-catalog")

    def get_recipes_catalog_category(self, category_id: int | str) -> Any:
        """Fetch a single recipes catalog category by id or slug."""
        return self.request("GET", f"/recipes-catalog/category/{quote(str(category_id), safe='')}")

    def search_recipes(
        self,
        phrase: str = "",
        *,
        max_energy: float | None = None,
        min_protein: int | None = None,
        max_carbohydrate: int | None = None,
        max_cooking_time: int | None = None,
        tags: Sequence[str] = (),
        meals: Sequence[str] = (),
        locale: str | None = None,
        limit: int = 10,
        page: int = 1,
    ) -> list[dict[str, Any]]:
        """Search the recipe catalog, as the app's recipe tab does (``GET /search/catalog/recipes``).

        Hits are shaped like :meth:`search_food` rows of ``type`` RECIPE (``foodId``, ``energy``
        and ``measure`` of one portion) plus ``premiumInRecipesCatalog``, ``rating``,
        ``cookingTime`` and ``preparationTime``; some are premium recipes. The filters are per
        portion and whole numbers: ``max_energy`` kcal (rounded down), ``min_protein`` and
        ``max_carbohydrate`` g. ``max_cooking_time`` works in two steps: 10 to 29 keeps recipes of
        up to 10 minutes, 30 or more up to 30, less than 10 nothing. ``tags`` are the catalog's
        ``tag.*`` names (e.g. ``tag.vegan``) and must all apply; ``meals`` (meal keys) any of them.
        Checked live 2026-10-15; ``hasFilters=true`` goes with any filter, as the app sends it.
        """
        validate_positive_int(limit, "limit")
        validate_positive_int(page, "page")
        for value, name in ((min_protein, "min_protein"), (max_carbohydrate, "max_carbohydrate"), (max_cooking_time, "max_cooking_time")):
            if value is not None:
                validate_non_negative_int(value, name)
        params: dict[str, Any] = {"page": page, "limit": limit, "locale": locale or self.auth.app_search_locale, "phrase": phrase}
        filters = {
            "maxEnergy": None if max_energy is None else int(max_energy),  # Fitatu answers 400 to a decimal (checked live 2026-10-15)
            "minProtein": min_protein,
            "maxCarbohydrate": max_carbohydrate,
            "maxCookingTime": max_cooking_time,
            # PHP-style list keys, as the app sends them; a bare tags= or meal= is ignored.
            "tags[]": list(tags),
            "meals[]": list(meals),
        }
        filters = {key: value for key, value in filters.items() if value is not None and value != []}
        params["hasFilters"] = "true" if filters else "false"
        params.update(filters)
        return as_dict_list(self.request("GET", "/search/catalog/recipes", params=params))

    def get_recipe(self, recipe_id: int | str) -> dict[str, Any]:
        """Fetch recipe details by recipe id.

        ``recipe_id`` must be a numeric id, not a catalog slug (e.g. "recipe-of-the-day").
        Passing a slug causes a 500 from the backend.  Use ``get_recipes_catalog()`` to
        browse catalog categories and obtain real numeric recipe ids.
        """
        try:
            rid = validate_numeric_id(recipe_id, "recipe_id")
        except FitatuValidationError as exc:
            raise FitatuValidationError(f"{exc}. Use get_recipes_catalog() to find numeric recipe ids.") from None
        return read_reply(self.request("GET", f"/recipes/{rid}"), f"GET /recipes/{rid}")
