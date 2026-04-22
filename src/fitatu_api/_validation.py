"""Input validation helpers for public API methods."""

from __future__ import annotations


def validate_user_id(value: str, name: str = "user_id") -> None:
    """Raise ValueError when *value* is not a non-empty string."""
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{name} must be a non-empty string, got {value!r}")


def validate_positive_int(value: int, name: str) -> None:
    """Raise ValueError when *value* is not a positive integer."""
    if not isinstance(value, int) or isinstance(value, bool) or value <= 0:
        raise ValueError(f"{name} must be a positive integer, got {value!r}")


def validate_non_negative_int(value: int, name: str) -> None:
    """Raise ValueError when *value* is a negative integer."""
    if not isinstance(value, int) or isinstance(value, bool) or value < 0:
        raise ValueError(f"{name} must be a non-negative integer, got {value!r}")
