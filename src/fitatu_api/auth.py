"""Authentication context and token persistence helpers."""

from __future__ import annotations

import base64
import json
import logging
import os
import tempfile
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, cast

from .constants import (
    DEFAULT_SESSION_PATH,
    FITATU_SESSION_CONTEXT_SCHEMA_VERSION,
    WEB_CLIENT_PROFILE,
)
from .exceptions import FitatuValidationError

logger = logging.getLogger(__name__)


def resolve_session_path(path: str | Path | None = None) -> Path:
    """The session file to use: ``path``, else ``$FITATU_SESSION`` (or its alias
    ``$FITATU_SESSION_PATH``), else ``~/.fitatu/session.json``; ``~`` and variables such as
    ``${HOME}`` are expanded (Claude Desktop passes an extension's ``${HOME}`` default as it is)."""
    raw = path or os.environ.get("FITATU_SESSION") or os.environ.get("FITATU_SESSION_PATH")
    return Path(os.path.expandvars(raw or DEFAULT_SESSION_PATH)).expanduser()


def client_profile() -> dict[str, str]:
    """``WEB_CLIENT_PROFILE`` with the keys that ``$FITATU_CLIENT_PROFILE`` (a JSON object) sets.

    For when Fitatu changes the web app's version or key before a release of this library does,
    e.g. ``FITATU_CLIENT_PROFILE='{"app_version": "4.14.0"}'``.
    """
    raw = os.environ.get("FITATU_CLIENT_PROFILE")
    if not raw:
        return WEB_CLIENT_PROFILE
    try:
        override = json.loads(raw)
    except ValueError:
        override = None
    if not (
        isinstance(override, dict)
        and override.keys() <= WEB_CLIENT_PROFILE.keys()
        and all(isinstance(value, str) and value and value == value.strip() for value in override.values())
    ):
        # The value is not quoted: it may hold the API secret.
        raise FitatuValidationError(
            f"FITATU_CLIENT_PROFILE must be a JSON object with text values for some of: {', '.join(WEB_CLIENT_PROFILE)}"
        )
    return {**WEB_CLIENT_PROFILE, **override}


def _profile(key: str, **options: Any) -> Any:
    """A dataclass field whose default is ``client_profile()[key]``, read when the context is made."""
    return field(default_factory=lambda: client_profile()[key], **options)


def jwt_payload(token: str | None) -> dict[str, Any]:
    """Decode a JWT's payload (base64url) without verifying it; ``{}`` when it is not a JWT."""
    parts = token.split(".") if isinstance(token, str) else []
    if len(parts) < 2:
        return {}
    segment = parts[1]
    try:
        payload = json.loads(base64.urlsafe_b64decode(segment + "=" * (-len(segment) % 4)))
    except ValueError:
        return {}
    return cast(dict[str, Any], payload) if isinstance(payload, dict) else {}


def jwt_user_id(token: str | None) -> str | None:
    """Return the Fitatu user id carried in a bearer token, if there is one."""
    payload = jwt_payload(token)
    user_id = payload.get("user_id") or payload.get("uid") or payload.get("id") or payload.get("sub")
    return str(user_id) if user_id is not None else None


@dataclass
class FitatuAuthContext:
    """Runtime auth/session context used by :class:`FitatuApiClient`.

    The client identity fields (``api_key`` ... ``user_agent``) default to
    ``WEB_CLIENT_PROFILE``, with what ``$FITATU_CLIENT_PROFILE`` overrides (see
    :func:`client_profile`); the locales and timezone describe the user.
    """

    # repr=False keeps secrets out of tracebacks, logs and agent traces that print the context.
    bearer_token: str | None = field(default=None, repr=False)
    refresh_token: str | None = field(default=None, repr=False)
    api_key: str = _profile("api_key")
    api_secret: str = _profile("api_secret", repr=False)
    app_uuid: str = _profile("app_uuid")
    api_cluster: str = _profile("api_cluster")
    app_locale: str = "pl_PL"
    app_search_locale: str = "pl_PL"
    app_storage_locale: str = "pl_PL"
    app_timezone: str = "Europe/Warsaw"
    app_os: str = _profile("app_os")
    app_version: str = _profile("app_version")
    user_agent: str = _profile("user_agent")
    fitatu_user_id: str | None = None
    # The account's API host; the client fills it in (see FitatuApiClient(base_url=...)).
    base_url: str | None = None

    @staticmethod
    def _extract_local_storage(session_data: dict[str, Any]) -> dict[str, str]:
        """Flatten Playwright storage-state localStorage into a plain key→value dict."""
        out: dict[str, str] = {}
        origins_raw = session_data.get("origins")
        if not isinstance(origins_raw, list):
            return out

        origins: list[dict[str, Any]] = []
        for maybe_origin in origins_raw:
            if isinstance(maybe_origin, dict):
                origins.append(cast(dict[str, Any], maybe_origin))

        for origin in origins:
            local_storage_raw = origin.get("localStorage")
            if not isinstance(local_storage_raw, list):
                continue
            for maybe_row in local_storage_raw:
                if not isinstance(maybe_row, dict):
                    continue
                row = cast(dict[str, Any], maybe_row)
                name = row.get("name")
                value = row.get("value")
                if isinstance(name, str) and isinstance(value, str):
                    out[name] = value
        return out

    @classmethod
    def from_session_data(cls, session_data: dict[str, Any]) -> FitatuAuthContext:
        """Build auth context from a session payload.

        ``session_data`` is the library's session (``bearer_token``, ``refresh_token``,
        ``fitatu_user_id``, ...), a Playwright storage state, or the web app's
        ``localStorage.user`` object itself (``token``, ``refresh_token``, ``id``, ...).
        Only the tokens, user id, locales, timezone and host are taken.

        Client identity keys (``api_key`` ... ``user_agent``) that older session files
        still carry are ignored, so ``WEB_CLIENT_PROFILE`` applies to every session.
        """
        storage = cls._extract_local_storage(session_data)

        # The web app's "user" object: from a storage state's localStorage, else the payload itself.
        user_payload: dict[str, Any] = session_data
        raw_user = storage.get("user")
        if raw_user:
            try:
                parsed = json.loads(raw_user)
            except Exception:
                logger.debug("Failed to parse 'user' key from local storage")
                parsed = {}
            if isinstance(parsed, dict):
                user_payload = cast(dict[str, Any], parsed)

        bearer_token = (
            session_data.get("bearerToken")
            or session_data.get("bearer_token")
            or storage.get("token")
            or user_payload.get("token")
        )
        user_id = session_data.get("fitatu_user_id") or user_payload.get("id") or jwt_user_id(bearer_token)
        values: dict[str, Any] = {
            "bearer_token": bearer_token,
            "refresh_token": (
                session_data.get("refreshToken")
                or session_data.get("refresh_token")
                or storage.get("refresh_token")
                or user_payload.get("refresh_token")
            ),
            "app_locale": session_data.get("app_locale") or user_payload.get("locale"),
            "app_search_locale": (
                session_data.get("app_searchlocale")
                or session_data.get("app_search_locale")
                or user_payload.get("searchLocale")
            ),
            "app_storage_locale": (
                session_data.get("app_storagelocale")
                or session_data.get("app_storage_locale")
                or user_payload.get("storageLocale")
            ),
            "app_timezone": session_data.get("app_timezone") or user_payload.get("timezone"),
            "fitatu_user_id": str(user_id) if user_id is not None else None,
            "base_url": session_data.get("base_url"),
        }
        # A token pasted from the browser often ends in a newline, which no header may carry.
        values = {key: value.strip() if isinstance(value, str) else value for key, value in values.items()}
        return cls(**{key: value for key, value in values.items() if value})

    @property
    def bearer_expires_at(self) -> datetime | None:
        """When the bearer token expires (its JWT ``exp``), or None when unknown."""
        exp = jwt_payload(self.bearer_token).get("exp")
        if isinstance(exp, bool) or not isinstance(exp, int | float):
            return None
        try:
            return datetime.fromtimestamp(exp, UTC)
        except (ValueError, OverflowError, OSError):  # e.g. exp in milliseconds, NaN, Infinity
            return None

    def snapshot(self) -> dict[str, Any]:
        """Return a safe, non-secret snapshot of auth state."""
        expires_at = self.bearer_expires_at
        return {
            "has_bearer_token": bool(self.bearer_token),
            "bearer_expires_at": expires_at.isoformat() if expires_at else None,
            "has_refresh_token": bool(self.refresh_token),
            "fitatu_user_id": self.fitatu_user_id,
            "api_cluster": self.api_cluster,
            "app_locale": self.app_locale,
            "app_search_locale": self.app_search_locale,
            "app_storage_locale": self.app_storage_locale,
            "app_timezone": self.app_timezone,
            "app_os": self.app_os,
            "app_version": self.app_version,
            "user_agent": self.user_agent,
        }

    def to_session_data(self, *, include_tokens: bool = False) -> dict[str, Any]:
        """Export session data in a reusable library format.

        The client identity (``WEB_CLIENT_PROFILE``) is left out on purpose: it belongs
        to the library version, not to the session.
        """
        data: dict[str, Any] = {
            "session_context_schema_version": FITATU_SESSION_CONTEXT_SCHEMA_VERSION,
            "base_url": self.base_url,
            "app_locale": self.app_locale,
            "app_searchlocale": self.app_search_locale,
            "app_storagelocale": self.app_storage_locale,
            "app_timezone": self.app_timezone,
        }
        if self.base_url is None:
            del data["base_url"]
        if self.fitatu_user_id is not None:
            data["fitatu_user_id"] = self.fitatu_user_id
        if include_tokens:
            if self.bearer_token is not None:
                data["bearer_token"] = self.bearer_token
            if self.refresh_token is not None:
                data["refresh_token"] = self.refresh_token
        return data


def write_private_json(path: Path, data: dict[str, Any]) -> None:
    """Write ``data`` as JSON readable only by the owner (0600); it holds live tokens.

    The JSON goes to a temporary file in the same directory that then replaces ``path``,
    so a crash or a concurrent reader never sees half a session.
    """
    path = Path(path).expanduser()
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.", suffix=".tmp")  # created 0600
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(json.dumps(data, indent=2, ensure_ascii=True))
        os.replace(tmp, path)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise


@dataclass
class FitatuTokenStore:
    """JSON file holding the token pair; usually the session file itself.

    Saving merges into the existing JSON, so a session file used as the store keeps its
    user id, locales and host when tokens are refreshed.
    """

    path: Path

    def __post_init__(self) -> None:
        # Read, merge and clear the same file that write_private_json writes.
        self.path = Path(self.path).expanduser()

    def _read(self) -> dict[str, Any]:
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return {}
        except (OSError, ValueError):
            logger.warning("Failed to load token store from %s", self.path)
            return {}
        return cast(dict[str, Any], raw) if isinstance(raw, dict) else {}

    def load(self) -> dict[str, str]:
        """Load the persisted bearer and refresh tokens and the user id, when present."""
        raw = self._read()
        return {
            key: str(raw[key]).strip()
            for key in ("bearer_token", "refresh_token", "fitatu_user_id")
            if isinstance(raw.get(key), str | int) and str(raw[key]).strip()
        }

    def save(self, *, bearer_token: str | None, refresh_token: str | None, **fields: Any) -> None:
        """Merge the token pair (and any other session ``fields``) into the file."""
        data = self._read()
        data.update(fields)
        data.update(
            bearer_token=bearer_token or "",
            refresh_token=refresh_token or "",
            updated_at=datetime.now(UTC).isoformat(),
        )
        write_private_json(self.path, data)

    def clear(self) -> None:
        """Remove the token file when it exists."""
        try:
            self.path.unlink()
        except FileNotFoundError:
            return
