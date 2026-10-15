import json
import unittest

from fitatu_api import (
    FitatuApiError,
    FitatuAuthError,
    FitatuConnectionError,
    FitatuMaintenanceError,
    FitatuNotFoundError,
    FitatuPlannerSyncError,
    FitatuPremiumRequiredError,
    FitatuRateLimitError,
    FitatuValidationError,
)
from fitatu_api.exceptions import describe_error


class DescribeErrorTests(unittest.TestCase):
    def test_each_error_maps_to_a_type_and_retry_advice(self) -> None:
        cases = [
            (FitatuNotFoundError("meal not found"), "not_found", False),
            (FitatuValidationError("bad date"), "validation", False),
            (FitatuNotFoundError("GET /recipes/1 -> 404", status_code=404), "not_found", False),
            (ValueError("bad number"), "validation", False),
            # call() checks the arguments first, so these come from inside the library: a bug.
            (TypeError("unexpected keyword"), "unexpected", False),
            (AttributeError("'NoneType' object has no attribute 'get'"), "unexpected", False),
            (FitatuAuthError("expired", status_code=401), "auth", False),
            (FitatuRateLimitError("slow down", status_code=429, retry_after=12), "rate_limit", True),
            (FitatuMaintenanceError("break", display_to="2026-10-14 06:00"), "maintenance", False),
            (FitatuConnectionError("timeout"), "network", True),
            (FitatuPlannerSyncError("rejected", results=[]), "sync_rejected", False),
            (FitatuPremiumRequiredError("a premium recipe"), "forbidden", False),
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
        self.assertEqual((info["status_code"], info["retry_after"], info["hint"]), (429, 12, None))
        self.assertIn("retry once", str(describe_error(FitatuRateLimitError("slow down", status_code=429))["hint"]))
        info = describe_error(FitatuApiError("quota", status_code=403, body='{"codename": "NUMBER_OF_USES_EXCEEDED"}'))
        self.assertEqual(info["codename"], "NUMBER_OF_USES_EXCEEDED")
        self.assertIn("fitatu-api login", str(describe_error(FitatuAuthError("expired"))["hint"]))
        self.assertEqual(len(str(describe_error(FitatuApiError("x" * 900))["message"])), 500)

    def test_a_403_html_page_is_a_firewall_block_not_premium(self) -> None:
        page = "\n<!DOCTYPE html><html><head><title>Attention Required! | Cloudflare</title>"
        info = describe_error(FitatuApiError("GET /x -> 403", status_code=403, body=page))
        self.assertEqual(info["error_type"], "forbidden")
        self.assertIn("firewall", str(info["hint"]))
        self.assertNotIn("Premium", str(info["hint"]))
        self.assertIn("Premium", str(describe_error(FitatuApiError("GET /x -> 403", status_code=403, body='{"code": 403}'))["hint"]))


if __name__ == "__main__":
    unittest.main()
