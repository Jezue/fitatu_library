"""Shared pytest configuration for the fitatu-api test suite."""

from __future__ import annotations

import pytest

# A developer's exported session or settings would win over the files the tests write.
_FITATU_ENVIRONMENT = ("FITATU_SESSION_JSON", "FITATU_CLIENT_PROFILE", "FITATU_SESSION", "FITATU_SESSION_PATH", "FITATU_LOG_LEVEL")


def pytest_configure(config: pytest.Config) -> None:
    config.addinivalue_line("markers", "live: requires FITATU_RUN_LIVE_TESTS=1 and real credentials")


@pytest.fixture(autouse=True)
def _no_fitatu_environment(request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch) -> None:
    if request.node.path.name != "test_live_all.py":  # the live suite runs on the exported session
        for name in _FITATU_ENVIRONMENT:
            monkeypatch.delenv(name, raising=False)
