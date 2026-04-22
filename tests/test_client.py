from __future__ import annotations

import tempfile
import unittest
from copy import deepcopy
from datetime import date
from pathlib import Path
from unittest.mock import Mock, patch

import requests

from fitatu_api.api_client import (
    ActivitiesModule,
    AuthModule,
    CmsModule,
    DietPlanModule,
    FitatuApiClient,
    FitatuApiError,
    FitatuAuthContext,
    FitatuTokenStore,
    PlannerModule,
    ResourcesModule,
    UserSettingsModule,
    WaterModule,
)
from fitatu_api.fitatu_api import FitatuLibrary

TEST_DAY = date(2026, 4, 19)
TEST_USER_ID = "user-123"


class FakeClient:
    def __init__(
        self,
        day_payload: dict[str, object],
        *,
        search_results: dict[str, list[dict[str, object]]] | None = None,
        product_details: dict[str, dict[str, object]] | None = None,
        recipe_details: dict[str, dict[str, object]] | None = None,
    ) -> None:
        self.day_payload = deepcopy(day_payload)
        self.request_log: list[tuple[str, str]] = []
        self.last_sync_payload: dict[str, object] | None = None
        self.search_results = search_results or {}
        self.product_details = product_details or {}
        self.recipe_details = recipe_details or {}
        self.planner = PlannerModule(self)

    def request(self, method: str, path: str, **kwargs: object) -> object:
        self.request_log.append((method, path))
        expected_path = f"/diet-and-activity-plan/{TEST_USER_ID}/day/{TEST_DAY.isoformat()}"
        if method == "GET" and path == expected_path:
            return deepcopy(self.day_payload)
        raise AssertionError(f"Unexpected request: {method} {path} {kwargs}")

    def request_first_success(
        self,
        method: str,
        paths: list[str],
        json_data: dict[str, object] | None = None,
        **kwargs: object,
    ) -> object:
        self.request_log.append((method, paths[0]))
        if method == "DELETE":
            raise FitatuApiError("missing", status_code=404)
        if method != "POST":
            raise AssertionError(f"Unexpected method: {method}")
        if json_data is None:
            raise AssertionError("sync payload missing")
        self.last_sync_payload = deepcopy(json_data)
        day_key = TEST_DAY.isoformat()
        payload = json_data.get(day_key)
        if not isinstance(payload, dict):
            raise AssertionError("day payload missing")
        self.day_payload = deepcopy(payload)
        return {"status": "ok", "savedDay": day_key, "path": paths[0]}

    def search_food(self, phrase: str, **kwargs: object) -> list[dict[str, object]]:
        return deepcopy(self.search_results.get(phrase.lower(), []))

    def get_product_details(self, product_id: int | str) -> dict[str, object]:
        details = self.product_details.get(str(product_id))
        if details is None:
            raise FitatuApiError(f"missing product {product_id}", status_code=404)
        return deepcopy(details)

    def get_recipe(self, recipe_id: int | str) -> dict[str, object]:
        details = self.recipe_details.get(str(recipe_id))
        if details is None:
            raise FitatuApiError(f"missing recipe {recipe_id}", status_code=404)
        return deepcopy(details)


def _make_day_payload() -> dict[str, object]:
    return {
        "dietPlan": {
            "breakfast": {
                "items": [
                    {
                        "planDayDietItemId": "item-1",
                        "productId": 1001,
                        "measureId": 11,
                        "measureQuantity": 1.0,
                        "name": "Porridge",
                        "eaten": False,
                        "source": "API",
                    }
                ]
            }
        },
        "toiletItems": [],
        "note": None,
        "tagsIds": [],
    }


def _make_custom_recipe_like_payload() -> dict[str, object]:
    payload = _make_day_payload()
    payload["dietPlan"] = {
        "breakfast": {
            "items": [
                {
                    "planDayDietItemId": "recipe-custom-1",
                    "foodType": "CUSTOM_ITEM",
                    "name": "Recipe-like custom",
                    "measureId": 1,
                    "measureQuantity": 1.0,
                    "source": "API",
                }
            ]
        }
    }
    return payload


def _make_custom_add_payload() -> dict[str, object]:
    payload = _make_day_payload()
    payload["dietPlan"] = {
        "breakfast": {
            "items": [
                {
                    "planDayDietItemId": "custom-add-1",
                    "foodType": "CUSTOM_ITEM",
                    "name": "Manual custom",
                    "measureId": 1,
                    "measureQuantity": 100.0,
                    "source": "API",
                    "energy": 220,
                    "protein": 10,
                    "fat": 8,
                    "carbohydrate": 20,
                }
            ]
        }
    }
    return payload


def _make_mixed_removal_payload() -> dict[str, object]:
    return {
        "dietPlan": {
            "breakfast": {
                "items": [
                    {
                        "planDayDietItemId": "prod-1",
                        "foodType": "PRODUCT",
                        "productId": 1001,
                        "measureId": 1,
                        "measureQuantity": 1.0,
                        "name": "Milk",
                        "source": "API",
                    }
                ]
            },
            "lunch": {
                "items": [
                    {
                        "planDayDietItemId": "custom-add-1",
                        "foodType": "CUSTOM_ITEM",
                        "measureId": 1,
                        "measureQuantity": 150.0,
                        "name": "Custom add",
                        "source": "WEB",
                    }
                ]
            },
            "snack": {
                "items": [
                    {
                        "planDayDietItemId": "custom-recipe-1",
                        "foodType": "CUSTOM_ITEM",
                        "measureId": 1,
                        "measureQuantity": 1.0,
                        "name": "Custom recipe-like",
                        "source": "API",
                    }
                ]
            },
        },
        "toiletItems": [],
        "note": None,
        "tagsIds": [],
    }


def _make_recipe_details_payload() -> dict[str, object]:
    return {
        "id": 123456,
        "name": "Protein Pancakes",
        "photoUrl": "https://cdn.fitatu.example/recipes/123456.jpg",
        "nutritionalValues": {
            "energy": 410,
            "protein": 30,
            "fat": 12,
            "carbohydrate": 45,
        },
        "ingredientsServing": 2,
        "measureId": 39,
        "measureQuantity": 1,
    }


class PlannerTests(unittest.TestCase):
    def test_planner_helpers_read_and_find_items(self) -> None:
        client = FakeClient(_make_day_payload())

        meal = client.planner.get_meal(TEST_USER_ID, TEST_DAY, "breakfast")
        items = client.planner.list_meal_items(TEST_USER_ID, TEST_DAY, "breakfast")
        item = client.planner.find_meal_item(TEST_USER_ID, TEST_DAY, "breakfast", "porr")

        self.assertEqual(meal["items"][0]["name"], "Porridge")
        self.assertEqual(len(items), 1)
        self.assertIsNotNone(item)
        self.assertEqual(item["productId"], 1001)

    def test_update_day_item_changes_portion_and_name(self) -> None:
        client = FakeClient(_make_day_payload())
        result = client.planner.update_day_item(
            TEST_USER_ID,
            TEST_DAY,
            meal_type="breakfast",
            item_id="item-1",
            measure_quantity=2.5,
            measure_id=12,
            eaten=True,
            name="Updated porridge",
            source="WEB",
        )
        self.assertTrue(result["ok"])
        self.assertEqual(result["validation"]["measureQuantity"], True)
        updated_item = client.day_payload["dietPlan"]["breakfast"]["items"][0]
        self.assertEqual(updated_item["measureQuantity"], 2.5)
        self.assertEqual(updated_item["name"], "Updated porridge")

    def test_update_day_item_raises_when_item_missing(self) -> None:
        client = FakeClient(_make_day_payload())
        with self.assertRaises(FitatuApiError):
            client.planner.update_day_item(
                TEST_USER_ID,
                TEST_DAY,
                meal_type="breakfast",
                item_id="missing-item",
                measure_quantity=2.0,
            )

    def test_add_custom_item_to_day_meal_appends_manual_entry(self) -> None:
        client = FakeClient(_make_day_payload())

        result = client.planner.add_custom_item_to_day_meal(
            TEST_USER_ID,
            TEST_DAY,
            meal_type="breakfast",
            name="Fresh juice",
            calories=45,
            protein_g=1,
            fat_g=0,
            carbs_g=10,
        )

        self.assertTrue(result["ok"])
        self.assertEqual(result["addedItem"]["foodType"], "CUSTOM_ITEM")
        self.assertEqual(result["addedItem"]["name"], "Fresh juice")

    def test_add_recipe_to_day_meal_appends_recipe_entry(self) -> None:
        client = FakeClient(_make_day_payload())

        result = client.planner.add_recipe_to_day_meal(
            TEST_USER_ID,
            TEST_DAY,
            meal_type="breakfast",
            recipe_id=123456,
            food_type="RECIPE",
            measure_id=39,
            measure_quantity=1,
            ingredients_serving=1,
        )

        self.assertTrue(result["ok"])
        self.assertEqual(result["addedItem"]["foodType"], "RECIPE")
        self.assertEqual(result["addedItem"]["recipeId"], 123456)

        saved_item = client.day_payload["dietPlan"]["breakfast"]["items"][-1]
        self.assertEqual(saved_item["foodType"], "RECIPE")
        self.assertEqual(saved_item["recipeId"], 123456)
        self.assertTrue(bool(saved_item.get("planDayDietItemId")))

    def test_add_recipe_to_day_meal_hydrates_additional_fields_from_recipe_details(self) -> None:
        client = FakeClient(
            _make_day_payload(),
            recipe_details={"123456": _make_recipe_details_payload()},
        )

        result = client.planner.add_recipe_to_day_meal(
            TEST_USER_ID,
            TEST_DAY,
            meal_type="breakfast",
            recipe_id=123456,
            ingredients_serving=None,
        )

        self.assertTrue(result["ok"])
        self.assertEqual(result["recipeDetailsHydration"]["status"], "hydrated")
        self.assertEqual(result["addedItem"]["name"], "Protein Pancakes")
        self.assertEqual(result["addedItem"]["photo"], "https://cdn.fitatu.example/recipes/123456.jpg")
        self.assertEqual(result["addedItem"]["energy"], 410)
        self.assertEqual(result["addedItem"]["protein"], 30)
        self.assertEqual(result["addedItem"]["fat"], 12)
        self.assertEqual(result["addedItem"]["carbohydrate"], 45)
        self.assertEqual(result["addedItem"]["ingredientsServing"], 2)

    def test_add_recipe_to_day_meal_keeps_explicit_serving_fields_over_recipe_defaults(self) -> None:
        details = _make_recipe_details_payload()
        details["measureId"] = 99
        details["measureQuantity"] = 3
        details["ingredientsServing"] = 7
        client = FakeClient(
            _make_day_payload(),
            recipe_details={"123456": details},
        )

        result = client.planner.add_recipe_to_day_meal(
            TEST_USER_ID,
            TEST_DAY,
            meal_type="breakfast",
            recipe_id=123456,
            measure_id=39,
            measure_quantity=1,
            ingredients_serving=1,
        )

        self.assertTrue(result["ok"])
        self.assertEqual(result["addedItem"]["measureId"], 39)
        self.assertEqual(result["addedItem"]["measureQuantity"], 1)
        self.assertEqual(result["addedItem"]["ingredientsServing"], 1)

    def test_add_recipe_to_day_meal_continues_when_recipe_details_unavailable(self) -> None:
        client = FakeClient(_make_day_payload())

        result = client.planner.add_recipe_to_day_meal(
            TEST_USER_ID,
            TEST_DAY,
            meal_type="breakfast",
            recipe_id=999999,
        )

        self.assertTrue(result["ok"])
        self.assertEqual(result["recipeDetailsHydration"]["status"], "unavailable")
        self.assertEqual(result["addedItem"]["recipeId"], 999999)

    def test_add_recipe_to_day_meal_hydrated_fields_are_sent_in_sync_payload(self) -> None:
        client = FakeClient(
            _make_day_payload(),
            recipe_details={"123456": _make_recipe_details_payload()},
        )

        client.planner.add_recipe_to_day_meal(
            TEST_USER_ID,
            TEST_DAY,
            meal_type="breakfast",
            recipe_id=123456,
        )

        self.assertIsNotNone(client.last_sync_payload)
        assert client.last_sync_payload is not None
        synced_day = client.last_sync_payload[TEST_DAY.isoformat()]
        assert isinstance(synced_day, dict)
        synced_items = synced_day["dietPlan"]["breakfast"]["items"]
        synced_item = synced_items[-1]
        self.assertEqual(synced_item["name"], "Protein Pancakes")
        self.assertEqual(synced_item["energy"], 410)
        self.assertEqual(synced_item["protein"], 30)

    def test_compact_diet_item_for_sync_keeps_recipe_id(self) -> None:
        item = {
            "planDayDietItemId": "recipe-1",
            "foodType": "RECIPE",
            "recipeId": 777,
            "measureId": 39,
            "measureQuantity": 1,
            "ingredientsServing": 1,
            "source": "API",
            "name": "Recipe name",
            "energy": 100,
            "protein": 5,
            "fat": 3,
            "carbohydrate": 9,
        }

        compact = PlannerModule._compact_diet_item_for_sync(item)

        self.assertEqual(compact["foodType"], "RECIPE")
        self.assertEqual(compact["recipeId"], 777)
        self.assertEqual(compact["planDayDietItemId"], "recipe-1")

    def test_remove_day_item_falls_back_to_soft_delete_on_404(self) -> None:
        client = FakeClient(_make_day_payload())

        result = client.planner.remove_day_item(TEST_USER_ID, TEST_DAY, "breakfast", "item-1")

        self.assertTrue(result["ok"])
        self.assertEqual(result["cleanupMode"], "removed")
        self.assertNotIn("breakfast", client.day_payload["dietPlan"])

    def test_remove_day_item_delegates_to_auto_strategy(self) -> None:
        client = FakeClient(_make_day_payload())

        with patch.object(
            client.planner,
            "remove_day_item_with_strategy",
            return_value={"ok": True, "cleanupMode": "removed", "resolvedKind": "normal_item"},
        ) as remove_with_strategy:
            result = client.planner.remove_day_item(
                TEST_USER_ID,
                TEST_DAY,
                "breakfast",
                "item-1",
                delete_all_related_meals=True,
                use_aggressive_soft_delete=False,
            )

        self.assertTrue(result["ok"])
        remove_with_strategy.assert_called_once_with(
            TEST_USER_ID,
            TEST_DAY,
            "breakfast",
            "item-1",
            item_kind="auto",
            delete_all_related_meals=True,
            use_aggressive_soft_delete=False,
            max_soft_delete_retries=2,
        )

    def test_remove_day_item_via_snapshot_removes_item_from_payload(self) -> None:
        client = FakeClient(_make_day_payload())

        result = client.planner.remove_day_item_via_snapshot(TEST_USER_ID, TEST_DAY, "breakfast", "item-1")

        self.assertTrue(result["ok"])
        self.assertEqual(result["cleanupMode"], "removed")
        self.assertNotIn("breakfast", client.day_payload["dietPlan"])

    def test_classify_day_item_for_removal_distinguishes_kinds(self) -> None:
        product_client = FakeClient(_make_day_payload())
        custom_add_client = FakeClient(_make_custom_add_payload())
        custom_recipe_client = FakeClient(_make_custom_recipe_like_payload())

        product_kind = product_client.planner.classify_day_item_for_removal(
            TEST_USER_ID,
            TEST_DAY,
            "breakfast",
            "item-1",
        )
        custom_add_kind = custom_add_client.planner.classify_day_item_for_removal(
            TEST_USER_ID,
            TEST_DAY,
            "breakfast",
            "custom-add-1",
        )
        custom_recipe_kind = custom_recipe_client.planner.classify_day_item_for_removal(
            TEST_USER_ID,
            TEST_DAY,
            "breakfast",
            "recipe-custom-1",
        )

        self.assertEqual(product_kind["resolvedKind"], "normal_item")
        self.assertEqual(custom_add_kind["resolvedKind"], "custom_add_item")
        self.assertEqual(custom_recipe_kind["resolvedKind"], "custom_recipe_item")

    def test_remove_day_item_with_strategy_for_custom_recipe_prefers_soft_delete(self) -> None:
        client = FakeClient(_make_custom_recipe_like_payload())

        with patch.object(
            client.planner,
            "soft_remove_day_item_via_snapshot",
            return_value={"ok": True, "cleanupMode": "deleted_at"},
        ) as soft_remove, patch.object(
            client.planner,
            "remove_day_item_via_snapshot",
            return_value={"ok": True, "cleanupMode": "removed"},
        ) as snapshot_remove:
            result = client.planner.remove_day_item_with_strategy(
                TEST_USER_ID,
                TEST_DAY,
                "breakfast",
                "recipe-custom-1",
                item_kind="custom_recipe_item",
                max_soft_delete_retries=2,
            )

        self.assertTrue(result["ok"])
        self.assertEqual(result["resolvedKind"], "custom_recipe_item")
        self.assertEqual(result["attempts"][0]["step"], "soft_deleted_at_retry_1")
        self.assertEqual(soft_remove.call_count, 1)
        snapshot_remove.assert_not_called()

    def test_remove_day_items_by_kind_filters_targets_and_uses_resolved_strategy(self) -> None:
        client = FakeClient(_make_mixed_removal_payload())

        with patch.object(
            client.planner,
            "remove_day_item_with_strategy",
            side_effect=[
                {"ok": True, "cleanupMode": "removed"},
            ],
        ) as remove_with_strategy:
            result = client.planner.remove_day_items_by_kind(
                TEST_USER_ID,
                TEST_DAY,
                item_kind="normal_item",
            )

        self.assertTrue(result["ok"])
        self.assertEqual(result["targetedCount"], 1)
        self.assertEqual(result["removedCount"], 1)
        remove_with_strategy.assert_called_once_with(
            TEST_USER_ID,
            TEST_DAY,
            "breakfast",
            "prod-1",
            item_kind="normal_item",
            delete_all_related_meals=False,
            use_aggressive_soft_delete=True,
            max_soft_delete_retries=2,
        )

    def test_remove_day_items_by_kind_supports_meal_scope(self) -> None:
        client = FakeClient(_make_mixed_removal_payload())

        with patch.object(
            client.planner,
            "remove_day_item_with_strategy",
            return_value={"ok": True, "cleanupMode": "deleted_at"},
        ) as remove_with_strategy:
            result = client.planner.remove_day_items_by_kind(
                TEST_USER_ID,
                TEST_DAY,
                item_kind="custom_add_item",
                meal_key="lunch",
            )

        self.assertTrue(result["ok"])
        self.assertEqual(result["targetedCount"], 1)
        self.assertEqual(result["meal"], "lunch")
        remove_with_strategy.assert_called_once_with(
            TEST_USER_ID,
            TEST_DAY,
            "lunch",
            "custom-add-1",
            item_kind="custom_add_item",
            delete_all_related_meals=False,
            use_aggressive_soft_delete=True,
            max_soft_delete_retries=2,
        )

    def test_quick_add_form_with_fallback_uses_search_add_when_endpoint_missing(self) -> None:
        client = FakeClient(_make_day_payload())
        search_item = {"id": 55, "name": "Banan", "measureId": 7}

        with patch.object(
            client.planner,
            "quick_add_form",
            side_effect=FitatuApiError("missing", status_code=404),
        ), patch.object(
            client,
            "search_food",
            return_value=[search_item],
            create=True,
        ):
            result = client.planner.quick_add_form_with_fallback(
                {
                    "name": "banan",
                    "mealType": "breakfast",
                    "mealDate": TEST_DAY.isoformat(),
                    "userId": TEST_USER_ID,
                    "measureQuantity": 2,
                }
            )

        self.assertEqual(result["mode"], "fallback_search_add")
        self.assertTrue(result["result"]["ok"])
        self.assertEqual(result["result"]["searchItem"]["id"], 55)

    def test_add_search_result_to_day_meal_resolves_direct_measure_unit(self) -> None:
        client = FakeClient(
            _make_day_payload(),
            search_results={
                "jogurt": [
                    {
                        "id": 24881725,
                        "foodId": 24881725,
                        "name": "Jogurt naturalny",
                        "measure": {"defaultMeasureId": 2, "measureName": "opakowanie"},
                    }
                ]
            },
            product_details={
                "24881725": {
                    "id": 24881725,
                    "measures": [
                        {"id": 1, "name": "g", "weightPerUnit": 1},
                        {"id": 2, "name": "opakowanie", "weightPerUnit": 150},
                    ],
                    "simpleMeasures": [
                        {"id": 1, "portion": 100, "capacity": 100, "isLiquid": False},
                        {"id": 2, "portion": 1, "capacity": 150, "isLiquid": False},
                    ],
                }
            },
        )

        result = client.planner.add_search_result_to_day_meal(
            TEST_USER_ID,
            TEST_DAY,
            meal_type="breakfast",
            phrase="jogurt",
            measure_unit="g",
            measure_amount=200,
        )

        self.assertTrue(result["ok"])
        self.assertEqual(result["measureResolution"]["measureId"], 1)
        self.assertEqual(result["measureResolution"]["measureQuantity"], 200)
        self.assertEqual(result["measureResolution"]["strategy"], "direct_unit_match")

    def test_add_search_result_to_day_meal_converts_amount_when_unit_missing(self) -> None:
        client = FakeClient(
            _make_day_payload(),
            search_results={
                "jogurt": [
                    {
                        "id": 24881725,
                        "foodId": 24881725,
                        "name": "Jogurt naturalny",
                        "measure": {"defaultMeasureId": 2, "measureName": "opakowanie"},
                    }
                ]
            },
            product_details={
                "24881725": {
                    "id": 24881725,
                    "measures": [
                        {"id": 2, "name": "opakowanie", "weightPerUnit": 150},
                    ],
                    "simpleMeasures": [
                        {"id": 2, "portion": 1, "capacity": 150, "isLiquid": False},
                    ],
                }
            },
        )

        result = client.planner.add_search_result_to_day_meal(
            TEST_USER_ID,
            TEST_DAY,
            meal_type="breakfast",
            phrase="jogurt",
            measure_unit="g",
            measure_amount=300,
            strict_measure=False,
        )

        self.assertTrue(result["ok"])
        self.assertEqual(result["measureResolution"]["measureId"], 2)
        self.assertAlmostEqual(float(result["measureResolution"]["measureQuantity"]), 2.0)
        self.assertEqual(result["measureResolution"]["strategy"], "converted_from_weight")

    def test_add_search_result_to_day_meal_raises_when_unit_unresolvable_in_strict_mode(self) -> None:
        client = FakeClient(
            _make_day_payload(),
            search_results={
                "jogurt": [
                    {
                        "id": 24881725,
                        "foodId": 24881725,
                        "name": "Jogurt naturalny",
                        "measure": {"defaultMeasureId": 2, "measureName": "opakowanie"},
                    }
                ]
            },
            product_details={
                "24881725": {
                    "id": 24881725,
                    "measures": [
                        {"id": 2, "name": "opakowanie", "weightPerUnit": 0},
                    ],
                    "simpleMeasures": [],
                }
            },
        )

        with self.assertRaises(FitatuApiError):
            client.planner.add_search_result_to_day_meal(
                TEST_USER_ID,
                TEST_DAY,
                meal_type="breakfast",
                phrase="jogurt",
                measure_unit="g",
                measure_amount=150,
                strict_measure=True,
            )

    def test_route_variant_helpers_call_first_success(self) -> None:
        client = Mock()
        client.auth.fitatu_user_id = TEST_USER_ID
        client.request_first_success.return_value = {"status": "ok"}
        module = PlannerModule(client)

        module.get_product_for_meal("10", "breakfast", TEST_DAY)
        module.send_changes({"items": []})
        module.add_day_items(TEST_USER_ID, TEST_DAY, [{"id": 1}])
        module.sync_single_day(TEST_USER_ID, TEST_DAY, {"dietPlan": {}})
        module.remove_activity_day_item(TEST_USER_ID, TEST_DAY, "act-1")

        self.assertEqual(client.request_first_success.call_count, 5)


class FacadeTests(unittest.TestCase):
    def test_fitatu_api_wrapper_delegates_to_api_client(self) -> None:
        class StubPlanner:
            def add_product_to_day_meal(self, *args: object, **kwargs: object) -> dict[str, object]:
                return {"ok": True, "args": args, "kwargs": kwargs}

            def add_recipe_to_day_meal(self, *args: object, **kwargs: object) -> dict[str, object]:
                return {"ok": True, "args": args, "kwargs": kwargs}

            def add_search_result_to_day_meal(
                self, *args: object, **kwargs: object
            ) -> dict[str, object]:
                return {"ok": True, "args": args, "kwargs": kwargs}

            def update_day_item(self, *args: object, **kwargs: object) -> dict[str, object]:
                return {"ok": True, "args": args, "kwargs": kwargs}

        class StubClient:
            def __init__(self, auth: object) -> None:
                self.auth = auth
                self.planner = StubPlanner()

        class StubAuth:
            fitatu_user_id = "user-123"

        with patch(
            "fitatu_api.facade.FitatuAuthContext.from_session_data",
            return_value=StubAuth(),
        ), patch(
            "fitatu_api.facade.FitatuApiClient",
            StubClient,
        ):
            lib = FitatuLibrary(session_data={}, headless=True)
            result = lib.update_day_item_via_api(
                target_date=TEST_DAY,
                meal_key="breakfast",
                item_id="item-1",
                measure_quantity=3.0,
            )
        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["result"]["kwargs"]["measure_quantity"], 3.0)

    def test_fitatu_api_add_recipe_to_day_meal_via_api_delegates_to_planner(self) -> None:
        class StubPlanner:
            def add_recipe_to_day_meal(self, *args: object, **kwargs: object) -> dict[str, object]:
                return {"ok": True, "args": args, "kwargs": kwargs}

        class StubClient:
            def __init__(self, auth: object) -> None:
                self.auth = auth
                self.planner = StubPlanner()

        class StubAuth:
            fitatu_user_id = "user-123"

        with patch(
            "fitatu_api.facade.FitatuAuthContext.from_session_data",
            return_value=StubAuth(),
        ), patch(
            "fitatu_api.facade.FitatuApiClient",
            StubClient,
        ):
            lib = FitatuLibrary(session_data={}, headless=True)
            result = lib.add_recipe_to_day_meal_via_api(
                target_date=TEST_DAY,
                meal_key="breakfast",
                recipe_id=999,
                food_type="RECIPE",
                measure_id=39,
                measure_quantity=1,
                ingredients_serving=1,
            )

        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["result"]["kwargs"]["recipe_id"], 999)
        self.assertEqual(result["result"]["kwargs"]["food_type"], "RECIPE")
        self.assertEqual(result["result"]["kwargs"]["hydrate_from_recipe_details"], True)

    def test_fitatu_api_add_recipe_to_day_meal_via_api_forwards_hydration_toggle(self) -> None:
        class StubPlanner:
            def add_recipe_to_day_meal(self, *args: object, **kwargs: object) -> dict[str, object]:
                return {"ok": True, "args": args, "kwargs": kwargs}

        class StubClient:
            def __init__(self, auth: object) -> None:
                self.auth = auth
                self.planner = StubPlanner()

        class StubAuth:
            fitatu_user_id = "user-123"

        with patch(
            "fitatu_api.facade.FitatuAuthContext.from_session_data",
            return_value=StubAuth(),
        ), patch(
            "fitatu_api.facade.FitatuApiClient",
            StubClient,
        ):
            lib = FitatuLibrary(session_data={}, headless=True)
            result = lib.add_recipe_to_day_meal_via_api(
                target_date=TEST_DAY,
                meal_key="breakfast",
                recipe_id=999,
                hydrate_from_recipe_details=False,
            )

        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["result"]["kwargs"]["hydrate_from_recipe_details"], False)

    def test_fitatu_api_preserves_error_details(self) -> None:
        class StubPlanner:
            def update_day_item(self, *args: object, **kwargs: object) -> dict[str, object]:
                raise FitatuApiError("boom", status_code=503, body="down")

        class StubClient:
            def __init__(self, auth: object) -> None:
                self.auth = auth
                self.planner = StubPlanner()

        class StubAuth:
            fitatu_user_id = "user-123"

        with patch(
            "fitatu_api.facade.FitatuAuthContext.from_session_data",
            return_value=StubAuth(),
        ), patch(
            "fitatu_api.facade.FitatuApiClient",
            StubClient,
        ):
            lib = FitatuLibrary(session_data={}, headless=True)
            result = lib.update_day_item_via_api(
                target_date=TEST_DAY,
                meal_key="breakfast",
                item_id="item-1",
            )
        self.assertEqual(result["status_code"], 503)
        self.assertEqual(result["body"], "down")

    def test_fitatu_api_remove_day_item_via_api_delegates_to_planner(self) -> None:
        class StubPlanner:
            def remove_day_item(self, *args: object, **kwargs: object) -> dict[str, object]:
                return {"ok": True, "args": args, "kwargs": kwargs}

        class StubClient:
            def __init__(self, auth: object) -> None:
                self.auth = auth
                self.planner = StubPlanner()

        class StubAuth:
            fitatu_user_id = "user-123"

        with patch(
            "fitatu_api.facade.FitatuAuthContext.from_session_data",
            return_value=StubAuth(),
        ), patch(
            "fitatu_api.facade.FitatuApiClient",
            StubClient,
        ):
            lib = FitatuLibrary(session_data={}, headless=True)
            result = lib.remove_day_item_via_api(
                target_date=TEST_DAY,
                meal_key="breakfast",
                item_id="item-1",
                delete_all_related_meals=True,
            )

        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["result"]["kwargs"]["delete_all_related_meals"], True)

    def test_fitatu_api_remove_day_item_with_strategy_via_api_delegates_to_planner(self) -> None:
        class StubPlanner:
            def remove_day_item_with_strategy(self, *args: object, **kwargs: object) -> dict[str, object]:
                return {"ok": True, "args": args, "kwargs": kwargs}

        class StubClient:
            def __init__(self, auth: object) -> None:
                self.auth = auth
                self.planner = StubPlanner()

        class StubAuth:
            fitatu_user_id = "user-123"

        with patch(
            "fitatu_api.facade.FitatuAuthContext.from_session_data",
            return_value=StubAuth(),
        ), patch(
            "fitatu_api.facade.FitatuApiClient",
            StubClient,
        ):
            lib = FitatuLibrary(session_data={}, headless=True)
            result = lib.remove_day_item_with_strategy_via_api(
                target_date=TEST_DAY,
                meal_key="breakfast",
                item_id="item-1",
                item_kind="custom_recipe_item",
                max_soft_delete_retries=3,
            )

        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["result"]["kwargs"]["item_kind"], "custom_recipe_item")
        self.assertEqual(result["result"]["kwargs"]["max_soft_delete_retries"], 3)

    def test_fitatu_api_remove_day_items_by_kind_via_api_delegates_to_planner(self) -> None:
        class StubPlanner:
            def remove_day_items_by_kind(self, *args: object, **kwargs: object) -> dict[str, object]:
                return {"ok": True, "args": args, "kwargs": kwargs}

        class StubClient:
            def __init__(self, auth: object) -> None:
                self.auth = auth
                self.planner = StubPlanner()

        class StubAuth:
            fitatu_user_id = "user-123"

        with patch(
            "fitatu_api.facade.FitatuAuthContext.from_session_data",
            return_value=StubAuth(),
        ), patch(
            "fitatu_api.facade.FitatuApiClient",
            StubClient,
        ):
            lib = FitatuLibrary(session_data={}, headless=True)
            result = lib.remove_day_items_by_kind_via_api(
                target_date=TEST_DAY,
                item_kind="normal_item",
                meal_key="breakfast",
                max_items=3,
            )

        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["result"]["kwargs"]["item_kind"], "normal_item")
        self.assertEqual(result["result"]["kwargs"]["meal_key"], "breakfast")
        self.assertEqual(result["result"]["kwargs"]["max_items"], 3)

    def test_fitatu_api_move_day_item_via_api_delegates_to_planner(self) -> None:
        class StubPlanner:
            def move_day_item(self, *args: object, **kwargs: object) -> dict[str, object]:
                return {"ok": True, "args": args, "kwargs": kwargs}

        class StubClient:
            def __init__(self, auth: object) -> None:
                self.auth = auth
                self.planner = StubPlanner()

        class StubAuth:
            fitatu_user_id = "user-123"

        with patch(
            "fitatu_api.facade.FitatuAuthContext.from_session_data",
            return_value=StubAuth(),
        ), patch(
            "fitatu_api.facade.FitatuApiClient",
            StubClient,
        ):
            lib = FitatuLibrary(session_data={}, headless=True)
            result = lib.move_day_item_via_api(
                from_date=TEST_DAY,
                from_meal_key="breakfast",
                item_id="item-1",
                to_meal_key="lunch",
                synchronous=True,
            )

        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["result"]["kwargs"]["from_meal_type"], "breakfast")
        self.assertEqual(result["result"]["kwargs"]["to_meal_type"], "lunch")
        self.assertEqual(result["result"]["kwargs"]["synchronous"], True)

    def test_fitatu_api_replace_day_item_with_custom_item_via_api_delegates_to_planner(self) -> None:
        class StubPlanner:
            def replace_day_item_with_custom_item(self, *args: object, **kwargs: object) -> dict[str, object]:
                return {"ok": True, "args": args, "kwargs": kwargs}

        class StubClient:
            def __init__(self, auth: object) -> None:
                self.auth = auth
                self.planner = StubPlanner()

        class StubAuth:
            fitatu_user_id = "user-123"

        with patch(
            "fitatu_api.facade.FitatuAuthContext.from_session_data",
            return_value=StubAuth(),
        ), patch(
            "fitatu_api.facade.FitatuApiClient",
            StubClient,
        ):
            lib = FitatuLibrary(session_data={}, headless=True)
            result = lib.replace_day_item_with_custom_item_via_api(
                target_date=TEST_DAY,
                meal_key="breakfast",
                item_id="item-1",
                name="Updated",
                calories=100,
                protein_g=10,
                fat_g=2,
                carbs_g=12,
            )

        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["result"]["kwargs"]["meal_type"], "breakfast")
        self.assertEqual(result["result"]["kwargs"]["name"], "Updated")
        self.assertEqual(result["result"]["kwargs"]["synchronous"], True)


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
            result = client.search_user_food("user-123", "Meal", TEST_DAY, page=2, limit=10)

        self.assertEqual(result[0]["foodId"], 1)
        req.assert_called_once_with(
            "GET",
            "/search/food/user/user-123",
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
            {"foodId": 1, "name": "Meal", "brand": "Provider", "energy": 100},
            {"foodId": 2, "name": "Meal", "brand": "Provider", "energy": 100},
            {"foodId": 3, "name": "Meal", "brand": "Other", "energy": 100},
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
            )

        self.assertEqual(result["ok"], True)
        self.assertEqual(result["matchedCount"], 2)
        self.assertEqual(result["deletedProductIds"], ["2"])
        delete_product.assert_called_once_with(2)

    def test_cleanup_duplicate_user_products_accepts_custom_predicate(self) -> None:
        client = self._make_client()
        products = [
            {"foodId": 1, "name": "Meal", "brand": "A", "energy": 100},
            {"foodId": 2, "name": "Meal", "brand": "B", "energy": 200},
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
            )

        self.assertEqual(result["matchedCount"], 1)
        self.assertEqual(result["deletedCount"], 0)
        delete_product.assert_not_called()

    def test_request_maps_network_errors_to_fitatu_api_error(self) -> None:
        client = self._make_client()
        with patch(
            "fitatu_api.client.requests.request",
            side_effect=requests.RequestException("boom"),
        ):
            with self.assertRaises(FitatuApiError):
                client.request("GET", "/ping")

    def test_request_retries_get_on_429_then_succeeds(self) -> None:
        client = self._make_client()
        first = Mock(status_code=429, text="rate limited", headers={"retry-after": "0"})
        second = Mock(status_code=200, text='{"ok": true}', headers={})
        second.json.return_value = {"ok": True}
        with patch(
            "fitatu_api.client.requests.request",
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
            "fitatu_api.client.requests.request",
            side_effect=[first, second],
        ) as req, patch.object(
            client,
            "refresh_access_token",
            return_value={"status": "ok", "token": "fresh-token"},
        ) as refresh:
            result = client.request("GET", "/ping")

        self.assertEqual(result["ok"], True)
        self.assertEqual(req.call_count, 2)
        refresh.assert_called_once()

    def test_request_returns_plain_text_when_json_decode_fails(self) -> None:
        client = self._make_client()
        response = Mock(status_code=200, text="plain-text-response", headers={})
        response.json.side_effect = ValueError("not-json")

        with patch("fitatu_api.client.requests.request", return_value=response):
            result = client.request("GET", "/ping")

        self.assertEqual(result, "plain-text-response")

    def test_request_returns_none_for_empty_body(self) -> None:
        client = self._make_client()
        response = Mock(status_code=204, text="", headers={})

        with patch("fitatu_api.client.requests.request", return_value=response):
            result = client.request("DELETE", "/ping")

        self.assertIsNone(result)

    def test_request_first_success_falls_back_from_404(self) -> None:
        client = self._make_client()

        with patch.object(
            client,
            "request",
            side_effect=[
                FitatuApiError("missing", status_code=404),
                {"ok": True},
            ],
        ) as req:
            result = client.request_first_success("GET", ["/missing", "/working"])

        self.assertEqual(result["ok"], True)
        self.assertEqual(req.call_count, 2)

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

    def test_get_product_details_falls_back_to_route_variant(self) -> None:
        client = self._make_client()

        with patch.object(
            client,
            "request",
            side_effect=[
                FitatuApiError("missing", status_code=404),
                {"id": 321, "name": "Product"},
            ],
        ) as req:
            result = client.get_product_details(321)

        self.assertEqual(result["id"], 321)
        self.assertEqual(req.call_count, 2)

    def test_refresh_persists_tokens_to_store(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            store_path = Path(tmpdir) / "fitatu_tokens.json"
            client = self._make_client(token_store_path=store_path)
            with patch.object(
                client,
                "request",
                return_value={"access_token": "new-token", "refresh_token": "new-refresh"},
            ):
                result = client.refresh_access_token()
            self.assertEqual(result["status"], "ok")
            self.assertEqual(client.auth.refresh_token, "new-refresh")
            self.assertIn("new-token", store_path.read_text(encoding="utf-8"))
            self.assertIn("new-refresh", store_path.read_text(encoding="utf-8"))

    def test_refresh_falls_back_to_alternate_payload_keys(self) -> None:
        client = self._make_client()
        with patch.object(
            client,
            "request",
            side_effect=[
                FitatuApiError("bad refresh payload", status_code=400, body="bad"),
                {"token": "new-token", "refreshToken": "new-refresh"},
            ],
        ) as request:
            result = client.refresh_access_token()

        self.assertEqual(result["status"], "ok")
        self.assertEqual(client.auth.bearer_token, "new-token")
        self.assertEqual(client.auth.refresh_token, "new-refresh")
        self.assertEqual(request.call_count, 2)
        self.assertEqual(request.call_args_list[0].kwargs["json_data"], {"refresh_token": "refresh-token"})
        self.assertEqual(request.call_args_list[1].kwargs["json_data"], {"refreshToken": "refresh-token"})

    def test_refresh_returns_error_when_refresh_token_missing(self) -> None:
        client = self._make_client()
        client.auth.refresh_token = None

        result = client.refresh_access_token()

        self.assertEqual(result["status"], "error")
        self.assertIn("missing refresh_token", result["message"])

    def test_refresh_returns_error_when_response_has_no_token(self) -> None:
        client = self._make_client()

        with patch.object(client, "request", return_value={"status": "ok"}):
            result = client.refresh_access_token()

        self.assertEqual(result["status"], "error")
        self.assertIn("token not found", result["message"])

    def test_clear_auth_clears_tokens_and_store(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            store_path = Path(tmpdir) / "fitatu_tokens.json"
            client = self._make_client(token_store_path=store_path)
            store_path.write_text('{"bearer_token":"a","refresh_token":"b"}', encoding="utf-8")

            client.clear_auth()

            self.assertIsNone(client.auth.bearer_token)
            self.assertIsNone(client.auth.refresh_token)
            self.assertEqual(client.lifecycle_state, "relogin_required")
            self.assertFalse(store_path.exists())

    def test_reauthenticate_returns_refresh_success(self) -> None:
        client = self._make_client()

        with patch.object(client, "refresh_access_token", return_value={"status": "ok", "token": "fresh"}):
            result = client.reauthenticate()

        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["mode"], "refresh")

    def test_reauthenticate_uses_relogin_callback_after_refresh_failure(self) -> None:
        client = self._make_client()

        with patch.object(
            client,
            "refresh_access_token",
            return_value={"status": "error", "message": "expired"},
        ):
            result = client.reauthenticate(
                relogin_callback=lambda auth: {
                    "bearer_token": "new-bearer",
                    "refresh_token": "new-refresh",
                }
            )

        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["mode"], "relogin")
        self.assertEqual(client.auth.bearer_token, "new-bearer")

    def test_reauthenticate_rolls_back_on_failure(self) -> None:
        client = self._make_client()
        original_bearer = client.auth.bearer_token
        original_refresh = client.auth.refresh_token

        with patch.object(
            client,
            "refresh_access_token",
            return_value={"status": "error", "message": "expired"},
        ):
            result = client.reauthenticate(
                relogin_callback=lambda auth: {"note": "no tokens here"},
                rollback_on_failure=True,
            )

        self.assertEqual(result["status"], "error")
        self.assertTrue(result["rollback_applied"])
        self.assertEqual(client.auth.bearer_token, original_bearer)
        self.assertEqual(client.auth.refresh_token, original_refresh)

    def test_headers_and_url_helpers_include_expected_values(self) -> None:
        client = self._make_client(base_url="https://example.com/api")

        headers = client._headers(include_auth=True)

        self.assertIn("authorization", headers)
        self.assertEqual(client._url("/ping"), "https://example.com/api/ping")
        self.assertEqual(client._url("ping"), "https://example.com/api/ping")
        self.assertEqual(client._url("https://other.test/x"), "https://other.test/x")

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
        self.assertEqual(restored.api_cluster, "cluster")
        self.assertEqual(restored.fitatu_user_id, "user-123")

    def test_management_report_includes_session_and_modules(self) -> None:
        client = self._make_client()
        report = client.management_report()
        self.assertEqual(report["management_report_schema_version"], "1.0")
        self.assertIn("planner", report["modules"])
        self.assertNotIn("bearer_token", report["session_data"])

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

    def test_probe_known_endpoints_collects_statuses(self) -> None:
        client = self._make_client()
        side_effect = [
            {"ok": True},
            {"ok": True},
            FitatuApiError("forbidden", status_code=403),
            {"ok": True},
            {"ok": True},
            {"ok": True},
            {"ok": True},
            {"ok": True},
        ]
        with patch.object(client, "request", side_effect=side_effect):
            result = client.probe_known_endpoints("user-123", TEST_DAY)
        self.assertEqual(len(result), 8)
        self.assertEqual(result[2]["ok"], False)
        self.assertEqual(result[2]["status"], 403)

    def test_package_root_exports_main_symbols(self) -> None:
        import fitatu_api

        self.assertIs(fitatu_api.FitatuApiClient, FitatuApiClient)
        self.assertIs(fitatu_api.FitatuLibrary, FitatuLibrary)
        self.assertIsInstance(fitatu_api.__version__, str)

    def test_operational_store_records_and_reads_events(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            store_path = Path(tmpdir) / "ops.sqlite"
            client = self._make_client(operational_store_path=store_path)

            with patch(
                "fitatu_api.client.requests.request",
                side_effect=requests.RequestException("boom"),
            ):
                with self.assertRaises(FitatuApiError):
                    client.request("GET", "/ping")

            self.assertGreater(client.management_report()["operational_event_count"], 0)
            assert client.operational_store is not None
            events = client.operational_store.list_recent_events()
            self.assertGreaterEqual(len(events), 1)
            # Close explicitly so SQLite releases the file before TemporaryDirectory cleanup (Windows)
            client.close()

    def test_close_closes_operational_store(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            store_path = Path(tmpdir) / "ops.sqlite"
            client = self._make_client(operational_store_path=store_path)
            assert client.operational_store is not None

            client.close()

            self.assertIsNone(client.operational_store._connection)


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
        self.assertEqual(auth.api_cluster, "pl-pl2")

    def test_token_store_roundtrip_and_clear(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            store = FitatuTokenStore(Path(tmpdir) / "tokens.json")

            store.save(bearer_token="token-a", refresh_token="token-b")
            loaded = store.load()
            self.assertEqual(loaded["bearer_token"], "token-a")
            self.assertEqual(loaded["refresh_token"], "token-b")

            store.clear()
            self.assertEqual(store.load(), {})

    def test_token_store_ignores_invalid_json(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "tokens.json"
            path.write_text("not-json", encoding="utf-8")
            store = FitatuTokenStore(path)

            self.assertEqual(store.load(), {})


class ServiceModuleTests(unittest.TestCase):
    def test_resources_module_calls_expected_route(self) -> None:
        client = Mock()
        client.request.return_value = {"tags": []}
        module = ResourcesModule(client)

        result = module.get_food_tags_recipe()

        self.assertEqual(result, {"tags": []})
        client.request.assert_called_once_with("GET", "/resources/food-tags/recipe")

    def test_cms_module_builds_graphql_payload(self) -> None:
        client = Mock()
        client.request.return_value = {"data": {"ok": True}}
        module = CmsModule(client)

        result = module.graphql(
            "query Example { ping }",
            variables={"foo": "bar"},
            operation_name="Example",
        )

        self.assertEqual(result["data"]["ok"], True)
        client.request.assert_called_once()
        args, kwargs = client.request.call_args
        self.assertEqual(args[0], "POST")
        self.assertEqual(args[1], "https://www.fitatu.com/cms/api/graphql")
        self.assertEqual(kwargs["json_data"]["operationName"], "Example")

    def test_auth_module_delegates_refresh(self) -> None:
        client = Mock()
        client.refresh_access_token.return_value = {"status": "ok"}
        module = AuthModule(client)

        result = module.refresh()

        self.assertEqual(result["status"], "ok")
        client.refresh_access_token.assert_called_once_with()

    def test_user_settings_module_get_uses_optional_date_param(self) -> None:
        client = Mock()
        client.request.return_value = {"ok": True}
        module = UserSettingsModule(client)

        result = module.get("user-123", day=TEST_DAY)

        self.assertEqual(result["ok"], True)
        client.request.assert_called_once_with(
            "GET",
            "/users/user-123/settings",
            params={"date": TEST_DAY.isoformat()},
        )

    def test_user_settings_module_write_helpers_call_expected_routes(self) -> None:
        client = Mock()
        client.request.side_effect = [
            {"ok": True},
            {"ok": True},
            {"ok": True},
            {"token": "abc"},
        ]
        module = UserSettingsModule(client)

        module.update_profile("user-123", {"name": "Adam"})
        module.update_new("user-123", {"dietType": "balanced"})
        module.update_water_settings("user-123", unit_capacity=300)
        token = module.get_firebase_token("user-123")

        self.assertEqual(token["token"], "abc")
        self.assertEqual(client.request.call_args_list[0].args, ("PATCH", "/users/user-123"))
        self.assertEqual(client.request.call_args_list[1].args, ("PATCH", "/users/user-123/settings-new"))
        self.assertEqual(client.request.call_args_list[2].args, ("PATCH", "/users/user-123/settings-new"))
        self.assertEqual(client.request.call_args_list[3].args, ("GET", "/users/user-123/firebaseToken"))

    def test_user_settings_update_system_info_calls_expected_route(self) -> None:
        client = Mock()
        client.request.return_value = {"ok": True}
        module = UserSettingsModule(client)

        result = module.update_system_info("user-123", app_version="5.0.0", system_info="FITATU-IOS")

        self.assertEqual(result["ok"], True)
        client.request.assert_called_once_with(
            "PATCH",
            "/users/user-123",
            json_data={"systemInfo": "FITATU-IOS", "appVersion": "5.0.0"},
        )

    def test_diet_plan_module_get_default_meal_schema(self) -> None:
        client = Mock()
        client.request.return_value = {"schema": []}
        module = DietPlanModule(client)

        result = module.get_default_meal_schema("user-123")

        self.assertEqual(result["schema"], [])
        client.request.assert_called_once_with(
            "GET",
            "/diet-plan/user-123/settings/preferences/meal-schema/default",
        )

    def test_diet_plan_module_get_settings_and_meal_schema(self) -> None:
        client = Mock()
        client.request.side_effect = [{"kcal": 2100}, {"schemas": []}]
        module = DietPlanModule(client)

        settings = module.get_settings("user-123")
        schema = module.get_meal_schema("user-123")

        self.assertEqual(settings["kcal"], 2100)
        self.assertEqual(schema["schemas"], [])
        self.assertEqual(client.request.call_args_list[0].args, ("GET", "/diet-plan/user-123/settings"))
        self.assertEqual(
            client.request.call_args_list[1].args,
            ("GET", "/diet-plan/user-123/settings/preferences/meal-schema"),
        )

    def test_water_module_get_day_calls_expected_route(self) -> None:
        client = Mock()
        client.request.return_value = {"water": 123}
        module = WaterModule(client)

        result = module.get_day("user-123", TEST_DAY)

        self.assertEqual(result["water"], 123)
        client.request.assert_called_once_with("GET", f"/water/user-123/{TEST_DAY.isoformat()}")

    def test_activities_module_get_catalog_calls_expected_route(self) -> None:
        client = Mock()
        client.request.return_value = [{"id": 1}]
        module = ActivitiesModule(client)

        result = module.get_catalog()

        self.assertEqual(result[0]["id"], 1)
        client.request.assert_called_once_with("GET", "/activities/")


class FacadeConvenienceTests(unittest.TestCase):
    def test_describe_session_and_management_report_delegate_to_client(self) -> None:
        stub_client = Mock()
        stub_client.describe_auth_state.return_value = {"lifecycle_state": "healthy"}
        stub_client.management_report.return_value = {"management_report_schema_version": "1.0"}

        lib = FitatuLibrary(session_data={})
        with patch.object(lib, "_build_client", return_value=stub_client):
            session = lib.describe_session(base_url="https://example.com")
            report = lib.management_report(include_tokens=True)

        self.assertEqual(session["lifecycle_state"], "healthy")
        self.assertEqual(report["management_report_schema_version"], "1.0")
        stub_client.describe_auth_state.assert_called_once_with()
        stub_client.management_report.assert_called_once_with(include_tokens=True)

    def test_clear_session_returns_ok_payload(self) -> None:
        stub_client = Mock()
        stub_client.describe_auth_state.return_value = {"has_bearer_token": False}

        lib = FitatuLibrary(session_data={})
        with patch.object(lib, "_build_client", return_value=stub_client):
            result = lib.clear_session(clear_token_store=False)

        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["session"]["has_bearer_token"], False)
        stub_client.clear_auth.assert_called_once_with(clear_token_store=False)

    def test_reauthenticate_session_passes_through_result(self) -> None:
        stub_client = Mock()
        stub_client.reauthenticate.return_value = {"status": "ok", "mode": "refresh"}

        lib = FitatuLibrary(session_data={})
        callback = Mock()
        with patch.object(lib, "_build_client", return_value=stub_client):
            result = lib.reauthenticate_session(
                relogin_callback=callback,
                clear_token_store=True,
                rollback_on_failure=False,
            )

        self.assertEqual(result["mode"], "refresh")
        stub_client.reauthenticate.assert_called_once_with(
            relogin_callback=callback,
            clear_token_store=True,
            rollback_on_failure=False,
        )

    def test_export_session_context_uses_auth_export(self) -> None:
        class StubAuth:
            def to_session_data(self, *, include_tokens: bool = False) -> dict[str, object]:
                return {"include_tokens": include_tokens}

        class StubClient:
            def __init__(self) -> None:
                self.auth = StubAuth()

        lib = FitatuLibrary(session_data={})
        with patch.object(lib, "_build_client", return_value=StubClient()):
            result = lib.export_session_context(include_tokens=True)

        self.assertEqual(result["include_tokens"], True)

    def test_search_food_returns_empty_list_on_api_error(self) -> None:
        lib = FitatuLibrary(session_data={})
        with patch("fitatu_api.facade.FitatuApiClient.search_food", side_effect=FitatuApiError("boom")):
            result = lib.search_food("banan")
        self.assertEqual(result, [])

    def test_get_recipe_and_catalog_facade_wrap_success_payloads(self) -> None:
        stub_client = Mock()
        stub_client.get_recipes_catalog.return_value = {"items": []}
        stub_client.get_recipes_catalog_category.return_value = {"category": "breakfast"}
        stub_client.get_recipe.return_value = {"id": 42}

        lib = FitatuLibrary(session_data={})
        with patch.object(lib, "_build_client", return_value=stub_client):
            catalog = lib.get_recipes_catalog_via_api()
            category = lib.get_recipes_catalog_via_api(category_id="breakfast")
            recipe = lib.get_recipe_via_api(recipe_id=42)

        self.assertEqual(catalog["status"], "ok")
        self.assertEqual(category["result"]["category"], "breakfast")
        self.assertEqual(recipe["result"]["id"], 42)
        # No params — /recipes-catalog returns 400 when any params are sent
        stub_client.get_recipes_catalog.assert_called_once_with()
        stub_client.get_recipes_catalog_category.assert_called_once_with("breakfast")
        stub_client.get_recipe.assert_called_once_with(42)

    def test_create_product_and_add_user_dish_wrap_api_errors(self) -> None:
        stub_client = Mock()
        stub_client.create_product.side_effect = FitatuApiError("product boom", status_code=422, body="bad product")
        stub_client.create_recipe.side_effect = FitatuApiError("recipe boom", status_code=400, body="bad recipe")

        lib = FitatuLibrary(session_data={})
        with patch.object(lib, "_build_client", return_value=stub_client):
            product = lib.create_product_via_api(
                name="Jogurt",
                brand="Demo",
                energy=100,
                protein=10,
                fat=2,
                carbohydrate=8,
            )
            dish = lib.add_user_dish_via_api(
                name="Miska",
                items=[{"itemId": 1, "measureId": 2}],
            )

        self.assertEqual(product["status"], "error")
        self.assertEqual(product["status_code"], 422)
        self.assertEqual(dish["status"], "error")
        self.assertEqual(dish["body"], "bad recipe")

    def test_planner_facade_methods_return_missing_user_error(self) -> None:
        lib = FitatuLibrary(session_data={})

        custom = lib.add_custom_item_to_day_meal_via_api(
            target_date=TEST_DAY,
            meal_key="breakfast",
            name="Juice",
            calories=50,
            protein_g=1,
            fat_g=0,
            carbs_g=12,
        )
        product = lib.add_product_to_day_meal_via_api(
            target_date=TEST_DAY,
            meal_key="breakfast",
            product_id=1,
            measure_id=2,
        )
        search = lib.add_search_result_to_day_meal_via_api(
            target_date=TEST_DAY,
            meal_key="breakfast",
            phrase="banan",
        )
        update = lib.update_day_item_via_api(
            target_date=TEST_DAY,
            meal_key="breakfast",
            item_id="item-1",
        )

        for result in (custom, product, search, update):
            self.assertEqual(result["status"], "error")
            self.assertIn("Missing fitatu user id", result["message"])

    def test_planner_facade_methods_wrap_success_payloads(self) -> None:
        planner = Mock()
        planner.add_custom_item_to_day_meal.return_value = {"ok": True, "kind": "custom"}
        planner.add_product_to_day_meal.return_value = {"ok": True, "kind": "product"}
        planner.add_product_to_day_meal_with_unit.return_value = {"ok": True, "kind": "product-unit"}
        planner.add_search_result_to_day_meal.return_value = {"ok": True, "kind": "search"}
        planner.update_day_item.return_value = {"ok": True, "kind": "update"}
        stub_client = Mock()
        stub_client.planner = planner

        lib = FitatuLibrary(session_data={"fitatu_user_id": TEST_USER_ID})
        with patch.object(lib, "_planner_result", return_value=(stub_client, TEST_USER_ID, None)):
            custom = lib.add_custom_item_to_day_meal_via_api(
                target_date=TEST_DAY,
                meal_key="breakfast",
                name="Juice",
                calories=50,
                protein_g=1,
                fat_g=0,
                carbs_g=12,
            )
            product = lib.add_product_to_day_meal_via_api(
                target_date=TEST_DAY,
                meal_key="breakfast",
                product_id=1,
                measure_id=2,
            )
            search = lib.add_search_result_to_day_meal_via_api(
                target_date=TEST_DAY,
                meal_key="breakfast",
                phrase="banan",
            )
            update = lib.update_day_item_via_api(
                target_date=TEST_DAY,
                meal_key="breakfast",
                item_id="item-1",
            )

        self.assertEqual(custom["result"]["kind"], "custom")
        self.assertEqual(product["result"]["kind"], "product")
        self.assertEqual(search["result"]["kind"], "search")
        self.assertEqual(update["result"]["kind"], "update")

    def test_planner_facade_methods_forward_unit_aware_add_parameters(self) -> None:
        planner = Mock()
        planner.add_product_to_day_meal_with_unit.return_value = {"ok": True, "kind": "product-unit"}
        planner.add_search_result_to_day_meal.return_value = {"ok": True, "kind": "search"}
        stub_client = Mock()
        stub_client.planner = planner

        lib = FitatuLibrary(session_data={"fitatu_user_id": TEST_USER_ID})
        with patch.object(lib, "_planner_result", return_value=(stub_client, TEST_USER_ID, None)):
            product = lib.add_product_to_day_meal_via_api(
                target_date=TEST_DAY,
                meal_key="breakfast",
                product_id=1,
                measure_unit="g",
                measure_amount=180,
            )
            search = lib.add_search_result_to_day_meal_via_api(
                target_date=TEST_DAY,
                meal_key="breakfast",
                phrase="banan",
                measure_unit="g",
                measure_amount=200,
                strict_measure=False,
            )

        self.assertEqual(product["result"]["kind"], "product-unit")
        self.assertEqual(search["result"]["kind"], "search")
        planner.add_product_to_day_meal_with_unit.assert_called_once()
        planner.add_search_result_to_day_meal.assert_called_once_with(
            TEST_USER_ID,
            TEST_DAY,
            meal_type="breakfast",
            phrase="banan",
            index=0,
            measure_quantity=1,
            measure_amount=200,
            measure_unit="g",
            strict_measure=False,
            eaten=False,
        )

    def test_planner_facade_methods_wrap_api_errors(self) -> None:
        planner = Mock()
        planner.add_custom_item_to_day_meal.side_effect = FitatuApiError("custom boom", status_code=400, body="bad custom")
        planner.add_product_to_day_meal.side_effect = FitatuApiError("product boom", status_code=401, body="bad product")
        planner.add_search_result_to_day_meal.side_effect = FitatuApiError("search boom", status_code=402, body="bad search")
        planner.update_day_item.side_effect = FitatuApiError("update boom", status_code=403, body="bad update")
        stub_client = Mock()
        stub_client.planner = planner

        lib = FitatuLibrary(session_data={"fitatu_user_id": TEST_USER_ID})
        with patch.object(lib, "_planner_result", return_value=(stub_client, TEST_USER_ID, None)):
            custom = lib.add_custom_item_to_day_meal_via_api(
                target_date=TEST_DAY,
                meal_key="breakfast",
                name="Juice",
                calories=50,
                protein_g=1,
                fat_g=0,
                carbs_g=12,
            )
            product = lib.add_product_to_day_meal_via_api(
                target_date=TEST_DAY,
                meal_key="breakfast",
                product_id=1,
                measure_id=2,
            )
            search = lib.add_search_result_to_day_meal_via_api(
                target_date=TEST_DAY,
                meal_key="breakfast",
                phrase="banan",
            )
            update = lib.update_day_item_via_api(
                target_date=TEST_DAY,
                meal_key="breakfast",
                item_id="item-1",
            )

        self.assertEqual(custom["status_code"], 400)
        self.assertEqual(product["status_code"], 401)
        self.assertEqual(search["status_code"], 402)
        self.assertEqual(update["status_code"], 403)


class ValidationTests(unittest.TestCase):
    def test_validate_user_id_accepts_valid_string(self) -> None:
        from fitatu_api._validation import validate_user_id
        validate_user_id("user-123")  # should not raise

    def test_validate_user_id_rejects_empty_string(self) -> None:
        from fitatu_api._validation import validate_user_id
        with self.assertRaises(ValueError):
            validate_user_id("")

    def test_validate_user_id_rejects_whitespace_only(self) -> None:
        from fitatu_api._validation import validate_user_id
        with self.assertRaises(ValueError):
            validate_user_id("   ")

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
        from fitatu_api.api_client import WaterModule
        mock_client = Mock()
        module = WaterModule(mock_client)
        with self.assertRaises(ValueError):
            module.set_day("user-123", TEST_DAY, -1)


class LoginTests(unittest.TestCase):
    """Tests for FitatuApiClient.login() classmethod."""

    def _make_jwt(self, payload: dict) -> str:
        import base64
        import json
        header = base64.b64encode(b'{"alg":"HS256"}').rstrip(b"=").decode()
        body = base64.b64encode(json.dumps(payload).encode()).rstrip(b"=").decode()
        return f"{header}.{body}.sig"

    def test_login_success_returns_client_with_auth(self) -> None:
        token = self._make_jwt({"user_id": "42"})
        mock_resp = Mock()
        mock_resp.ok = True
        mock_resp.status_code = 200
        mock_resp.json.return_value = {"token": token, "refresh_token": "ref-tok"}

        with patch("fitatu_api.client.requests.post", return_value=mock_resp):
            client = FitatuApiClient.login("user@example.com", "secret")

        self.assertEqual(client.auth.bearer_token, token)
        self.assertEqual(client.auth.refresh_token, "ref-tok")
        self.assertEqual(client.auth.fitatu_user_id, "42")

    def test_login_extracts_user_id_from_uid_field(self) -> None:
        token = self._make_jwt({"uid": "99"})
        mock_resp = Mock()
        mock_resp.ok = True
        mock_resp.status_code = 200
        mock_resp.json.return_value = {"token": token}

        with patch("fitatu_api.client.requests.post", return_value=mock_resp):
            client = FitatuApiClient.login("u@e.com", "pw")

        self.assertEqual(client.auth.fitatu_user_id, "99")

    def test_login_raises_on_non_ok_response(self) -> None:
        mock_resp = Mock()
        mock_resp.ok = False
        mock_resp.status_code = 401
        mock_resp.text = "Unauthorized"

        with patch("fitatu_api.client.requests.post", return_value=mock_resp):
            with self.assertRaises(FitatuApiError) as ctx:
                FitatuApiClient.login("bad@example.com", "wrong")

        self.assertEqual(ctx.exception.status_code, 401)

    def test_login_raises_when_token_missing_from_response(self) -> None:
        mock_resp = Mock()
        mock_resp.ok = True
        mock_resp.status_code = 200
        mock_resp.text = "{}"
        mock_resp.json.return_value = {}

        with patch("fitatu_api.client.requests.post", return_value=mock_resp):
            with self.assertRaises(FitatuApiError):
                FitatuApiClient.login("u@e.com", "pw")

    def test_login_posts_to_correct_endpoint(self) -> None:
        token = self._make_jwt({"user_id": "1"})
        mock_resp = Mock()
        mock_resp.ok = True
        mock_resp.status_code = 200
        mock_resp.json.return_value = {"token": token}

        with patch("fitatu_api.client.requests.post", return_value=mock_resp) as mock_post:
            FitatuApiClient.login("u@e.com", "pw")

        call_url = mock_post.call_args[0][0]
        self.assertTrue(call_url.endswith("/login"))
        call_body = mock_post.call_args[1]["json"]
        self.assertEqual(call_body["_username"], "u@e.com")
        self.assertEqual(call_body["_password"], "pw")
