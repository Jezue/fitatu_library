import json
import unittest

from fitatu_api import (
    FitatuApiError,
    FitatuAuthError,
    FitatuConnectionError,
    FitatuMaintenanceError,
    FitatuNotFoundError,
    FitatuPlannerSyncError,
    FitatuRateLimitError,
    FitatuValidationError,
)
from fitatu_api.exceptions import describe_error


class DescribeErrorTests(unittest.TestCase):
    def test_each_error_maps_to_a_type_and_retry_advice(self) -> None:
        cases = [
            (FitatuNotFoundError("meal not found"), "not_found", False),
            (FitatuValidationError("bad date"), "validation", False),
            (TypeError("unexpected keyword"), "validation", False),
            (FitatuAuthError("expired", status_code=401), "auth", False),
            (FitatuRateLimitError("slow down", status_code=429, retry_after=12), "rate_limit", True),
            (FitatuMaintenanceError("break", display_to="2026-10-14 06:00"), "maintenance", False),
            (FitatuConnectionError("timeout"), "network", True),
            (FitatuPlannerSyncError("rejected", results=[]), "sync_rejected", False),
            (FitatuApiError("quota", status_code=403, body='{"codename": "NUMBER_OF_USES_EXCEEDED"}'), "forbidden", False),
            (FitatuApiError("gone", status_code=404), "not_found", False),
            (FitatuApiError("bad body", status_code=422), "validation", False),
            (FitatuApiError("boom", status_code=502), "server", True),
            (FitatuApiError("GET /x -> unexpected response shape"), "server", False),
            (RuntimeError("bug"), "unexpected", False),
        ]
        for exc, error_type, retryable in cases:
            with self.subTest(exc=exc):
                info = describe_error(exc)
                self.assertEqual((info["error_type"], info["retryable"]), (error_type, retryable))
                json.dumps(info)

    def test_details_come_from_the_exception(self) -> None:
        info = describe_error(FitatuRateLimitError("slow down", status_code=429, retry_after=12))
        self.assertEqual((info["status_code"], info["retry_after"]), (429, 12))
        info = describe_error(FitatuApiError("quota", status_code=403, body='{"codename": "NUMBER_OF_USES_EXCEEDED"}'))
        self.assertEqual(info["codename"], "NUMBER_OF_USES_EXCEEDED")
        self.assertIn("fitatu-api login", str(describe_error(FitatuAuthError("expired"))["hint"]))
        self.assertEqual(len(str(describe_error(FitatuApiError("x" * 900))["message"])), 500)


if __name__ == "__main__":
    unittest.main()
