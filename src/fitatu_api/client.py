"""Low-level Fitatu HTTP client."""

from __future__ import annotations

import base64
import json
import logging
import time
import uuid
from collections.abc import Callable
from datetime import date
from pathlib import Path
from typing import Any, cast

import requests

from ._validation import validate_positive_int
from .auth import FitatuAuthContext, FitatuTokenStore
from .constants import (
    DEFAULT_FITATU_API_BASE_URL,
    FITATU_LIFECYCLE_HEALTHY,
    FITATU_LIFECYCLE_REAUTH_FAILED,
    FITATU_LIFECYCLE_REFRESH_ONLY,
    FITATU_LIFECYCLE_RELOGIN_REQUIRED,
    FITATU_LIFECYCLE_TOKEN_ONLY,
    FITATU_MANAGEMENT_REPORT_SCHEMA_VERSION,
)
from .exceptions import FitatuApiError
from .operational_store import FitatuOperationalStore
from .planner import PlannerModule
from .service_modules import (
    ActivitiesModule,
    AuthModule,
    CmsModule,
    DietPlanModule,
    ResourcesModule,
    UserSettingsModule,
    WaterModule,
)

logger = logging.getLogger(__name__)


def _serialize_log_value(value: Any) -> Any:
    """Recursively coerce a value to a JSON-safe type for structured logging."""
    if isinstance(value, dict):
        return {str(key): _serialize_log_value(inner) for key, inner in value.items()}
    if isinstance(value, list):
        return [_serialize_log_value(item) for item in value]
    if isinstance(value, tuple):
        return [_serialize_log_value(item) for item in value]
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    return str(value)


def _log_event(event: str, **fields: Any) -> None:
    """Emit a structured JSON log line at INFO level for a named lifecycle event."""
    payload = {"event": event, **{key: _serialize_log_value(value) for key, value in fields.items()}}
    logger.info(json.dumps(payload, ensure_ascii=True, sort_keys=True))


def _new_correlation_id() -> str:
    """Return a fresh random hex correlation id for request tracing."""
    return uuid.uuid4().hex


def _parse_jwt_payload(token: str) -> dict[str, Any]:
    """Decode the payload section of a JWT without verifying the signature."""
    parts = token.split(".")
    if len(parts) < 2:
        return {}
    padded = parts[1] + "=" * (-len(parts[1]) % 4)
    try:
        return cast(dict[str, Any], json.loads(base64.b64decode(padded).decode("utf-8")))
    except Exception:
        return {}


class FitatuApiClient:
    """Main low-level API client for Fitatu."""

    def __init__(
        self,
        auth: FitatuAuthContext,
        base_url: str = DEFAULT_FITATU_API_BASE_URL,
        timeout_seconds: int = 25,
        retry_max_attempts: int = 3,
        retry_base_delay_seconds: float = 0.5,
        token_store_path: str | Path | None = None,
        operational_store_path: str | Path | None = None,
        persist_tokens: bool = True,
    ) -> None:
        self.auth = auth
        self.base_url = base_url.rstrip("/")
        self.timeout_seconds = timeout_seconds
        self.retry_max_attempts = max(1, int(retry_max_attempts))
        self.retry_base_delay_seconds = max(0.0, float(retry_base_delay_seconds))
        self.persist_tokens = persist_tokens
        self.token_store = FitatuTokenStore(Path(token_store_path)) if token_store_path else None
        self.operational_store = (
            FitatuOperationalStore(Path(operational_store_path))
            if operational_store_path
            else None
        )
        self._load_tokens_from_store()
        self.lifecycle_state = self._derive_lifecycle_state()
        self.auth_api = AuthModule(self)
        self.planner = PlannerModule(self)
        self.user_settings = UserSettingsModule(self)
        self.diet_plan = DietPlanModule(self)
        self.water = WaterModule(self)
        self.activities = ActivitiesModule(self)
        self.resources = ResourcesModule(self)
        self.cms = CmsModule(self)

    @classmethod
    def login(
        cls,
        email: str,
        password: str,
        base_url: str = DEFAULT_FITATU_API_BASE_URL,
        **kwargs: Any,
    ) -> FitatuApiClient:
        """Authenticate with email and password and return a ready-to-use client.

        Calls ``POST /login`` with the supplied credentials, decodes the returned
        JWT to extract the user id, and constructs a fully initialised
        :class:`FitatuApiClient`.

        Args:
            email: Fitatu account e-mail address.
            password: Fitatu account password.
            base_url: API base URL (defaults to the standard Polish endpoint).
            **kwargs: Forwarded verbatim to :meth:`__init__` (e.g. ``timeout_seconds``).

        Raises:
            FitatuApiError: When the server rejects the credentials or returns
                a response without a token field.
        """
        defaults = FitatuAuthContext()
        url = f"{base_url.rstrip('/')}/login"
        headers = {
            "api-key": defaults.api_key,
            "api-secret": defaults.api_secret,
            "Content-Type": "application/json",
        }
        resp = requests.post(
            url,
            json={"_username": email, "_password": password},
            headers=headers,
            timeout=kwargs.get("timeout_seconds", 25),
        )
        if not resp.ok:
            raise FitatuApiError(
                f"Login failed with status {resp.status_code}",
                status_code=resp.status_code,
                body=resp.text,
            )
        data: dict[str, Any] = resp.json()
        token = data.get("token") or data.get("access_token")
        refresh = data.get("refresh_token") or data.get("refreshToken")
        if not token:
            raise FitatuApiError(
                "Login response did not contain a token",
                status_code=resp.status_code,
                body=resp.text,
            )
        payload = _parse_jwt_payload(str(token))
        user_id = (
            payload.get("user_id")
            or payload.get("uid")
            or payload.get("id")
            or payload.get("sub")
        )
        auth = FitatuAuthContext(
            bearer_token=str(token),
            refresh_token=str(refresh) if refresh is not None else None,
            fitatu_user_id=str(user_id) if user_id is not None else None,
        )
        logger.info("login email=%s user_id=%s", email, auth.fitatu_user_id)
        return cls(auth=auth, base_url=base_url, **kwargs)

    def _load_tokens_from_store(self) -> None:
        """Seed bearer and refresh tokens from the on-disk token store into the auth context."""
        if self.token_store is None:
            return
        token_data = self.token_store.load()
        bearer = token_data.get("bearer_token")
        refresh = token_data.get("refresh_token")
        if bearer:
            self.auth.bearer_token = bearer
        if refresh:
            self.auth.refresh_token = refresh

    def _persist_tokens_to_store(self) -> None:
        """Write the current token pair to disk if token persistence is enabled."""
        if not self.persist_tokens or self.token_store is None:
            return
        self.token_store.save(
            bearer_token=self.auth.bearer_token,
            refresh_token=self.auth.refresh_token,
        )

    def _derive_lifecycle_state(self) -> str:
        """Compute the lifecycle state constant from the current token availability."""
        has_bearer = bool(self.auth.bearer_token)
        has_refresh = bool(self.auth.refresh_token)
        if has_bearer and has_refresh:
            return FITATU_LIFECYCLE_HEALTHY
        if has_bearer:
            return FITATU_LIFECYCLE_TOKEN_ONLY
        if has_refresh:
            return FITATU_LIFECYCLE_REFRESH_ONLY
        return FITATU_LIFECYCLE_RELOGIN_REQUIRED

    def close(self) -> None:
        """Release local resources associated with the client instance."""
        if self.operational_store is not None:
            self.operational_store.close()

    def __enter__(self) -> FitatuApiClient:
        """Use the client as a context manager for deterministic cleanup."""
        return self

    def __exit__(self, exc_type: object, exc: object, traceback: object) -> None:
        """Close managed resources when leaving a context block."""
        self.close()

    def __del__(self) -> None:
        self.close()

    def _set_lifecycle_state(self, state: str) -> None:
        """Update the cached lifecycle state constant."""
        self.lifecycle_state = state

    def _record_operational_event(
        self,
        *,
        event: str,
        correlation_id: str,
        payload: dict[str, Any],
    ) -> None:
        """Append a lifecycle event to the operational store, if one is configured."""
        if self.operational_store is None:
            return
        self.operational_store.append_event(
            event=event,
            correlation_id=correlation_id,
            lifecycle_state=self.lifecycle_state,
            payload=payload,
        )

    def _capture_auth_checkpoint(self) -> dict[str, Any]:
        """Snapshot the current token pair and lifecycle state for rollback purposes."""
        return {
            "bearer_token": self.auth.bearer_token,
            "refresh_token": self.auth.refresh_token,
            "lifecycle_state": self.lifecycle_state,
        }

    def _restore_auth_checkpoint(self, checkpoint: dict[str, Any]) -> None:
        """Restore a previously captured auth checkpoint, overwriting the current token pair."""
        self.auth.bearer_token = checkpoint.get("bearer_token")
        self.auth.refresh_token = checkpoint.get("refresh_token")
        self._set_lifecycle_state(self._derive_lifecycle_state())
        if self.auth.bearer_token or self.auth.refresh_token:
            self._persist_tokens_to_store()

    def describe_auth_state(self) -> dict[str, Any]:
        """Return a non-secret snapshot of the current client auth state."""
        state = self.auth.snapshot()
        state.update(
            {
                "base_url": self.base_url,
                "retry_max_attempts": self.retry_max_attempts,
                "persist_tokens": self.persist_tokens,
                "has_token_store": self.token_store is not None,
                "lifecycle_state": self.lifecycle_state,
            }
        )
        return state

    def management_report(self, *, include_tokens: bool = False) -> dict[str, Any]:
        """Build an operational report describing auth, modules, and storage."""
        return {
            "management_report_schema_version": FITATU_MANAGEMENT_REPORT_SCHEMA_VERSION,
            "base_url": self.base_url,
            "timeout_seconds": self.timeout_seconds,
            "retry_max_attempts": self.retry_max_attempts,
            "retry_base_delay_seconds": self.retry_base_delay_seconds,
            "persist_tokens": self.persist_tokens,
            "has_token_store": self.token_store is not None,
            "token_store_path": (
                str(self.token_store.path) if self.token_store is not None else None
            ),
            "lifecycle_state": self.lifecycle_state,
            "has_operational_store": self.operational_store is not None,
            "operational_store_path": (
                str(self.operational_store.path)
                if self.operational_store is not None
                else None
            ),
            "operational_event_count": (
                self.operational_store.count_events()
                if self.operational_store is not None
                else 0
            ),
            "auth": self.auth.snapshot(),
            "session_data": self.auth.to_session_data(include_tokens=include_tokens),
            "modules": [
                "auth_api",
                "planner",
                "user_settings",
                "diet_plan",
                "water",
                "activities",
                "resources",
                "cms",
            ],
        }

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
        self._set_lifecycle_state(FITATU_LIFECYCLE_RELOGIN_REQUIRED)
        if clear_token_store and self.token_store is not None:
            self.token_store.clear()
        state_after = self.describe_auth_state()
        _log_event("fitatu.auth.cleared", correlation_id=correlation_id, state_after=state_after)
        self._record_operational_event(
            event="fitatu.auth.cleared",
            correlation_id=correlation_id,
            payload={"clear_token_store": clear_token_store, "state_after": state_after},
        )

    def reauthenticate(
        self,
        *,
        relogin_callback: Any | None = None,
        clear_token_store: bool = False,
        rollback_on_failure: bool = True,
    ) -> dict[str, Any]:
        """Reauthenticate using refresh first and optional relogin fallback."""
        correlation_id = _new_correlation_id()
        checkpoint = self._capture_auth_checkpoint()
        state_before = self.describe_auth_state()
        _log_event(
            "fitatu.auth.reauthenticate.start",
            correlation_id=correlation_id,
            state_before=state_before,
        )
        self._record_operational_event(
            event="fitatu.auth.reauthenticate.start",
            correlation_id=correlation_id,
            payload={"state_before": state_before},
        )

        refresh_result: dict[str, Any] = {}
        if self.auth.refresh_token:
            refresh_result = self.refresh_access_token(correlation_id=correlation_id)

        if refresh_result.get("status") == "ok":
            self._set_lifecycle_state(self._derive_lifecycle_state())
            refresh_ok_result = {
                "status": "ok",
                "mode": "refresh",
                "refresh": refresh_result,
                "state": self.describe_auth_state(),
            }
            _log_event(
                "fitatu.auth.reauthenticate.ok",
                correlation_id=correlation_id,
                **refresh_ok_result,
            )
            self._record_operational_event(
                event="fitatu.auth.reauthenticate.ok",
                correlation_id=correlation_id,
                payload=refresh_ok_result,
            )
            return refresh_ok_result

        relogin_result: dict[str, Any] | None = None
        relogin_applied = False
        if relogin_callback is not None:
            relogin_result = relogin_callback(self.auth)
            if isinstance(relogin_result, dict):
                refreshed_bearer = relogin_result.get("bearer_token")
                refreshed_refresh = relogin_result.get("refresh_token")
                if isinstance(refreshed_bearer, str) and refreshed_bearer.strip():
                    self.auth.bearer_token = refreshed_bearer.strip()
                    relogin_applied = True
                if isinstance(refreshed_refresh, str) and refreshed_refresh.strip():
                    self.auth.refresh_token = refreshed_refresh.strip()
                self._persist_tokens_to_store()

        if relogin_applied and self.auth.bearer_token:
            self._set_lifecycle_state(self._derive_lifecycle_state())
            relogin_ok_result: dict[str, Any] = {
                "status": "ok",
                "mode": "relogin",
                "refresh": refresh_result,
                "relogin": relogin_result,
                "state": self.describe_auth_state(),
            }
            _log_event(
                "fitatu.auth.reauthenticate.ok",
                correlation_id=correlation_id,
                **relogin_ok_result,
            )
            self._record_operational_event(
                event="fitatu.auth.reauthenticate.ok",
                correlation_id=correlation_id,
                payload=relogin_ok_result,
            )
            return relogin_ok_result

        rollback_applied = False
        if rollback_on_failure:
            self._restore_auth_checkpoint(checkpoint)
            rollback_applied = True
        elif clear_token_store:
            self.clear_auth(clear_token_store=True)

        if not rollback_applied:
            self._set_lifecycle_state(FITATU_LIFECYCLE_REAUTH_FAILED)
        failed_result: dict[str, Any] = {
            "status": "error",
            "mode": "failed",
            "refresh": refresh_result,
            "relogin": relogin_result,
            "rollback_applied": rollback_applied,
            "state": self.describe_auth_state(),
        }
        _log_event(
            "fitatu.auth.reauthenticate.failed",
            correlation_id=correlation_id,
            **failed_result,
        )
        self._record_operational_event(
            event="fitatu.auth.reauthenticate.failed",
            correlation_id=correlation_id,
            payload=failed_result,
        )
        return failed_result

    @staticmethod
    def _is_retryable_method(method: str) -> bool:
        """Return True for idempotent HTTP methods that are safe to retry automatically."""
        return method.upper() in {"GET", "HEAD", "OPTIONS"}

    def _backoff_delay_seconds(self, attempt: int, retry_after_header: str | None = None) -> float:
        """Compute the delay before the next retry, honouring a Retry-After header if present."""
        if retry_after_header:
            try:
                parsed = float(retry_after_header)
            except ValueError:
                parsed = None
            if parsed is not None and parsed >= 0:
                return parsed
        return self.retry_base_delay_seconds * (2 ** max(0, attempt - 1))

    def _headers(self, *, include_auth: bool) -> dict[str, str]:
        """Build the full request header dict, optionally including the Bearer authorization."""
        headers = {
            "accept": "application/json, text/plain, */*",
            "content-type": "application/json;charset=UTF-8",
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
        if include_auth and self.auth.bearer_token:
            headers["authorization"] = f"Bearer {self.auth.bearer_token}"
        return headers

    def _url(self, path: str) -> str:
        """Resolve a relative path or absolute URL against the configured base_url."""
        if path.startswith("http://") or path.startswith("https://"):
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
    ) -> Any:
        """Perform a Fitatu HTTP request with retries and optional auto-refresh."""
        correlation_id = _new_correlation_id()
        method_upper = method.upper()
        retryable_method = self._is_retryable_method(method_upper)
        _log_event(
            "fitatu.request.start",
            correlation_id=correlation_id,
            method=method_upper,
            path=path,
            include_auth=include_auth,
            allow_refresh_on_401=allow_refresh_on_401,
            params=params,
            has_json=json_data is not None,
        )
        self._record_operational_event(
            event="fitatu.request.start",
            correlation_id=correlation_id,
            payload={
                "method": method_upper,
                "path": path,
                "include_auth": include_auth,
                "allow_refresh_on_401": allow_refresh_on_401,
            },
        )

        for attempt in range(1, self.retry_max_attempts + 1):
            try:
                response = requests.request(
                    method=method_upper,
                    url=self._url(path),
                    headers=self._headers(include_auth=include_auth),
                    params=params,
                    json=json_data,
                    timeout=self.timeout_seconds,
                )
            except requests.RequestException as exc:
                if retryable_method and attempt < self.retry_max_attempts:
                    time.sleep(self._backoff_delay_seconds(attempt))
                    continue
                raise FitatuApiError(
                    message=f"Fitatu API network error: {method_upper} {path}: {exc}",
                    body=str(exc),
                ) from exc

            if response.status_code == 401 and include_auth and allow_refresh_on_401 and self.auth.refresh_token:
                refresh = self.refresh_access_token(correlation_id=correlation_id)
                if refresh.get("status") == "ok":
                    try:
                        response = requests.request(
                            method=method_upper,
                            url=self._url(path),
                            headers=self._headers(include_auth=True),
                            params=params,
                            json=json_data,
                            timeout=self.timeout_seconds,
                        )
                    except requests.RequestException as exc:
                        raise FitatuApiError(
                            message=f"Fitatu API network error after refresh: {method_upper} {path}: {exc}",
                            body=str(exc),
                        ) from exc

            if response.status_code in {429, 500, 502, 503, 504} and retryable_method and attempt < self.retry_max_attempts:
                time.sleep(self._backoff_delay_seconds(attempt, retry_after_header=response.headers.get("retry-after")))
                continue

            if response.status_code >= 400:
                error = FitatuApiError(
                    message=f"Fitatu API request failed: {method_upper} {path}",
                    status_code=response.status_code,
                    body=response.text,
                )
                _log_event(
                    "fitatu.request.error",
                    correlation_id=correlation_id,
                    method=method_upper,
                    path=path,
                    status_code=response.status_code,
                    body=response.text,
                )
                if response.status_code == 401:
                    self._set_lifecycle_state(FITATU_LIFECYCLE_REAUTH_FAILED)
                self._record_operational_event(
                    event="fitatu.request.error",
                    correlation_id=correlation_id,
                    payload={
                        "method": method_upper,
                        "path": path,
                        "status_code": response.status_code,
                    },
                )
                raise error

            if not response.text:
                _log_event(
                    "fitatu.request.ok",
                    correlation_id=correlation_id,
                    method=method_upper,
                    path=path,
                    status_code=response.status_code,
                    result_type="None",
                )
                self._record_operational_event(
                    event="fitatu.request.ok",
                    correlation_id=correlation_id,
                    payload={
                        "method": method_upper,
                        "path": path,
                        "status_code": response.status_code,
                        "result_type": "None",
                    },
                )
                return None

            try:
                result = response.json()
            except ValueError:
                result = response.text
                result_type = "str"
            else:
                result_type = type(result).__name__

            _log_event(
                "fitatu.request.ok",
                correlation_id=correlation_id,
                method=method_upper,
                path=path,
                status_code=response.status_code,
                result_type=result_type,
            )
            self._record_operational_event(
                event="fitatu.request.ok",
                correlation_id=correlation_id,
                payload={
                    "method": method_upper,
                    "path": path,
                    "status_code": response.status_code,
                    "result_type": result_type,
                },
            )
            return result

        raise FitatuApiError(message=f"Fitatu API request failed after retries: {method_upper} {path}")

    def request_first_success(
        self,
        method: str,
        paths: list[str],
        *,
        params: dict[str, Any] | None = None,
        json_data: Any | None = None,
        include_auth: bool = True,
        allow_refresh_on_401: bool = True,
    ) -> Any:
        """Try multiple route candidates and return the first non-404 success."""
        last_error: FitatuApiError | None = None
        for path in paths:
            try:
                return self.request(
                    method,
                    path,
                    params=params,
                    json_data=json_data,
                    include_auth=include_auth,
                    allow_refresh_on_401=allow_refresh_on_401,
                )
            except FitatuApiError as exc:
                last_error = exc
                if exc.status_code != 404:
                    raise
        if last_error is not None:
            raise last_error
        raise FitatuApiError(f"No paths provided for {method}")

    def refresh_access_token(self, *, correlation_id: str | None = None) -> dict[str, Any]:
        """Refresh the bearer token using the current refresh token."""
        request_correlation_id = correlation_id or _new_correlation_id()
        if not self.auth.refresh_token:
            missing_result: dict[str, Any] = {
                "status": "error",
                "message": "missing refresh_token",
            }
            _log_event(
                "fitatu.auth.refresh.missing",
                correlation_id=request_correlation_id,
                result=missing_result,
            )
            self._record_operational_event(
                event="fitatu.auth.refresh.missing",
                correlation_id=request_correlation_id,
                payload=missing_result,
            )
            return missing_result

        _log_event(
            "fitatu.auth.refresh.start",
            correlation_id=request_correlation_id,
            state_before=self.describe_auth_state(),
        )
        self._record_operational_event(
            event="fitatu.auth.refresh.start",
            correlation_id=request_correlation_id,
            payload={"state_before": self.describe_auth_state()},
        )

        refresh_token_value = self.auth.refresh_token
        refresh_payloads: list[dict[str, str]] = [
            {"refresh_token": refresh_token_value},
            {"refreshToken": refresh_token_value},
            {"token": refresh_token_value},
        ]
        data: Any | None = None
        last_refresh_error: FitatuApiError | None = None
        for payload in refresh_payloads:
            try:
                candidate_data = self.request(
                    "POST",
                    "/token/refresh",
                    json_data=payload,
                    include_auth=False,
                    allow_refresh_on_401=False,
                )
            except FitatuApiError as exc:
                last_refresh_error = exc
                continue

            if not isinstance(candidate_data, dict):
                data = candidate_data
                continue

            candidate_dict = cast(dict[str, Any], candidate_data)
            candidate_token = candidate_dict.get("token") or candidate_dict.get("access_token")
            if candidate_token:
                data = candidate_data
                break
            data = candidate_data

        if data is None and last_refresh_error is not None:
            refresh_error_result: dict[str, Any] = {
                "status": "error",
                "message": str(last_refresh_error),
                "status_code": last_refresh_error.status_code,
                "body": last_refresh_error.body,
            }
            self._set_lifecycle_state(FITATU_LIFECYCLE_REAUTH_FAILED)
            _log_event(
                "fitatu.auth.refresh.error",
                correlation_id=request_correlation_id,
                result=refresh_error_result,
            )
            self._record_operational_event(
                event="fitatu.auth.refresh.error",
                correlation_id=request_correlation_id,
                payload=refresh_error_result,
            )
            return refresh_error_result

        token: Any | None = None
        refresh_token: Any | None = None
        if isinstance(data, dict):
            data_dict = cast(dict[str, Any], data)
            token = data_dict.get("token") or data_dict.get("access_token")
            refresh_token = data_dict.get("refresh_token") or data_dict.get("refreshToken")

        if not token:
            missing_token_result: dict[str, Any] = {
                "status": "error",
                "message": "token not found in refresh response",
                "data": data,
            }
            self._set_lifecycle_state(FITATU_LIFECYCLE_REAUTH_FAILED)
            _log_event(
                "fitatu.auth.refresh.missing_token",
                correlation_id=request_correlation_id,
                result=missing_token_result,
            )
            self._record_operational_event(
                event="fitatu.auth.refresh.missing_token",
                correlation_id=request_correlation_id,
                payload=missing_token_result,
            )
            return missing_token_result

        self.auth.bearer_token = token
        if refresh_token:
            self.auth.refresh_token = str(refresh_token)
        self._persist_tokens_to_store()
        self._set_lifecycle_state(self._derive_lifecycle_state())
        result = {"status": "ok", "token": token, "refresh_token": self.auth.refresh_token, "data": data}
        _log_event(
            "fitatu.auth.refresh.ok",
            correlation_id=request_correlation_id,
            state_after=self.describe_auth_state(),
        )
        self._record_operational_event(
            event="fitatu.auth.refresh.ok",
            correlation_id=request_correlation_id,
            payload={"state_after": self.describe_auth_state()},
        )
        return result

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
                "accessType": ["FREE", "PREMIUM"],
            },
        )
        if isinstance(data, list):
            return [cast(dict[str, Any], x) for x in data if isinstance(x, dict)]
        if isinstance(data, dict):
            data_dict = cast(dict[str, Any], data)
            nested = data_dict.get("data")
            nested_dict = cast(dict[str, Any], nested) if isinstance(nested, dict) else {}
            items_raw: Any = data_dict.get("items") or nested_dict.get("items") or data_dict.get("results") or []
            if isinstance(items_raw, list):
                return [cast(dict[str, Any], x) for x in items_raw if isinstance(x, dict)]
        return []

    def get_product_details(self, product_id: int | str) -> dict[str, Any]:
        """Fetch full product details including available measures.

        The search endpoint returns both PRODUCT and RECIPE items in the same result
        list.  RECIPE items (``type="RECIPE"`` in search results) have their details
        under ``/recipes/{id}``, not ``/products/{id}``.  This method tries all product
        route variants first and falls back to the recipe route on 404, so callers
        do not need to distinguish item type before calling.

        Measure field names in the returned dict differ by sub-key:

        - ``result["measures"]`` — list of dicts with ``id`` and ``name``
        - ``result["simpleMeasures"]`` — list of dicts with ``id``, ``name``,
          ``weight``, ``capacity``, etc.
        - ``result["initialMeasure"]`` — single dict with ``key``, ``weight``,
          ``unitKey``

        Note: ``measures[n]["id"]`` is the correct field for ``measureId`` when
        calling planner add/update helpers.  Do not assume ``measureId`` — that
        field name appears only in ``simpleMeasures`` and search-result rows.
        """
        data = self.request_first_success(
            "GET",
            [
                f"/products/{product_id}",
                f"/v2/products/{product_id}",
                f"/v3/products/{product_id}",
                f"/recipes/{product_id}",
            ],
        )
        return cast(dict[str, Any], data) if isinstance(data, dict) else {"raw": data}

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
        normalized_items = self.normalize_recipe_items(items)
        if not normalized_items:
            raise FitatuApiError("No valid recipe items to send")
        data = self.request(
            "POST",
            "/recipes",
            json_data={
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
            },
        )
        return cast(dict[str, Any], data) if isinstance(data, dict) else {"raw": data}

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
        data = self.request("POST", "/products", json_data=payload)
        return cast(dict[str, Any], data) if isinstance(data, dict) else {"raw": data}

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
        validate_positive_int(page, "page")
        validate_positive_int(limit, "limit")
        day_value = day.isoformat() if isinstance(day, date) else day
        data = self.request(
            "GET",
            f"/search/food/user/{user_id}",
            params={
                "date": day_value,
                "phrase": phrase,
                "page": page,
                "limit": limit,
            },
        )
        if isinstance(data, list):
            return [cast(dict[str, Any], item) for item in data if isinstance(item, dict)]
        if isinstance(data, dict):
            data_dict = cast(dict[str, Any], data)
            items = data_dict.get("items") or data_dict.get("results") or data_dict.get("data") or []
            if isinstance(items, list):
                return [cast(dict[str, Any], item) for item in items if isinstance(item, dict)]
        return []

    def delete_product(self, product_id: int | str) -> dict[str, Any]:
        """Delete a user-created product from the Fitatu catalog."""
        data = self.request("DELETE", f"/products/{product_id}")
        return cast(dict[str, Any], data) if isinstance(data, dict) else {"raw": data}

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
        if property_name not in self._PROPOSAL_SUPPORTED_PROPERTIES:
            raise FitatuApiError(
                f"set_product_proposal: unsupported propertyName={property_name!r}. "
                f"Supported: {sorted(self._PROPOSAL_SUPPORTED_PROPERTIES)}"
            )
        data = self.request(
            "POST",
            f"/products/{product_id}/proposals",
            json_data={
                "propertyName": property_name,
                "propertyValue": property_value,
            },
        )
        return cast(dict[str, Any], data) if isinstance(data, dict) else {"raw": data}

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
    ) -> dict[str, Any]:
        """Delete duplicate user-food products selected by brand or a caller-supplied predicate.

        This is intentionally opt-in: pass ``brand`` or ``predicate`` so the helper does
        not delete unrelated user products returned for the search phrase.
        """
        if brand is None and predicate is None:
            raise FitatuApiError("cleanup requires brand or predicate filter")

        products = self.search_user_food(user_id, phrase, day, page=page, limit=limit)
        matches: list[dict[str, Any]] = []
        for product in products:
            if brand is not None and product.get("brand") != brand:
                continue
            if predicate is not None and not predicate(product):
                continue
            matches.append(product)

        if not matches:
            return {
                "ok": True,
                "phrase": phrase,
                "matchedCount": 0,
                "deletedCount": 0,
                "keptProductId": None,
                "deletedProductIds": [],
                "errors": [],
            }

        keep_id = str(keep_product_id) if keep_product_id is not None else str(
            matches[0].get("foodId") or matches[0].get("id")
        )
        deleted_ids: list[str] = []
        errors: list[dict[str, Any]] = []
        for product in matches:
            product_id = product.get("foodId") or product.get("id")
            if product_id is None or str(product_id) == keep_id:
                continue
            try:
                self.delete_product(product_id)
                deleted_ids.append(str(product_id))
            except FitatuApiError as exc:
                errors.append(
                    {
                        "productId": str(product_id),
                        "message": str(exc),
                        "status_code": exc.status_code,
                        "body": exc.body,
                    }
                )

        return {
            "ok": not errors,
            "phrase": phrase,
            "matchedCount": len(matches),
            "deletedCount": len(deleted_ids),
            "keptProductId": keep_id,
            "deletedProductIds": deleted_ids,
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
        return self.request("GET", f"/recipes-catalog/category/{category_id}")

    def get_recipe(self, recipe_id: int | str) -> dict[str, Any]:
        """Fetch recipe details by recipe id.

        ``recipe_id`` must be a numeric id, not a catalog slug (e.g. "recipe-of-the-day").
        Passing a slug causes a 500 from the backend.  Use ``get_recipes_catalog()`` to
        browse catalog categories and obtain real numeric recipe ids.
        """
        rid = str(recipe_id).strip()
        if rid and not rid.lstrip("-").isdigit():
            raise FitatuApiError(
                f"get_recipe requires a numeric recipe id, got slug-like value: {rid!r}. "
                "Use get_recipes_catalog() to find numeric recipe ids."
            )
        data = self.request("GET", f"/recipes/{recipe_id}")
        return cast(dict[str, Any], data) if isinstance(data, dict) else {"raw": data}

    def get_user(self, user_id: str) -> dict[str, Any]:
        """Fetch a user profile via the convenience client surface."""
        return self.user_settings.get_profile(user_id)

    def get_user_settings_for_day(self, user_id: str, day: date) -> dict[str, Any]:
        """Fetch user settings resolved for a specific day."""
        return self.user_settings.get_for_day(user_id, day)

    def get_user_settings(self, user_id: str, day: date | None = None) -> dict[str, Any]:
        """Fetch current user settings, optionally scoped by day."""
        return self.user_settings.get(user_id, day=day)

    def get_user_settings_new(self, user_id: str) -> dict[str, Any]:
        """Fetch the `settings-new` payload via the convenience surface."""
        return self.user_settings.get_new(user_id)

    def get_diet_plan_settings(self, user_id: str) -> dict[str, Any]:
        """Fetch diet plan settings for a user."""
        return self.diet_plan.get_settings(user_id)

    def get_day_plan(self, user_id: str, day: date) -> dict[str, Any]:
        """Fetch a planner day snapshot via the convenience surface."""
        return self.planner.get_day(user_id, day)

    def get_food_tags_recipe(self) -> Any:
        """Fetch recipe food tags from the resources module."""
        return self.resources.get_food_tags_recipe()

    def get_water(self, user_id: str, day: date) -> Any:
        """Fetch water tracking data for a given day."""
        return self.water.get_day(user_id, day)

    def get_activities_catalog(self) -> Any:
        """Fetch the activities catalog."""
        return self.activities.get_catalog()

    def probe_known_endpoints(self, user_id: str, day: date) -> list[dict[str, Any]]:
        """Run a shallow health probe against the best-known stable endpoints."""
        checks: list[tuple[str, str]] = [
            ("GET", f"/users/{user_id}"),
            ("GET", f"/users/{user_id}/settings/{day.isoformat()}"),
            ("GET", f"/users/{user_id}/settings-new"),
            ("GET", f"/diet-plan/{user_id}/settings"),
            ("GET", f"/diet-and-activity-plan/{user_id}/day/{day.isoformat()}"),
            ("GET", "/resources/food-tags/recipe"),
            ("GET", "/activities/"),
            ("GET", f"/water/{user_id}/{day.isoformat()}"),
        ]
        out: list[dict[str, Any]] = []
        for method, path in checks:
            try:
                self.request(method, path)
                out.append({"method": method, "path": path, "ok": True, "status": 200})
            except FitatuApiError as exc:
                out.append({"method": method, "path": path, "ok": False, "status": exc.status_code, "error": str(exc)})
        return out
