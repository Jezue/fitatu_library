"""Shared pytest fixtures and marks for fitatu-api test suite."""

from __future__ import annotations

import pytest

from fitatu_api import FitatuApiClient, FitatuAuthContext


def pytest_configure(config: pytest.Config) -> None:
    config.addinivalue_line("markers", "live: requires FITATU_RUN_LIVE_TESTS=1 and real credentials")
    config.addinivalue_line("markers", "integration: slower tests that touch real subsystems (db, disk)")


# ---------------------------------------------------------------------------
# Auth & client fixtures
# ---------------------------------------------------------------------------

TEST_BEARER = "test-bearer-token"
TEST_REFRESH = "test-refresh-token"
TEST_USER_ID = "user-test-123"


@pytest.fixture()
def test_auth() -> FitatuAuthContext:
    """Minimal FitatuAuthContext suitable for unit tests."""
    return FitatuAuthContext(
        bearer_token=TEST_BEARER,
        refresh_token=TEST_REFRESH,
        fitatu_user_id=TEST_USER_ID,
    )


@pytest.fixture()
def api_client(test_auth: FitatuAuthContext) -> FitatuApiClient:
    """FitatuApiClient wired with test auth and zero retry delay."""
    return FitatuApiClient(
        auth=test_auth,
        retry_max_attempts=3,
        retry_base_delay_seconds=0.0,
    )
