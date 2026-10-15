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
from fitatu_api.exceptions import describe_error, redact_user_id


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

    def test_the_user_id_is_left_out_of_the_message(self) -> None:
        # The id after the user-scoped routes (a fixture-style made-up id), with the rest of the message kept.
        cases = {
            "GET /diet-plan/7654321/summary-product/energy/custom -> 403": "GET /diet-plan/<user>/summary-product/energy/custom -> 403",
            "GET /v2/diet-plan/7654321/summary/custom -> 403": "GET /v2/diet-plan/<user>/summary/custom -> 403",
            "GET /diet-and-activity-plan/7654321/day/2027-01-03 -> 503": "GET /diet-and-activity-plan/<user>/day/2027-01-03 -> 503",
            "PUT /users/7654321/measurements/2027-01-03 -> 400": "PUT /users/<user>/measurements/2027-01-03 -> 400",
            "PUT /water/7654321/2027-01-03 -> 400": "PUT /water/<user>/2027-01-03 -> 400",
            "GET /search/food/user/7654321 -> 500": "GET /search/food/user/<user> -> 500",
            "GET /search/activity/7654321/last-used -> 500": "GET /search/activity/<user>/last-used -> 500",
            "network error: Max retries exceeded with url: /api/habits/7654321/good-days?userId=7654321": (
                "network error: Max retries exceeded with url: /api/habits/<user>/good-days?userId=<user>"
            ),
            # Food ids, row UUIDs, barcodes and search phrases stay.
            "GET /search/activity/5km -> 500": "GET /search/activity/5km -> 500",
            "GET /products/7654321 -> 404": "GET /products/7654321 -> 404",
            "row_id '5f0c3a2e-1d2b-11ef-9c1a-7654321abcde' is not on 2027-01-03": "row_id '5f0c3a2e-1d2b-11ef-9c1a-7654321abcde' is not on 2027-01-03",
        }
        for text, shown in cases.items():
            with self.subTest(text=text):
                self.assertEqual(redact_user_id(text), shown)
        # The known id goes wherever it stands alone, e.g. echoed by the server; longer numbers keep it.
        echoed = "POST /x -> 403: user 7654321 has no plan; barcode 5907654321001, food 76543210"
        self.assertEqual(redact_user_id(echoed, "7654321"), "POST /x -> 403: user <user> has no plan; barcode 5907654321001, food 76543210")
        self.assertEqual(describe_error(FitatuApiError(echoed, status_code=403), user_id="7654321")["message"], redact_user_id(echoed, "7654321"))


if __name__ == "__main__":
    unittest.main()
