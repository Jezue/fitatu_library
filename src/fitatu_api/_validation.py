"""Input validation helpers for public API methods."""

from __future__ import annotations

from datetime import date, datetime

from .exceptions import FitatuValidationError


def validate_positive_int(value: int, name: str) -> None:
    """Raise FitatuValidationError when *value* is not a positive integer."""
    if not isinstance(value, int) or isinstance(value, bool) or value <= 0:
        raise FitatuValidationError(f"{name} must be a positive integer, got {value!r}")


def validate_non_negative_int(value: int, name: str) -> None:
    """Raise FitatuValidationError when *value* is a negative integer."""
    if not isinstance(value, int) or isinstance(value, bool) or value < 0:
        raise FitatuValidationError(f"{name} must be a non-negative integer, got {value!r}")


def validate_bool(value: bool, name: str) -> None:
    """Raise FitatuValidationError unless *value* is True or False (``"no"`` is truthy)."""
    if not isinstance(value, bool):
        raise FitatuValidationError(f"{name} must be True or False, got {value!r}")


def validate_numeric_id(value: int | str, name: str) -> str:
    """Return *value* as a string of ASCII digits, or raise FitatuValidationError.

    Ids go into URL paths; anything else (``"../users/1"``, a slug, a URL) could reach a
    different route.
    """
    text = str(value).strip() if isinstance(value, int | str) and not isinstance(value, bool) else ""
    if not (text.isascii() and text.isdigit()):
        raise FitatuValidationError(f"{name} must be a numeric id, got {value!r}")
    return text


def as_date(value: date | str, name: str = "day") -> date:
    """Return *value* as a date: a ``date``, a ``datetime`` (its date) or ``'YYYY-MM-DD'``.

    Tool calls from language models send dates as strings; anything else raises
    FitatuValidationError naming the expected format.
    """
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    if isinstance(value, str):
        try:
            return date.fromisoformat(value.strip())
        except ValueError:
            pass
    raise FitatuValidationError(f"{name} must be a date or a 'YYYY-MM-DD' string, got {value!r}")
