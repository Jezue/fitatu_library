"""Helpers that turn decoded JSON replies into the shapes the public methods promise."""

from __future__ import annotations

from typing import Any, cast

from .exceptions import FitatuApiError


def as_dict(value: Any) -> dict[str, Any] | None:
    """Return *value* as a typed dict, or None when it is not a dict."""
    return cast(dict[str, Any], value) if isinstance(value, dict) else None


def as_dict_list(value: Any) -> list[dict[str, Any]]:
    """Filter *value* down to a list containing only the dict elements."""
    if not isinstance(value, list):
        return []
    return [cast(dict[str, Any], item) for item in value if isinstance(item, dict)]


def read_reply(data: Any, what: str) -> dict[str, Any]:
    """Return a GET reply that must be a JSON object; anything else is an API error."""
    reply = as_dict(data)
    if reply is None:
        raise FitatuApiError(
            f"{what} -> unexpected response shape: expected a JSON object, got {type(data).__name__}",
            body=str(data),
        )
    return reply


def write_reply(data: Any) -> dict[str, Any]:
    """Return a write reply as a dict; an empty or non-object reply becomes ``{}``.

    The write already succeeded (2xx), so raising here would only invite a retry that
    repeats it.
    """
    return as_dict(data) or {}
