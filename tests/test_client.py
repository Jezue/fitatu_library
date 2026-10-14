"""Low-level client: requests, auth, token store, validation, login."""

from __future__ import annotations

import base64
import json
import os
import tempfile
import time
import unittest
from datetime import date
from pathlib import Path
from unittest.mock import Mock, patch

import requests

from fitatu_api import (
    FitatuApiClient,
    FitatuApiError,
    FitatuAuthContext,
    FitatuLibrary,
    FitatuTokenStore,
    WaterModule,
)
from fitatu_api.auth import jwt_user_id
from fitatu_api.exceptions import (
    FitatuAuthError,
    FitatuConnectionError,
    FitatuMaintenanceError,
    FitatuNotFoundError,
    FitatuRateLimitError,
    FitatuValidationError,
    describe_error,
)

TEST_DAY = date(2026, 4, 19)



class ApiClientTests(unittest.TestCase):
    def _make_client(self, **kwargs: object) -> FitatuApiClient:
        auth = FitatuAuthContext(
            bearer_token="token",
            refresh_token="refresh-token",
            fitatu_user_id="user-123",
        )
        return FitatuApiClient(
            auth=auth,
            retry_max_attempts=3,
            retry_base_delay_seconds=0.0,
            **kwargs,
        )

    def test_create_product_sends_minimal_payload(self) -> None:
        client = self._make_client()
        with patch.object(client, "request", return_value={"id": 1, "name": "A"}) as req:
            result = client.create_product(
                name="A",
                brand="B",
                energy=100,
                protein=10,
                fat=5,
                carbohydrate=12,
            )
        self.assertEqual(result["id"], 1)
        req.assert_called_once_with(
            "POST",
            "/products",
            json_data={
                "name": "A",
                "brand": "B",
                "energy": 100,
                "protein": 10,
                "fat": 5,
                "carbohydrate": 12,
            },
        )

    def test_create_product_accepts_extended_payload_fields(self) -> None:
        client = self._make_client()
        with patch.object(client, "request", return_value={"id": 2}) as req:
            client.create_product(
                name="Meal",
                brand="Provider",
                energy=500,
                protein=35,
                fat=20,
                carbohydrate=45,
                saturated_fat=4,
                salt=2.1,
            )

        payload = req.call_args.kwargs["json_data"]
        self.assertEqual(payload["saturatedFat"], 4)
        self.assertEqual(payload["salt"], 2.1)
        # measures are not included — POST /products 404s when measures key is present
        self.assertNotIn("measures", payload)

    def test_search_user_food_calls_user_food_endpoint(self) -> None:
        client = self._make_client()
        with patch.object(client, "request", return_value=[{"foodId": 1, "name": "Meal"}]) as req:
            result = client.search_user_food("123", "Meal", TEST_DAY, page=2, limit=10)

        self.assertEqual(result[0]["foodId"], 1)
        req.assert_called_once_with(
            "GET",
            "/search/food/user/123",
            params={
                "date": TEST_DAY.isoformat(),
                "phrase": "Meal",
                "page": 2,
                "limit": 10,
            },
        )

    def test_delete_product_calls_delete_endpoint(self) -> None:
        client = self._make_client()
        with patch.object(client, "request", return_value={"ok": True}) as req:
            result = client.delete_product(123)

        self.assertEqual(result["ok"], True)
        req.assert_called_once_with("DELETE", "/products/123")

    def test_delete_recipe_calls_delete_endpoint(self) -> None:
        client = self._make_client()
        with patch.object(client, "request", return_value=[]) as req:  # the live reply: 200 []
            result = client.delete_recipe(123)

        self.assertEqual(result, {})
        req.assert_called_once_with("DELETE", "/recipes/123")

    def test_set_product_proposal_calls_proposals_endpoint(self) -> None:
        client = self._make_client()
        with patch.object(client, "request", return_value={"id": 9}) as req:
            result = client.set_product_proposal(
                123,
                property_name="rawIngredients",
                property_value="rice, chicken",
            )

        self.assertEqual(result["id"], 9)
        req.assert_called_once_with(
            "POST",
            "/products/123/proposals",
            json_data={
                "propertyName": "rawIngredients",
                "propertyValue": "rice, chicken",
            },
        )

    def test_set_product_proposal_rejects_unsupported_property_name(self) -> None:
        client = self._make_client()
        with self.assertRaises(FitatuApiError) as ctx:
            client.set_product_proposal(123, property_name="description", property_value="test")
        self.assertIn("unsupported propertyName", str(ctx.exception))
        self.assertIn("description", str(ctx.exception))

    def test_set_product_raw_ingredients_accepts_list(self) -> None:
        client = self._make_client()
        with patch.object(client, "set_product_proposal", return_value={"ok": True}) as proposal:
            result = client.set_product_raw_ingredients(123, ["rice", "chicken"])

        self.assertEqual(result["ok"], True)
        proposal.assert_called_once_with(
            123,
            property_name="rawIngredients",
            property_value="rice, chicken",
        )

    def test_nutrition_values_match_uses_relative_tolerance(self) -> None:
        self.assertTrue(FitatuApiClient.nutrition_values_match(100.0, 100.05, tolerance=0.001))
        self.assertFalse(FitatuApiClient.nutrition_values_match(100.0, 100.2, tolerance=0.001))

    def test_nutrition_values_match_skips_missing_expected_values(self) -> None:
        self.assertTrue(FitatuApiClient.nutrition_values_match(100.0, "N/A"))
        self.assertTrue(FitatuApiClient.nutrition_values_match(None, 100.0))

    def test_product_nutrition_matches_selected_fields(self) -> None:
        product = {"energy": 500.0, "protein": 30.0, "fat": 20.0, "carbohydrate": 40.0}
        expected = {"energy": 500.3, "protein": 30.0}

        self.assertTrue(
            FitatuApiClient.product_nutrition_matches(
                product,
                expected,
                fields=("energy", "protein"),
                tolerance=0.001,
            )
        )

    def test_find_matching_user_product_filters_by_brand_and_nutrition(self) -> None:
        client = self._make_client()
        products = [
            {"foodId": 1, "name": "Meal", "brand": "Other", "energy": 500, "protein": 30},
            {"foodId": 2, "name": "Meal", "brand": "Provider", "energy": 501, "protein": 30},
            {"foodId": 3, "name": "Meal", "brand": "Provider", "energy": 500.2, "protein": 30},
        ]
        with patch.object(client, "search_user_food", return_value=products):
            result = client.find_matching_user_product(
                "user-123",
                "Meal",
                TEST_DAY,
                brand="Provider",
                nutrition={"energy": 500, "protein": 30},
                fields=("energy", "protein"),
                tolerance=0.001,
            )

        assert result is not None
        self.assertEqual(result["foodId"], 3)

    def test_cleanup_duplicate_user_products_requires_filter(self) -> None:
        client = self._make_client()
        with self.assertRaises(FitatuApiError):
            client.cleanup_duplicate_user_products("user-123", "Meal", TEST_DAY)

    def test_cleanup_duplicate_user_products_deletes_filtered_duplicates(self) -> None:
        client = self._make_client()
        products = [
            {"foodId": 1, "type": "PRODUCT", "name": "Meal", "brand": "Provider", "energy": 100},
            {"foodId": 2, "type": "PRODUCT", "name": "Meal", "brand": "Provider", "energy": 100},
            {"foodId": 3, "type": "PRODUCT", "name": "Meal", "brand": "Other", "energy": 100},
        ]
        with patch.object(client, "search_user_food", return_value=products), patch.object(
            client,
            "delete_product",
            return_value={"ok": True},
        ) as delete_product:
            result = client.cleanup_duplicate_user_products(
                "user-123",
                "Meal",
                TEST_DAY,
                brand="Provider",
                keep_product_id=1,
                dry_run=False,
            )

        self.assertEqual(result["ok"], True)
        self.assertEqual(result["matched_count"], 2)
        self.assertEqual(result["deleted_product_ids"], ["2"])
        delete_product.assert_called_once_with("2")

    def test_cleanup_duplicate_user_products_accepts_custom_predicate(self) -> None:
        client = self._make_client()
        products = [
            {"foodId": 1, "type": "PRODUCT", "name": "Meal", "brand": "A", "energy": 100},
            {"foodId": 2, "type": "PRODUCT", "name": "Meal", "brand": "B", "energy": 200},
        ]
        with patch.object(client, "search_user_food", return_value=products), patch.object(
            client,
            "delete_product",
            return_value={"ok": True},
        ) as delete_product:
            result = client.cleanup_duplicate_user_products(
                "user-123",
                "Meal",
                TEST_DAY,
                predicate=lambda product: product.get("energy") == 200,
                dry_run=False,
            )

        self.assertEqual(result["matched_count"], 1)
        self.assertEqual(result["deleted_count"], 0)
        delete_product.assert_not_called()

    def _cleanup(self, products: list[dict], **kwargs: object) -> tuple[dict, Mock]:
        client = self._make_client()
        delete = kwargs.pop("delete", None) or Mock(return_value={})
        with patch.object(client, "search_user_food", return_value=products), patch.object(
            client, "delete_product", delete
        ):
            result = client.cleanup_duplicate_user_products("123", "Meal", TEST_DAY, brand="P", **kwargs)
        return result, delete

    def test_cleanup_is_a_dry_run_by_default_and_skips_recipes(self) -> None:
        rows = [
            {"foodId": 1, "type": "PRODUCT", "brand": "P"},
            {"foodId": 2, "type": "PRODUCT", "brand": "P"},
            {"foodId": 3, "type": "RECIPE", "brand": "P"},
            {"foodId": 4, "brand": "P"},
        ]
        result, delete = self._cleanup(rows, keep_product_id=1)

        delete.assert_not_called()
        self.assertEqual((result["dry_run"], result["matched_count"]), (True, 2))
        self.assertEqual((result["to_delete_product_ids"], result["deleted_product_ids"]), (["2"], []))

    def test_cleanup_needs_a_known_keep_id_when_several_match(self) -> None:
        rows = [{"foodId": n, "type": "PRODUCT", "brand": "P"} for n in (1, 2)]
        with self.assertRaisesRegex(FitatuValidationError, "keep_product_id"):
            self._cleanup(rows, dry_run=False)
        with self.assertRaisesRegex(FitatuValidationError, "not one of the matches"):
            self._cleanup(rows, keep_product_id=99, dry_run=False)

    def test_cleanup_refuses_more_than_max_deletions(self) -> None:
        rows = [{"foodId": n, "type": "PRODUCT", "brand": "P"} for n in range(1, 13)]
        delete = Mock(return_value={})
        with self.assertRaisesRegex(FitatuValidationError, "max_deletions=10"):
            self._cleanup(rows, keep_product_id=1, dry_run=False, delete=delete)
        delete.assert_not_called()

        result, _ = self._cleanup(rows, keep_product_id=1, dry_run=False, max_deletions=11)
        self.assertEqual(result["deleted_count"], 11)

    def test_cleanup_stops_on_auth_and_rate_limit_errors(self) -> None:
        rows = [{"foodId": n, "type": "PRODUCT", "brand": "P"} for n in (1, 2, 3, 4)]
        for error in (FitatuAuthError("gone", status_code=401), FitatuRateLimitError("slow", status_code=429)):
            with self.subTest(error=type(error).__name__):
                delete = Mock(side_effect=error)
                with self.assertRaises(type(error)):
                    self._cleanup(rows, keep_product_id=1, dry_run=False, delete=delete)
                delete.assert_called_once()

        delete = Mock(side_effect=[FitatuApiError("x", status_code=500), {}, {}])
        result, _ = self._cleanup(rows, keep_product_id=1, dry_run=False, delete=delete)
        self.assertEqual((result["ok"], result["deleted_product_ids"]), (False, ["3", "4"]))
        self.assertEqual(result["errors"][0]["product_id"], "2")

    def test_search_user_food_accepts_iso_date_strings(self) -> None:
        client = self._make_client()
        with patch.object(client, "request", return_value=[]) as req:
            client.search_user_food("123", "Meal", "2026-04-19")
        self.assertEqual(req.call_args.kwargs["params"]["date"], "2026-04-19")
        with self.assertRaisesRegex(FitatuValidationError, "YYYY-MM-DD"):
            client.search_user_food("123", "Meal", "19.04.2026")

    def test_request_maps_network_errors_to_fitatu_api_error(self) -> None:
        client = self._make_client()
        with patch(
            "fitatu_api.client.requests.Session.request",
            side_effect=requests.RequestException("boom"),
        ):
            with self.assertRaises(FitatuConnectionError):
                client.request("GET", "/ping")

    def test_request_raises_typed_errors_by_status(self) -> None:
        cases = {401: FitatuAuthError, 403: FitatuApiError, 429: FitatuRateLimitError, 422: FitatuApiError}
        for status, expected in cases.items():
            client = self._make_client()
            client.auth.refresh_token = None
            response = Mock(status_code=status, text="{}", headers={})
            with self.subTest(status=status), patch(
                "fitatu_api.client.requests.Session.request", return_value=response
            ), patch("fitatu_api.client.time.sleep", return_value=None):
                with self.assertRaises(expected) as ctx:
                    client.request("GET", "/ping")
                self.assertEqual(ctx.exception.status_code, status)
                self.assertIs(type(ctx.exception), expected)

    def test_api_error_exposes_codename_from_body(self) -> None:
        error = FitatuApiError("quota", status_code=403, body='{"codename": "NUMBER_OF_USES_EXCEEDED"}')
        self.assertEqual(error.codename, "NUMBER_OF_USES_EXCEEDED")
        self.assertIsNone(FitatuApiError("x", body="not json").codename)
        self.assertIsNone(FitatuApiError("x").codename)

    def test_request_retries_get_on_429_then_succeeds(self) -> None:
        client = self._make_client()
        first = Mock(status_code=429, text="rate limited", headers={"retry-after": "0"})
        second = Mock(status_code=200, text='{"ok": true}', headers={})
        second.json.return_value = {"ok": True}
        with patch(
            "fitatu_api.client.requests.Session.request",
            side_effect=[first, second],
        ) as req, patch(
            "fitatu_api.client.time.sleep",
            return_value=None,
        ) as sleep_mock:
            result = client.request("GET", "/ping")
        self.assertEqual(result["ok"], True)
        self.assertEqual(req.call_count, 2)
        self.assertEqual(sleep_mock.call_count, 1)

    def test_request_retries_after_refresh_on_401(self) -> None:
        client = self._make_client()
        first = Mock(status_code=401, text="expired", headers={})
        second = Mock(status_code=200, text='{"ok": true}', headers={})
        second.json.return_value = {"ok": True}

        with patch(
            "fitatu_api.client.requests.Session.request",
            side_effect=[first, second],
        ) as req, patch.object(client, "_refresh") as refresh:
            result = client.request("GET", "/ping")

        self.assertEqual(result["ok"], True)
        self.assertEqual(req.call_count, 2)
        refresh.assert_called_once()

    def test_request_returns_plain_text_when_json_decode_fails(self) -> None:
        client = self._make_client()
        response = Mock(status_code=200, text="plain-text-response", headers={})
        response.json.side_effect = ValueError("not-json")

        with patch("fitatu_api.client.requests.Session.request", return_value=response):
            result = client.request("GET", "/ping")

        self.assertEqual(result, "plain-text-response")

    def test_request_returns_none_for_empty_body(self) -> None:
        client = self._make_client()
        response = Mock(status_code=204, text="", headers={})

        with patch("fitatu_api.client.requests.Session.request", return_value=response):
            result = client.request("DELETE", "/ping")

        self.assertIsNone(result)

    def test_search_food_supports_nested_dict_shape(self) -> None:
        client = self._make_client()
        payload = {
            "data": {
                "items": [
                    {"id": 1, "name": "Banan"},
                    {"id": 2, "name": "Jablko"},
                ]
            }
        }
        with patch.object(client, "request", return_value=payload):
            result = client.search_food("ban")
        self.assertEqual(len(result), 2)
        self.assertEqual(result[0]["name"], "Banan")

    def test_get_product_details_does_not_try_the_recipe_route(self) -> None:
        # Product and recipe ids overlap: a 404 must not turn into some unrelated recipe.
        client = self._make_client()

        with patch.object(client, "request", side_effect=FitatuApiError("missing", status_code=404)) as req:
            with self.assertRaises(FitatuApiError) as caught:
                client.get_product_details(321)

        self.assertEqual(caught.exception.status_code, 404)
        req.assert_called_once_with("GET", "/products/321")

    def test_search_food_sends_access_type_as_list_param(self) -> None:
        client = self._make_client()
        with patch.object(client, "request", return_value=[]) as req:
            client.search_food("ban")

        params = req.call_args.kwargs["params"]
        self.assertEqual(params["accessType[]"], ["FREE", "PREMIUM"])
        self.assertNotIn("accessType", params)

    def test_refresh_persists_tokens_to_store(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            store_path = Path(tmpdir) / "fitatu_tokens.json"
            client = self._make_client(token_store_path=store_path)
            with patch.object(
                client,
                "request",
                return_value={"token": "new-token", "refreshToken": "new-refresh"},
            ):
                result = client.refresh_access_token()
            self.assertEqual(result, client.describe_auth_state())
            self.assertEqual(client.auth.refresh_token, "new-refresh")
            self.assertIn("new-token", store_path.read_text(encoding="utf-8"))
            self.assertIn("new-refresh", store_path.read_text(encoding="utf-8"))

    def test_refresh_sends_only_the_refresh_token_and_returns_no_tokens(self) -> None:
        client = self._make_client()
        with patch.object(client, "request", return_value={"token": "new-token", "refreshToken": "new-refresh"}) as request:
            result = client.refresh_access_token()

        request.assert_called_once_with(
            "POST",
            "/token/refresh",
            json_data={"refresh_token": "refresh-token"},
            include_auth=False,
            allow_refresh_on_401=False,
        )
        self.assertEqual((client.auth.bearer_token, client.auth.refresh_token), ("new-token", "new-refresh"))
        self.assertEqual(result, client.describe_auth_state())
        self.assertNotIn("new-token", json.dumps(result))
        self.assertNotIn("new-refresh", json.dumps(result))

    def test_rejected_refresh_token_stops_auto_refresh_until_a_new_one_arrives(self) -> None:
        client = self._make_client()
        expired = Mock(status_code=401, text="", headers={})
        rejected = Mock(status_code=401, text='{"message": "Invalid refresh token"}', headers={})
        with patch("fitatu_api.client.requests.Session.request", side_effect=[expired, rejected, expired]) as http:
            with self.assertRaises(FitatuAuthError):
                client.request("GET", "/ping")
            with self.assertRaises(FitatuAuthError):
                client.request("GET", "/ping")

        # call 1: 401, refresh rejected; call 2: 401 and no second refresh attempt
        self.assertEqual([c.kwargs["url"].rsplit("/", 1)[-1] for c in http.call_args_list], ["ping", "refresh", "ping"])

        client.auth.refresh_token = "a-new-refresh-token"
        ok = Mock(status_code=200, text='{"token": "fresh"}', headers={})
        ok.json.return_value = {"token": "fresh"}
        with patch("fitatu_api.client.requests.Session.request", side_effect=[expired, ok, ok]) as http:
            client.request("GET", "/ping")
        self.assertEqual(http.call_count, 3)
        self.assertEqual(client.auth.bearer_token, "fresh")

    def test_refresh_without_a_refresh_token_is_an_auth_error(self) -> None:
        client = self._make_client()
        client.auth.refresh_token = None

        with self.assertRaises(FitatuAuthError) as caught:
            client.refresh_access_token()

        self.assertIn("missing refresh_token", str(caught.exception))

    def test_a_rejected_refresh_token_is_an_auth_error_for_400_too(self) -> None:
        for status in (400, 401):
            client = self._make_client()
            rejected = Mock(status_code=status, text='{"message": "Invalid refresh token"}', headers={})
            with self.subTest(status=status), patch("fitatu_api.client.requests.Session.request", return_value=rejected):
                with self.assertRaises(FitatuAuthError) as caught:
                    client.refresh_access_token()
                self.assertEqual(caught.exception.status_code, status)

    def test_a_refresh_that_fails_otherwise_raises_its_own_error(self) -> None:
        client = self._make_client()  # a refresh is a POST, so a 503 is not retried
        down = Mock(status_code=503, text="", headers={})
        with patch("fitatu_api.client.requests.Session.request", return_value=down):
            with self.assertRaises(FitatuApiError) as caught:
                client.refresh_access_token()
        self.assertNotIsInstance(caught.exception, FitatuAuthError)
        self.assertEqual(caught.exception.status_code, 503)

    def test_refresh_raises_when_response_has_no_token(self) -> None:
        client = self._make_client()

        with patch.object(client, "request", return_value={"status": "ok"}):
            with self.assertRaises(FitatuApiError) as caught:
                client.refresh_access_token()

        self.assertIn("token not found", str(caught.exception))

    def test_clear_auth_clears_tokens_and_store(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            store_path = Path(tmpdir) / "fitatu_tokens.json"
            client = self._make_client(token_store_path=store_path)
            store_path.write_text('{"bearer_token":"a","refresh_token":"b"}', encoding="utf-8")

            client.clear_auth()

            self.assertIsNone(client.auth.bearer_token)
            self.assertIsNone(client.auth.refresh_token)
            self.assertFalse(store_path.exists())

    def test_headers_and_url_helpers_include_expected_values(self) -> None:
        client = self._make_client(base_url="https://de-de.fitatu.com/api/")

        headers = client._headers(include_auth=True, has_json=False)

        self.assertIn("authorization", headers)
        self.assertNotIn("content-type", headers)
        self.assertIn("content-type", client._headers(include_auth=True, has_json=True))
        self.assertEqual(client._url("/ping"), "https://de-de.fitatu.com/api/ping")
        self.assertEqual(client._url("ping"), "https://de-de.fitatu.com/api/ping")
        cms = "https://www.fitatu.com/cms/api/graphql"
        self.assertEqual(client._url(cms), cms)

    def test_auth_context_roundtrips_to_session_data(self) -> None:
        auth = FitatuAuthContext(
            bearer_token="bearer",
            refresh_token="refresh",
            api_key="api-key",
            api_secret="api-secret",
            app_uuid="uuid",
            api_cluster="cluster",
            app_locale="pl_PL",
            app_search_locale="pl_PL",
            app_storage_locale="pl_PL",
            app_timezone="Europe/Warsaw",
            app_os="WEB",
            app_version="4.13.1",
            user_agent="agent",
            fitatu_user_id="user-123",
        )
        exported = auth.to_session_data(include_tokens=True)
        restored = FitatuAuthContext.from_session_data(exported)
        self.assertEqual(restored.bearer_token, "bearer")
        self.assertEqual(restored.refresh_token, "refresh")
        self.assertEqual(restored.fitatu_user_id, "user-123")
        # The client identity is a library constant, not part of the session.
        self.assertNotIn("api_key", exported)
        self.assertNotIn("api_cluster", exported)
        self.assertEqual(restored.api_cluster, "pl-pl0")
        # Older session files carry the identity too; it must not pin an outdated client profile.
        old_file = {"api_cluster": "pl-pl2", "app_version": "4.0.0", "api_secret": "old", "user_agent": "x"}
        self.assertEqual(FitatuAuthContext.from_session_data(old_file), FitatuAuthContext())

    def test_recipe_catalog_helpers_call_expected_routes(self) -> None:
        client = self._make_client()
        with patch.object(client, "request", side_effect=[{"items": []}, {"id": "breakfast"}, {"id": 7, "name": "Omelet"}]) as req:
            catalog = client.get_recipes_catalog()
            category = client.get_recipes_catalog_category("breakfast")
            recipe = client.get_recipe(7)

        self.assertEqual(catalog["items"], [])
        self.assertEqual(category["id"], "breakfast")
        self.assertEqual(recipe["name"], "Omelet")
        # /recipes-catalog must not receive params — any params cause 400
        self.assertEqual(req.call_args_list[0].args, ("GET", "/recipes-catalog"))
        self.assertNotIn("params", req.call_args_list[0].kwargs)
        self.assertEqual(req.call_args_list[1].args, ("GET", "/recipes-catalog/category/breakfast"))
        self.assertEqual(req.call_args_list[2].args, ("GET", "/recipes/7"))

    def test_normalize_recipe_items_accepts_multiple_shapes(self) -> None:
        normalized = FitatuApiClient.normalize_recipe_items(
            [
                {"itemId": 10, "measureId": 20, "measureQuantity": 2, "type": "RECIPE"},
                {"foodId": 30, "measureId": 40, "quantity": 3},
                {"foodId": 99},
            ]
        )

        self.assertEqual(
            normalized,
            [
                {"type": "RECIPE", "itemId": 10, "measureId": 20, "measureQuantity": 2},
                {"type": "PRODUCT", "itemId": 30, "measureId": 40, "measureQuantity": 3},
            ],
        )

    def test_package_root_exports_main_symbols(self) -> None:
        import fitatu_api

        self.assertIs(fitatu_api.FitatuApiClient, FitatuApiClient)
        self.assertIs(fitatu_api.FitatuLibrary, FitatuLibrary)
        self.assertIs(fitatu_api.FitatuValidationError, FitatuValidationError)
        self.assertIs(fitatu_api.FitatuNotFoundError, FitatuNotFoundError)
        self.assertIs(fitatu_api.FitatuMaintenanceError, FitatuMaintenanceError)
        self.assertIsInstance(fitatu_api.__version__, str)


class CredentialSafetyTests(unittest.TestCase):
    def _client(self) -> FitatuApiClient:
        auth = FitatuAuthContext(bearer_token="secret-bearer", refresh_token="secret-refresh")
        return FitatuApiClient(auth=auth, retry_max_attempts=1, retry_base_delay_seconds=0.0)

    def test_urls_off_fitatu_or_without_https_are_refused(self) -> None:
        client = self._client()
        # requests connects to evil.example for the backslash ones; urlsplit sees a Fitatu host.
        tricky = ("https://evil.example\\.fitatu.com/api", "https://evil.example\\@pl-pl.fitatu.com/api")
        for url in (
            "https://evil.example/api/x",
            "http://pl-pl.fitatu.com/api/x",
            "https://fitatu.com.evil.example/x",
            "https://user@pl-pl.fitatu.com/api/x",
            *(f"{base}/x" for base in tricky),
        ):
            with self.subTest(url=url), patch("fitatu_api.client.requests.Session.request") as http:
                with self.assertRaises(FitatuValidationError):
                    client.request("GET", url)
                http.assert_not_called()
        for base_url in ("https://example.com/api", *tricky):
            with self.subTest(base_url=base_url), self.assertRaises(ValueError):
                FitatuApiClient(auth=FitatuAuthContext(bearer_token="b"), base_url=base_url)

    def test_bearer_goes_only_to_the_api_root(self) -> None:
        client = self._client()
        response = Mock(status_code=200, text="", headers={})
        with patch("fitatu_api.client.requests.Session.request", return_value=response) as http:
            client.request("POST", "https://www.fitatu.com/cms/api/graphql", json_data={"query": "{ x }"})
            client.request("GET", "/users/1")

        cms_headers, api_headers = (call.kwargs["headers"] for call in http.call_args_list)
        self.assertNotIn("authorization", cms_headers)
        self.assertEqual(cms_headers["api-key"], "FITATU-MOBILE-APP")
        self.assertEqual(api_headers["authorization"], "Bearer secret-bearer")

    def test_redirects_are_not_followed(self) -> None:
        # requests would resend a 307's POST body (password, refresh token) to the Location host.
        def redirect(request: requests.PreparedRequest, **kwargs: object) -> requests.Response:
            response = requests.Response()
            response.status_code, response.url, response.request = 307, request.url, request
            response.headers["Location"] = "http://evil.example/steal"
            response._content = b""
            return response

        with patch("requests.adapters.HTTPAdapter.send", side_effect=redirect) as send:
            with self.assertRaises(FitatuApiError) as login:
                FitatuApiClient.login("me@example.com", "hunter2-password")
            with self.assertRaises(FitatuApiError) as refresh:
                self._client().refresh_access_token()

        self.assertEqual(
            [call.args[0].url for call in send.call_args_list],
            ["https://pl-pl.fitatu.com/api/login", "https://pl-pl.fitatu.com/api/token/refresh"],
        )
        self.assertEqual(login.exception.status_code, 307)
        self.assertEqual(refresh.exception.status_code, 307)

    def test_path_ids_must_be_numeric(self) -> None:
        client = self._client()
        with patch.object(client, "request") as request:
            for call in (
                lambda: client.delete_product("../users/123/measurements/2020-01-01"),
                lambda: client.delete_recipe("1/../../users/1"),
                lambda: client.get_product_details("12/../../users/1"),
                lambda: client.set_product_raw_ingredients("x1", "rice"),
                lambda: client.get_recipe("recipe-of-the-day"),
                lambda: client.search_user_food("1/../2", "rice", TEST_DAY),
            ):
                with self.assertRaises(FitatuValidationError):
                    call()
            request.assert_not_called()

            client.delete_product(" 42 ")
            client.get_recipes_catalog_category("../users/1")
        self.assertEqual(request.call_args_list[0].args, ("DELETE", "/products/42"))
        self.assertEqual(request.call_args_list[1].args, ("GET", "/recipes-catalog/category/..%2Fusers%2F1"))

    def test_secrets_stay_out_of_repr_logs_and_results(self) -> None:
        client = self._client()
        self.assertNotIn("secret", repr(client.auth))

        replies = [
            Mock(status_code=401, text="", headers={}),
            _json_response(200, {"token": "secret-new-bearer", "refreshToken": "secret-new-refresh"}),
            Mock(status_code=500, text='{"message": "boom"}' + "x" * 1000, headers={}),
            _json_response(200, {"token": "secret-new-bearer"}),
        ]
        with self.assertLogs("fitatu_api", level="DEBUG") as logs, patch(
            "fitatu_api.client.requests.Session.request", side_effect=replies
        ):
            with self.assertRaises(FitatuApiError):
                client.request("GET", "/users/1")
            result = client.refresh_access_token()

        output = "\n".join(logs.output)
        for secret in ("secret-bearer", "secret-refresh", "secret-new-bearer", "secret-new-refresh"):
            self.assertNotIn(secret, output)
            self.assertNotIn(secret, json.dumps(result, default=str))
        self.assertNotIn("x" * 301, output)
        info_lines = [line for line in logs.output if line.startswith("INFO")]
        self.assertFalse(any("fitatu.request." in line for line in info_lines))

    def test_token_with_a_line_break_is_not_quoted_in_the_error(self) -> None:
        client = FitatuApiClient(auth=FitatuAuthContext(bearer_token="eyJSECRET\n"), retry_base_delay_seconds=0.0)
        with patch("requests.adapters.HTTPAdapter.send") as send, patch("fitatu_api.client.time.sleep") as sleep:
            with self.assertRaises(FitatuValidationError) as ctx:
                client.request("GET", "/users/1")
        send.assert_not_called()
        sleep.assert_not_called()
        self.assertNotIn("eyJSECRET", f"{ctx.exception} {ctx.exception.body} {ctx.exception.__cause__}")

        session = {"bearer_token": "eyJSECRET\n", "refresh_token": " r\n", "app_locale": "en_GB\n"}
        auth = FitatuAuthContext.from_session_data(session)
        self.assertEqual((auth.bearer_token, auth.refresh_token, auth.app_locale), ("eyJSECRET", "r", "en_GB"))
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "session.json"
            path.write_text(json.dumps(session), encoding="utf-8")
            self.assertEqual(FitatuTokenStore(path).load(), {"bearer_token": "eyJSECRET", "refresh_token": "r"})

    def test_log_redaction_keeps_flags_and_hides_values(self) -> None:
        from fitatu_api.client import _serialize_log_value

        self.assertEqual(
            _serialize_log_value({"refresh_token": "abc", "has_refresh_token": True, "nested": {"Authorization": "Bearer x"}}),
            {"refresh_token": "[redacted]", "has_refresh_token": True, "nested": {"Authorization": "[redacted]"}},
        )


class ErrorModelTests(unittest.TestCase):
    def _client(self) -> FitatuApiClient:
        auth = FitatuAuthContext(bearer_token="token", refresh_token="refresh-token")
        return FitatuApiClient(auth=auth, retry_max_attempts=1, retry_base_delay_seconds=0.0)

    def test_http_error_message_names_route_status_codename_and_server_message(self) -> None:
        body = json.dumps({"codename": "NUMBER_OF_USES_EXCEEDED", "message": "x" * 300})
        response = Mock(status_code=403, text=body, headers={})
        with patch("fitatu_api.client.requests.Session.request", return_value=response):
            with self.assertRaises(FitatuApiError) as ctx:
                self._client().request("POST", "/ai/meal")

        self.assertEqual(str(ctx.exception), "POST /ai/meal -> 403 NUMBER_OF_USES_EXCEEDED: " + "x" * 200)

    def test_http_error_message_without_codename_uses_the_body_message(self) -> None:
        response = Mock(status_code=404, text='{"message": "Measurement not found"}', headers={})
        with patch("fitatu_api.client.requests.Session.request", return_value=response):
            with self.assertRaises(FitatuNotFoundError) as ctx:
                self._client().request("GET", "/users/1/measurements/2020-01-01")

        self.assertEqual(str(ctx.exception), "GET /users/1/measurements/2020-01-01 -> 404: Measurement not found")
        self.assertEqual(ctx.exception.status_code, 404)

    def test_validation_errors_are_api_errors_and_value_errors(self) -> None:
        self.assertIsInstance(FitatuValidationError("bad"), FitatuApiError)
        self.assertIsInstance(FitatuValidationError("bad"), ValueError)
        # Not a ValueError: a server 404 is one too, and nothing was wrong with the arguments.
        self.assertIsInstance(FitatuNotFoundError("gone"), FitatuApiError)
        self.assertNotIsInstance(FitatuNotFoundError("gone"), ValueError)
        with self.assertRaises(FitatuValidationError):
            self._client().search_food("banan", limit=0)

    def test_technical_break_header_raises_maintenance_error_without_refreshing(self) -> None:
        window = {"displayFromDate": "2026-10-13T22:00:00+02:00", "displayToDate": "2026-10-14T02:00:00+02:00"}
        header = '"' + base64.b64encode(json.dumps(window).encode()).decode().rstrip("=") + '"'
        response = Mock(status_code=401, text="", headers={"technical-break": header})
        client = self._client()
        with patch("fitatu_api.client.requests.Session.request", return_value=response), patch.object(
            client, "_refresh"
        ) as refresh:
            with self.assertRaises(FitatuMaintenanceError) as ctx:
                client.request("GET", "/users/1")

        refresh.assert_not_called()
        self.assertEqual(ctx.exception.display_from, window["displayFromDate"])
        self.assertEqual(ctx.exception.display_to, window["displayToDate"])
        self.assertIn("technical break", str(ctx.exception))

    def test_get_with_unexpected_shape_raises_and_empty_write_reply_is_a_dict(self) -> None:
        client = self._client()
        with patch.object(client, "request", return_value=["not", "an", "object"]):
            with self.assertRaises(FitatuApiError) as ctx:
                client.get_recipe(7)
        self.assertIn("unexpected response shape", str(ctx.exception))

        with patch.object(client, "request", return_value=None):
            self.assertEqual(client.delete_product(7), {})


class AuthStoreTests(unittest.TestCase):
    def test_auth_context_reads_storage_state_like_payload(self) -> None:
        session_data = {
            "origins": [
                {
                    "localStorage": [
                        {"name": "token", "value": "bearer-from-storage"},
                        {"name": "refresh_token", "value": "refresh-from-storage"},
                        {
                            "name": "user",
                            "value": '{"id": 321, "locale": "pl_PL", "searchLocale": "pl_PL", "storageLocale": "pl_PL", "timezone": "Europe/Warsaw", "appVersion": "4.13.1", "searchUrls": ["https://pl-pl2.fitatu.com/search"]}',
                        },
                    ]
                }
            ]
        }

        auth = FitatuAuthContext.from_session_data(session_data)

        self.assertEqual(auth.bearer_token, "bearer-from-storage")
        self.assertEqual(auth.refresh_token, "refresh-from-storage")
        self.assertEqual(auth.fitatu_user_id, "321")
        self.assertEqual(auth.api_cluster, "pl-pl0")

    def test_token_store_roundtrip_and_clear(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            store = FitatuTokenStore(Path(tmpdir) / "tokens.json")

            store.save(bearer_token="token-a", refresh_token="token-b")
            loaded = store.load()
            self.assertEqual(loaded["bearer_token"], "token-a")
            self.assertEqual(loaded["refresh_token"], "token-b")

            store.clear()
            self.assertEqual(store.load(), {})

    def test_token_store_expands_home_for_reads_too(self) -> None:
        with tempfile.TemporaryDirectory() as home, patch.dict(os.environ, {"HOME": home, "USERPROFILE": home}):
            session = Path(home) / ".fitatu" / "session.json"
            session.parent.mkdir()
            session.write_text('{"fitatu_user_id": "1", "base_url": "https://de-de.fitatu.com/api"}', encoding="utf-8")
            store = FitatuTokenStore(Path("~/.fitatu/session.json"))

            store.save(bearer_token="b", refresh_token="r")

            self.assertEqual(store.load(), {"bearer_token": "b", "refresh_token": "r", "fitatu_user_id": "1"})
            self.assertEqual(json.loads(session.read_text(encoding="utf-8"))["base_url"], "https://de-de.fitatu.com/api")

    @unittest.skipIf(os.name == "nt", "POSIX permissions")
    def test_token_store_file_is_owner_only(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "tokens.json"
            path.write_text("{}", encoding="utf-8")
            path.chmod(0o644)

            FitatuTokenStore(path).save(bearer_token="a", refresh_token="b")

            self.assertEqual(path.stat().st_mode & 0o777, 0o600)

    def test_token_store_ignores_invalid_json(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "tokens.json"
            path.write_text("not-json", encoding="utf-8")
            store = FitatuTokenStore(path)

            self.assertEqual(store.load(), {})


class ValidationTests(unittest.TestCase):
    def test_validate_positive_int_accepts_positive(self) -> None:
        from fitatu_api._validation import validate_positive_int
        validate_positive_int(1, "limit")  # should not raise

    def test_validate_positive_int_rejects_zero(self) -> None:
        from fitatu_api._validation import validate_positive_int
        with self.assertRaises(ValueError):
            validate_positive_int(0, "limit")

    def test_validate_positive_int_rejects_negative(self) -> None:
        from fitatu_api._validation import validate_positive_int
        with self.assertRaises(ValueError):
            validate_positive_int(-5, "limit")

    def test_validate_non_negative_int_accepts_zero(self) -> None:
        from fitatu_api._validation import validate_non_negative_int
        validate_non_negative_int(0, "amount_ml")  # should not raise

    def test_as_date_accepts_dates_datetimes_and_iso_strings(self) -> None:
        from datetime import datetime

        from fitatu_api._validation import as_date

        self.assertEqual(as_date(TEST_DAY), TEST_DAY)
        self.assertEqual(as_date(datetime(2026, 4, 19, 23, 59)), TEST_DAY)
        self.assertEqual(as_date(" 2026-04-19 "), TEST_DAY)
        for bad in ("2026-13-01", "tomorrow", 20260419, None):
            with self.subTest(value=bad), self.assertRaisesRegex(FitatuValidationError, "target_date must be"):
                as_date(bad, "target_date")  # type: ignore[arg-type]

    def test_validate_non_negative_int_rejects_negative(self) -> None:
        from fitatu_api._validation import validate_non_negative_int
        with self.assertRaises(ValueError):
            validate_non_negative_int(-1, "amount_ml")

    def test_search_food_raises_on_invalid_limit(self) -> None:
        auth = FitatuAuthContext(bearer_token="tok")
        client = FitatuApiClient(auth=auth, retry_max_attempts=1, retry_base_delay_seconds=0.0)
        with self.assertRaises(ValueError):
            client.search_food("banan", limit=0)

    def test_water_set_day_raises_on_negative_ml(self) -> None:
        mock_client = Mock()
        module = WaterModule(mock_client)
        with self.assertRaises(ValueError):
            module.set_day("user-123", TEST_DAY, -1)


def _json_response(status: int, body: object) -> Mock:
    response = Mock(status_code=status, text=json.dumps(body), headers={})
    response.json.return_value = body
    return response


class LoginTests(unittest.TestCase):
    """Tests for FitatuApiClient.login() classmethod."""

    def _make_jwt(self, payload: dict) -> str:
        body = base64.urlsafe_b64encode(json.dumps(payload).encode()).rstrip(b"=").decode()
        return f"h.{body}.sig"

    def test_login_success_returns_client_with_auth(self) -> None:
        token = self._make_jwt({"user_id": "42"})
        response = _json_response(200, {"token": token, "refresh_token": "ref-tok"})

        with patch("fitatu_api.client.requests.Session.request", return_value=response):
            client = FitatuApiClient.login("user@example.com", "secret")

        self.assertEqual(client.auth.bearer_token, token)
        self.assertEqual(client.auth.refresh_token, "ref-tok")
        self.assertEqual(client.auth.fitatu_user_id, "42")

    def test_login_extracts_user_id_from_uid_field(self) -> None:
        response = _json_response(200, {"token": self._make_jwt({"uid": "99"})})

        with patch("fitatu_api.client.requests.Session.request", return_value=response):
            client = FitatuApiClient.login("u@e.com", "pw")

        self.assertEqual(client.auth.fitatu_user_id, "99")

    def test_login_raises_on_non_ok_response(self) -> None:
        response = Mock(status_code=401, text="Unauthorized", headers={})

        with patch("fitatu_api.client.requests.Session.request", return_value=response) as http:
            with self.assertRaises(FitatuAuthError) as ctx:
                FitatuApiClient.login("bad@example.com", "wrong")

        self.assertEqual(ctx.exception.status_code, 401)
        self.assertEqual(str(ctx.exception), "POST /login -> 401: the e-mail or password is wrong")
        http.assert_called_once()  # no refresh attempt

    def test_login_rejected_credentials_are_an_auth_error_that_points_to_import_session(self) -> None:
        # Recorded live (2026-10-13): a wrong or unknown e-mail or password, e.g. an Apple relay
        # address of an account that has no password.
        body = {"code": 422, "message": "Username or password are invalid"}

        with patch("fitatu_api.client.requests.Session.request", return_value=_json_response(422, body)):
            with self.assertRaises(FitatuAuthError) as ctx:
                FitatuApiClient.login("x@privaterelay.appleid.com", "guess")

        self.assertEqual((ctx.exception.status_code, json.loads(ctx.exception.body or "")), (422, body))
        self.assertIn("e-mail or password is wrong", str(ctx.exception))
        info = describe_error(ctx.exception)
        self.assertEqual(info["error_type"], "auth")
        self.assertIn("fitatu-api import-session", str(info["hint"]))
        self.assertNotIn("fitatu-api login", str(info["hint"]))

    def test_login_other_refusals_keep_their_error_type(self) -> None:
        # Recorded live (2026-10-13): an empty body, and a request without API-Key/API-Secret.
        cases = [
            (400, {"code": 400, "message": "Bad Request"}, "validation"),
            (403, {"code": 0, "message": "Invalid API-Key/API-Secret"}, "forbidden"),
        ]
        for status, body, error_type in cases:
            with self.subTest(status=status):
                with patch("fitatu_api.client.requests.Session.request", return_value=_json_response(status, body)):
                    with self.assertRaises(FitatuApiError) as ctx:
                        FitatuApiClient.login("me@example.com", "secret")
                self.assertNotIsInstance(ctx.exception, FitatuAuthError)
                self.assertEqual(ctx.exception.status_code, status)
                self.assertEqual(str(ctx.exception), f"POST /login -> {status}: {body['message']}")
                self.assertEqual(describe_error(ctx.exception)["error_type"], error_type)

    def test_login_keeps_fresh_tokens_over_an_existing_token_store(self) -> None:
        fresh = self._make_jwt({"id": 42})
        response = _json_response(200, {"token": fresh, "refresh_token": "fresh-refresh"})
        with tempfile.TemporaryDirectory() as tmpdir:
            store = Path(tmpdir) / "tokens.json"
            FitatuTokenStore(store).save(bearer_token="stale", refresh_token="stale-refresh")

            with patch("fitatu_api.client.requests.Session.request", return_value=response):
                client = FitatuApiClient.login("me@example.com", "secret", token_store_path=store)

            self.assertEqual(client.auth.bearer_token, fresh)
            self.assertEqual(client.auth.refresh_token, "fresh-refresh")
            self.assertEqual(FitatuTokenStore(store).load()["refresh_token"], "fresh-refresh")

    def test_login_wraps_network_errors(self) -> None:
        with patch("fitatu_api.client.requests.Session.request", side_effect=requests.ConnectionError("dns")) as http:
            with self.assertRaises(FitatuConnectionError):
                FitatuApiClient.login("me@example.com", "secret")
        http.assert_called_once()  # POST /login is not retried

    def test_login_raises_when_token_missing_from_response(self) -> None:
        with patch("fitatu_api.client.requests.Session.request", return_value=_json_response(200, {})):
            with self.assertRaises(FitatuApiError):
                FitatuApiClient.login("u@e.com", "pw")

    def test_login_posts_credentials_with_the_client_headers(self) -> None:
        response = _json_response(200, {"token": self._make_jwt({"user_id": "1"})})

        with patch("fitatu_api.client.requests.Session.request", return_value=response) as http:
            FitatuApiClient.login("u@e.com", "pw")

        call = http.call_args_list[0].kwargs
        self.assertEqual((call["method"], call["url"]), ("POST", "https://pl-pl.fitatu.com/api/login"))
        self.assertEqual(call["json"], {"_username": "u@e.com", "_password": "pw"})
        self.assertEqual(call["headers"]["api-key"], "FITATU-MOBILE-APP")
        self.assertEqual(call["headers"]["user-agent"], "Mozilla/5.0")
        self.assertNotIn("authorization", call["headers"])


def _jwt(payload: dict) -> str:
    body = base64.urlsafe_b64encode(json.dumps(payload, ensure_ascii=False).encode()).rstrip(b"=").decode()
    return f"h.{body}.sig"


class TokenLifetimeTests(unittest.TestCase):
    def test_jwt_payload_is_decoded_as_base64url(self) -> None:
        # Non-ASCII text and "~?>" put "-" and "_" into the payload, which plain base64 drops.
        token = _jwt({"id": 42, "name": "Zażółć gęślą jaźń ~?>"})
        self.assertRegex(token.split(".")[1], "[-_]")

        self.assertEqual(jwt_user_id(token), "42")

    def test_session_without_user_id_takes_it_from_the_token(self) -> None:
        auth = FitatuAuthContext.from_session_data({"bearer_token": _jwt({"id": 7}), "refresh_token": "r"})
        self.assertEqual(auth.fitatu_user_id, "7")
        explicit = FitatuAuthContext.from_session_data({"bearer_token": _jwt({"id": 7}), "fitatu_user_id": "9"})
        self.assertEqual(explicit.fitatu_user_id, "9")

    def test_snapshot_reports_when_the_bearer_expires(self) -> None:
        auth = FitatuAuthContext(bearer_token=_jwt({"id": 1, "exp": 1_800_000_000}))
        self.assertEqual(auth.snapshot()["bearer_expires_at"], "2027-01-15T08:00:00+00:00")
        self.assertIsNone(FitatuAuthContext(bearer_token="opaque").snapshot()["bearer_expires_at"])

    def test_unusable_exp_counts_as_unknown_expiry(self) -> None:
        for exp in (int(time.time() * 1000), float("inf"), float("nan")):
            with self.subTest(exp=exp):
                client = FitatuApiClient(auth=FitatuAuthContext(bearer_token=_jwt({"id": 1, "exp": exp})))
                self.assertIsNone(client.describe_auth_state()["bearer_expires_at"])
                with patch("fitatu_api.client.requests.Session.request", return_value=_json_response(200, {})) as http:
                    client.request("GET", "/ping")
                http.assert_called_once()

    def test_expiring_bearer_is_refreshed_before_the_call(self) -> None:
        expiring = _jwt({"id": 1, "exp": int(time.time()) + 30})
        fresh = _jwt({"id": 1, "exp": int(time.time()) + 3600})
        client = FitatuApiClient(auth=FitatuAuthContext(bearer_token=expiring, refresh_token="r"))
        replies = [_json_response(200, {"token": fresh, "refreshToken": "r2"}), _json_response(200, {"ok": True})]

        with patch("fitatu_api.client.requests.Session.request", side_effect=replies) as http:
            self.assertEqual(client.request("GET", "/ping"), {"ok": True})

        refresh_call, ping_call = http.call_args_list
        self.assertTrue(refresh_call.kwargs["url"].endswith("/token/refresh"))
        self.assertEqual(ping_call.kwargs["headers"]["authorization"], f"Bearer {fresh}")

    def test_a_refresh_that_fails_for_a_temporary_reason_is_not_reported_as_logged_out(self) -> None:
        cases = [
            (requests.ConnectionError("down"), FitatuConnectionError),
            (Mock(status_code=503, text="", headers={}), FitatuApiError),
            (Mock(status_code=429, text="", headers={}), FitatuRateLimitError),
        ]
        for refresh_reply, error_type in cases:
            with self.subTest(error=error_type.__name__):
                client = FitatuApiClient(auth=FitatuAuthContext(bearer_token="b", refresh_token="r"))
                expired = Mock(status_code=401, text='{"message": "Expired JWT Token"}', headers={})
                with patch("fitatu_api.client.requests.Session.request", side_effect=[expired, refresh_reply]):
                    with self.assertRaises(error_type) as ctx:
                        client.request("GET", "/x")
                self.assertNotIsInstance(ctx.exception, FitatuAuthError)
                self.assertIn("/token/refresh", str(ctx.exception))
                # The refresh token was not rejected, so the next call refreshes again.
                self.assertIsNone(client._rejected_refresh_token)

    def test_expiring_bearer_is_still_sent_when_the_early_refresh_fails(self) -> None:
        expiring = _jwt({"id": 1, "exp": int(time.time()) + 30})
        client = FitatuApiClient(auth=FitatuAuthContext(bearer_token=expiring, refresh_token="r"))
        replies = [Mock(status_code=503, text="", headers={}), _json_response(200, {"ok": True})]

        with patch("fitatu_api.client.requests.Session.request", side_effect=replies) as http:
            self.assertEqual(client.request("GET", "/ping"), {"ok": True})
        self.assertEqual(http.call_args.kwargs["headers"]["authorization"], f"Bearer {expiring}")

    def test_a_fresh_token_that_looks_expired_turns_refreshing_ahead_off_once(self) -> None:
        # The clock runs more than an hour fast: the token Fitatu just issued already looks expired.
        stale = _jwt({"id": 1, "exp": int(time.time()) - 600})
        client = FitatuApiClient(auth=FitatuAuthContext(bearer_token=stale, refresh_token="r"))
        replies = [_json_response(200, {"token": stale}), _json_response(200, {}), _json_response(200, {})]

        with self.assertLogs("fitatu_api.client", level="WARNING") as logs, patch(
            "fitatu_api.client.requests.Session.request", side_effect=replies
        ) as http:
            client.request("GET", "/a")
            client.request("GET", "/b")

        self.assertEqual([c.kwargs["url"].rsplit("/", 1)[-1] for c in http.call_args_list], ["refresh", "a", "b"])
        self.assertEqual([json.loads(r.getMessage())["event"] for r in logs.records], ["fitatu.auth.clock_skew"])
        # A 401 still refreshes.
        with patch("fitatu_api.client.requests.Session.request", side_effect=[_json_response(401, {}), *replies[:2]]) as http:
            client.request("GET", "/c")
        self.assertEqual([c.kwargs["url"].rsplit("/", 1)[-1] for c in http.call_args_list], ["c", "refresh", "c"])

    def test_no_second_refresh_when_another_call_already_refreshed(self) -> None:
        client = FitatuApiClient(auth=FitatuAuthContext(bearer_token="new", refresh_token="r"))
        with patch.object(client, "_refresh") as refresh:
            self.assertTrue(client._auto_refresh("old", "cid"))
        refresh.assert_not_called()


class RetryTests(unittest.TestCase):
    def _client(self) -> FitatuApiClient:
        auth = FitatuAuthContext(bearer_token="token", refresh_token="refresh-token")
        return FitatuApiClient(auth=auth, retry_max_attempts=3, retry_base_delay_seconds=0.0)

    def test_writes_are_retried_only_when_marked_idempotent(self) -> None:
        unavailable = Mock(status_code=503, text="", headers={})
        ok = _json_response(200, {"ok": True})
        client = self._client()
        with patch("fitatu_api.client.requests.Session.request", side_effect=[unavailable]) as http:
            with self.assertRaises(FitatuApiError):
                client.request("POST", "/diet-plan/1/days", json_data={})
        self.assertEqual(http.call_count, 1)

        with patch("fitatu_api.client.requests.Session.request", side_effect=[unavailable, ok]) as http:
            self.assertEqual(client.request("PUT", "/water/1/2026-10-13", json_data={}, idempotent=True), {"ok": True})
        self.assertEqual(http.call_count, 2)

    def test_long_retry_after_raises_instead_of_sleeping(self) -> None:
        limited = Mock(status_code=429, text="", headers={"retry-after": "120"})
        with patch("fitatu_api.client.requests.Session.request", return_value=limited) as http, patch(
            "fitatu_api.client.time.sleep"
        ) as sleep:
            with self.assertRaises(FitatuRateLimitError) as ctx:
                self._client().request("GET", "/ping")

        sleep.assert_not_called()
        self.assertEqual(http.call_count, 1)
        self.assertEqual(ctx.exception.retry_after, 120.0)
        self.assertIn("retry after 120 s", str(ctx.exception))

    def test_retries_and_their_outcome_are_logged_without_secrets(self) -> None:
        client = FitatuApiClient(auth=FitatuAuthContext(bearer_token="secret-bearer"), retry_base_delay_seconds=0.5)
        replies = [
            requests.ConnectionError("https://pl-pl.fitatu.com/api/search?phrase=private"),
            Mock(status_code=503, text="", headers={}),
            _json_response(200, {}),
        ]
        with self.assertLogs("fitatu_api.client", level="DEBUG") as logs, patch(
            "fitatu_api.client.requests.Session.request", side_effect=replies
        ), patch("fitatu_api.client.time.sleep") as sleep:
            client.request("GET", "/ping")
            with patch("fitatu_api.client.requests.Session.request", side_effect=requests.Timeout("t")):
                with self.assertRaises(FitatuConnectionError):
                    client.request("POST", "/ping", json_data={})  # a write: one attempt

        events = [json.loads(record.getMessage()) for record in logs.records]
        retries = [(e["attempt"], e["delay_seconds"], e["reason"]) for e in events if e["event"] == "fitatu.request.retry"]
        self.assertEqual(retries, [(1, 0.5, "network error: ConnectionError"), (2, 1.0, "status 503")])
        self.assertEqual([c.args for c in sleep.call_args_list], [(0.5,), (1.0,)])
        ok, error = (e for e in events if e["event"] in ("fitatu.request.ok", "fitatu.request.error"))
        self.assertEqual((ok["attempt"], error["attempt"], error["reason"]), (3, 1, "network error: Timeout"))
        self.assertTrue(isinstance(ok["elapsed_ms"], int) and isinstance(error["elapsed_ms"], int))
        self.assertNotIn("secret-bearer", "\n".join(logs.output))
        self.assertNotIn("private", "\n".join(logs.output))

    def test_a_request_that_failed_for_good_is_a_warning_and_a_4xx_only_debug(self) -> None:
        client = self._client()
        replies = [Mock(status_code=503, text="", headers={})] * 3 + [Mock(status_code=404, text="", headers={})]
        with self.assertLogs("fitatu_api.client", level="DEBUG") as logs, patch(
            "fitatu_api.client.requests.Session.request", side_effect=replies
        ):
            with self.assertRaises(FitatuApiError):
                client.request("GET", "/a")
            with self.assertRaises(FitatuNotFoundError):
                client.request("GET", "/b")  # e.g. a measurement that is not there, which callers expect
        errors = [(r.levelname, json.loads(r.getMessage())["status_code"]) for r in logs.records if "fitatu.request.error" in r.getMessage()]
        self.assertEqual(errors, [("WARNING", 503), ("DEBUG", 404)])

    def test_cloudflare_520_to_524_are_retried_like_502(self) -> None:
        for status in (520, 521, 522, 523, 524):
            with self.subTest(status=status), patch(
                "fitatu_api.client.requests.Session.request",
                side_effect=[Mock(status_code=status, text="", headers={}), _json_response(200, [])],
            ) as http:
                self.assertEqual(self._client().request("GET", "/ping"), [])
                self.assertEqual(http.call_count, 2)

    def test_short_retry_after_is_honoured(self) -> None:
        limited = Mock(status_code=429, text="", headers={"retry-after": "2"})
        with patch(
            "fitatu_api.client.requests.Session.request", side_effect=[limited, _json_response(200, [])]
        ), patch("fitatu_api.client.time.sleep") as sleep:
            self.assertEqual(self._client().request("GET", "/ping"), [])
        sleep.assert_called_once_with(2.0)

    def test_a_time_limit_skips_the_retries_that_would_end_past_it(self) -> None:
        client = self._client()
        limited = Mock(status_code=429, text="", headers={"retry-after": "20"})
        with patch("fitatu_api.client.requests.Session.request", return_value=limited) as http, patch(
            "fitatu_api.client.time.sleep"
        ) as sleep, client.time_limit(10), self.assertRaises(FitatuRateLimitError) as ctx:
            client.request("GET", "/ping")
        sleep.assert_not_called()
        self.assertEqual((http.call_count, ctx.exception.retry_after), (1, 20.0))
        # A retry whose wait fits is still made, and each request's timeout is cut to the time left.
        replies = [Mock(status_code=503, text="", headers={}), _json_response(200, [])]
        with patch("fitatu_api.client.requests.Session.request", side_effect=replies) as http:
            with client.time_limit(5):
                self.assertEqual(client.request("GET", "/ping"), [])
        self.assertTrue(all(0 < call.kwargs["timeout"] <= 5 for call in http.call_args_list) and http.call_count == 2)
        with patch("fitatu_api.client.requests.Session.request", return_value=_json_response(200, [])) as http:
            client.request("GET", "/ping")  # the limit ended with its block
        self.assertEqual(http.call_args.kwargs["timeout"], 25)

    def test_a_retry_that_would_have_almost_no_time_left_is_not_made(self) -> None:
        client = self._client()
        busy = Mock(status_code=503, text="", headers={"retry-after": "1"})
        with patch("fitatu_api.client.requests.Session.request", return_value=busy) as http, patch(
            "fitatu_api.client.time.sleep"
        ) as sleep, client.time_limit(2.5), self.assertRaises(FitatuApiError) as ctx:
            client.request("GET", "/ping")
        # 1 s of waiting would leave the retry 1.5 s: the 503 is reported, not the retry's timeout.
        sleep.assert_not_called()
        self.assertEqual((http.call_count, ctx.exception.status_code), (1, 503))

    def test_no_request_is_sent_once_the_time_limit_is_up(self) -> None:
        client = self._client()
        with patch("fitatu_api.client.requests.Session.request") as http, client.time_limit(0):
            with self.assertRaisesRegex(FitatuConnectionError, "GET /ping -> not sent: the time limit"):
                client.request("GET", "/ping")
        http.assert_not_called()

    def test_one_session_is_reused_and_closed(self) -> None:
        client = self._client()
        with patch.object(client._http, "request", return_value=_json_response(200, {})) as http:
            client.request("GET", "/a")
            client.request("GET", "/b")
        self.assertEqual(http.call_count, 2)
        with patch.object(client._http, "close") as close, client:
            pass
        close.assert_called_once()
