"""Shared pytest configuration for the fitatu-api test suite."""

from __future__ import annotations

import pytest


def pytest_configure(config: pytest.Config) -> None:
    config.addinivalue_line("markers", "live: requires FITATU_RUN_LIVE_TESTS=1 and real credentials")
