"""Authentication context and token persistence helpers."""

from __future__ import annotations

import json
import logging
import os
import re
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, cast

from .constants import FITATU_SESSION_CONTEXT_SCHEMA_VERSION

logger = logging.getLogger(__name__)


@dataclass
class FitatuAuthContext:
    """Runtime auth/session context used by :class:`FitatuApiClient`."""

    bearer_token: str | None = None
    refresh_token: str | None = None
    api_key: str = os.environ.get("FITATU_API_KEY", "FITATU-MOBILE-APP")
    api_secret: str = os.environ.get("FITATU_API_SECRET", "PYRXtfs88UDJMuCCrNpLV")
    app_uuid: str = "64c2d1b0-c8ad-11e8-8956-0242ac120008"
    api_cluster: str = "pl-pl0"
    app_locale: str = "pl_PL"
    app_search_locale: str = "pl_PL"
    app_storage_locale: str = "pl_PL"
    app_timezone: str = "Europe/Warsaw"
    app_os: str = "WEB"
    app_version: str = "4.13.1"
    user_agent: str = "Mozilla/5.0"
    fitatu_user_id: str | None = None

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
        """Build auth context from a storage-state-like session payload."""
        storage = cls._extract_local_storage(session_data)

        user_payload: dict[str, Any] = {}
        raw_user = storage.get("user")
        if raw_user:
            try:
                parsed = json.loads(raw_user)
            except Exception:
                logger.debug("Failed to parse 'user' key from local storage")
                parsed = {}
            if isinstance(parsed, dict):
                user_payload = cast(dict[str, Any], parsed)

        api_cluster = session_data.get("api_cluster")
        if not api_cluster and isinstance(user_payload.get("searchUrls"), list):
            urls = [u for u in user_payload.get("searchUrls", []) if isinstance(u, str)]
            match = next((re.search(r"https://([a-z]{2}-[a-z]{2}\d+)\.", u) for u in urls), None)
            if match and match.group(1):
                api_cluster = match.group(1)

        return cls(
            bearer_token=(
                session_data.get("bearerToken")
                or session_data.get("bearer_token")
                or storage.get("token")
                or user_payload.get("token")
            ),
            refresh_token=(
                session_data.get("refreshToken")
                or session_data.get("refresh_token")
                or storage.get("refresh_token")
                or user_payload.get("refresh_token")
            ),
            api_key=(
                session_data.get("api_key")
                or os.environ.get("FITATU_API_KEY")
                or "FITATU-MOBILE-APP"
            ),
            api_secret=(
                session_data.get("api_secret")
                or os.environ.get("FITATU_API_SECRET")
                or "PYRXtfs88UDJMuCCrNpLV"
            ),
            app_uuid=session_data.get("app_uuid") or "64c2d1b0-c8ad-11e8-8956-0242ac120008",
            api_cluster=api_cluster or "pl-pl0",
            app_locale=session_data.get("app_locale") or user_payload.get("locale") or "pl_PL",
            app_search_locale=(
                session_data.get("app_searchlocale")
                or session_data.get("app_search_locale")
                or user_payload.get("searchLocale")
                or "pl_PL"
            ),
            app_storage_locale=(
                session_data.get("app_storagelocale")
                or session_data.get("app_storage_locale")
                or user_payload.get("storageLocale")
                or "pl_PL"
            ),
            app_timezone=(
                session_data.get("app_timezone")
                or user_payload.get("timezone")
                or "Europe/Warsaw"
            ),
            app_os=session_data.get("app_os") or "WEB",
            app_version=(
                session_data.get("app_version")
                or user_payload.get("appVersion")
                or "4.13.1"
            ),
            user_agent=session_data.get("user_agent") or "Mozilla/5.0",
            fitatu_user_id=(
                str(session_data.get("fitatu_user_id"))
                if session_data.get("fitatu_user_id") is not None
                else (str(user_payload.get("id")) if user_payload.get("id") is not None else None)
            ),
        )

    def snapshot(self) -> dict[str, Any]:
        """Return a safe, non-secret snapshot of auth state."""
        return {
            "has_bearer_token": bool(self.bearer_token),
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
        """Export session data in a reusable library format."""
        data: dict[str, Any] = {
            "session_context_schema_version": FITATU_SESSION_CONTEXT_SCHEMA_VERSION,
            "api_key": self.api_key,
            "api_secret": self.api_secret,
            "app_uuid": self.app_uuid,
            "api_cluster": self.api_cluster,
            "app_locale": self.app_locale,
            "app_searchlocale": self.app_search_locale,
            "app_storagelocale": self.app_storage_locale,
            "app_timezone": self.app_timezone,
            "app_os": self.app_os,
            "app_version": self.app_version,
            "user_agent": self.user_agent,
        }
        if self.fitatu_user_id is not None:
            data["fitatu_user_id"] = self.fitatu_user_id
        if include_tokens:
            if self.bearer_token is not None:
                data["bearer_token"] = self.bearer_token
            if self.refresh_token is not None:
                data["refresh_token"] = self.refresh_token
        return data


@dataclass
class FitatuTokenStore:
    """Small JSON-backed token store."""

    path: Path

    def load(self) -> dict[str, str]:
        """Load persisted bearer and refresh tokens from disk."""
        if not self.path.exists():
            return {}
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
        except Exception:
            logger.warning("Failed to load token store from %s", self.path)
            return {}
        if not isinstance(raw, dict):
            return {}

        out: dict[str, str] = {}
        bearer = raw.get("bearer_token")
        refresh = raw.get("refresh_token")
        if isinstance(bearer, str) and bearer.strip():
            out["bearer_token"] = bearer
        if isinstance(refresh, str) and refresh.strip():
            out["refresh_token"] = refresh
        return out

    def save(self, *, bearer_token: str | None, refresh_token: str | None) -> None:
        """Persist the current token pair in a simple JSON document."""
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "bearer_token": bearer_token or "",
            "refresh_token": refresh_token or "",
            "updated_at": datetime.now(UTC).isoformat(),
        }
        self.path.write_text(json.dumps(payload, indent=2, ensure_ascii=True), encoding="utf-8")

    def clear(self) -> None:
        """Remove the token file when it exists."""
        try:
            self.path.unlink()
        except FileNotFoundError:
            return
