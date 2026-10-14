"""Planner flows against the recorded-behaviour fake server."""

from __future__ import annotations

import unittest
from datetime import date
from typing import Any
from unittest.mock import Mock

from fake_server import FakeFitatuServer

from fitatu_api import (
    FitatuApiError,
    PlannerModule,
)

TEST_DAY = date(2026, 4, 19)



TEST_USER_ID = "123"



class FakeClient(FakeFitatuServer):
    """The recorded-behaviour fake server, seeded with one test day."""

    def __init__(
        self,
        day_payload: dict[str, Any],
        *,
        product_details: dict[str, dict[str, Any]] | None = None,
        recipe_details: dict[str, dict[str, Any]] | None = None,
        search_results: dict[str, list[dict[str, Any]]] | None = None,
    ) -> None:
        super().__init__(
            day_payload,
            products=product_details,
            recipes=recipe_details,
            search_results=search_results,
            user_id=TEST_USER_ID,
        )

    @property
    def day_payload(self) -> dict[str, Any]:
        """The test day as the day GET would return it now."""
        return self.day_view(TEST_DAY.isoformat())

    @property
    def last_sync_payload(self) -> dict[str, Any] | None:
        return self.sync_payloads[-1] if self.sync_payloads else None

    @property
    def last_request_kwargs(self) -> dict[str, Any] | None:
        posts = [r for r in self.requests if r["method"] == "POST"]
        return {k: v for k, v in posts[-1].items() if k not in {"method", "path", "json_data"}} if posts else None

    @property
    def request_log(self) -> list[tuple[str, str]]:
        return [(r["method"], r["path"]) for r in self.requests]



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

        meal = client.planner.get_meal(TEST_USER_ID, TEST_DAY, meal_type="breakfast")
        items = client.planner.list_meal_items(TEST_USER_ID, TEST_DAY, meal_type="breakfast")
        item = client.planner.find_meal_item(TEST_USER_ID, TEST_DAY, meal_type="breakfast", query="porr")

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
        self.assertEqual(result["added_item"]["foodType"], "CUSTOM_ITEM")
        self.assertEqual(result["added_item"]["name"], "Fresh juice")

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
        self.assertEqual(result["added_item"]["foodType"], "RECIPE")
        self.assertEqual(result["added_item"]["recipeId"], 123456)

        saved_item = client.day_payload["dietPlan"]["breakfast"]["items"][-1]
        self.assertEqual(saved_item["foodType"], "RECIPE")
        self.assertEqual(saved_item["recipeId"], 123456)
        self.assertTrue(bool(saved_item.get("planDayDietItemId")))

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
        self.assertEqual(result["added_item"]["measureId"], 39)
        self.assertEqual(result["added_item"]["measureQuantity"], 1)
        self.assertEqual(result["added_item"]["ingredientsServing"], 1)

    def test_remove_day_item_marks_the_row_deleted(self) -> None:
        client = FakeClient(_make_day_payload())

        result = client.planner.remove_day_item(TEST_USER_ID, TEST_DAY, meal_type="breakfast", item_id="item-1")

        self.assertTrue(result["ok"])
        self.assertEqual(result["cleanup_mode"], "removed")
        self.assertEqual(client.day_payload["dietPlan"]["breakfast"]["items"], [])
        self.assertTrue(client.all_rows(TEST_DAY.isoformat())["item-1"]["deletedAt"])

    def test_classify_day_item_for_removal_distinguishes_kinds(self) -> None:
        product_client = FakeClient(_make_day_payload())
        custom_add_client = FakeClient(_make_custom_add_payload())
        custom_recipe_client = FakeClient(_make_custom_recipe_like_payload())

        product_kind = product_client.planner.classify_day_item_for_removal(
            TEST_USER_ID,
            TEST_DAY,
            meal_type="breakfast",
            item_id="item-1",
        )
        custom_add_kind = custom_add_client.planner.classify_day_item_for_removal(
            TEST_USER_ID,
            TEST_DAY,
            meal_type="breakfast",
            item_id="custom-add-1",
        )
        custom_recipe_kind = custom_recipe_client.planner.classify_day_item_for_removal(
            TEST_USER_ID,
            TEST_DAY,
            meal_type="breakfast",
            item_id="recipe-custom-1",
        )

        self.assertEqual(product_kind["resolved_kind"], "normal_item")
        self.assertEqual(custom_add_kind["resolved_kind"], "custom_add_item")
        self.assertEqual(custom_recipe_kind["resolved_kind"], "custom_recipe_item")

    def test_remove_day_item_deletes_a_custom_recipe_like_row_the_same_way(self) -> None:
        client = FakeClient(_make_custom_recipe_like_payload())

        result = client.planner.remove_day_item(
            TEST_USER_ID, TEST_DAY, meal_type="breakfast", item_id="recipe-custom-1"
        )

        self.assertTrue(result["ok"])
        [sent] = client.last_sync_payload[TEST_DAY.isoformat()]["dietPlan"]["breakfast"]["items"]
        self.assertTrue(sent["deletedAt"])

    def test_remove_day_items_by_kind_deletes_matching_rows_in_one_sync(self) -> None:
        client = FakeClient(_make_mixed_removal_payload())
        before = {row["planDayDietItemId"] for meal in client.day_payload["dietPlan"].values() for row in meal["items"]}

        result = client.planner.remove_day_items_by_kind(TEST_USER_ID, TEST_DAY, item_kind="normal_item", all_meals=True)

        self.assertTrue(result["ok"])
        self.assertEqual((result["targeted_count"], result["removed_count"]), (1, 1))
        self.assertEqual(len(client.sync_payloads), 1)
        after = {row["planDayDietItemId"] for meal in client.day_payload["dietPlan"].values() for row in meal["items"]}
        self.assertEqual(before - after, {"prod-1"})

    def test_remove_day_items_by_kind_supports_meal_scope(self) -> None:
        client = FakeClient(_make_mixed_removal_payload())

        result = client.planner.remove_day_items_by_kind(
            TEST_USER_ID, TEST_DAY, item_kind="custom_add_item", meal_type="lunch"
        )

        self.assertTrue(result["ok"])
        self.assertEqual(result["targeted_count"], 1)
        self.assertEqual(result["meal"], "lunch")
        [sent] = client.last_sync_payload[TEST_DAY.isoformat()]["dietPlan"]["lunch"]["items"]
        self.assertEqual(sent["planDayDietItemId"], "custom-add-1")
        self.assertTrue(sent["deletedAt"])

    def test_add_search_result_to_day_meal_resolves_direct_measure_unit(self) -> None:
        client = FakeClient(
            _make_day_payload(),
            search_results={
                "jogurt": [
                    {
                        "id": 24881725,
                        "foodId": 24881725,
                        "type": "PRODUCT",
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
        self.assertEqual(result["measure_resolution"]["measure_id"], 1)
        self.assertEqual(result["measure_resolution"]["measure_quantity"], 200)
        self.assertEqual(result["measure_resolution"]["strategy"], "direct_unit_match")

    def test_add_search_result_to_day_meal_converts_amount_when_unit_missing(self) -> None:
        client = FakeClient(
            _make_day_payload(),
            search_results={
                "jogurt": [
                    {
                        "id": 24881725,
                        "foodId": 24881725,
                        "type": "PRODUCT",
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
        self.assertEqual(result["measure_resolution"]["measure_id"], 2)
        self.assertAlmostEqual(float(result["measure_resolution"]["measure_quantity"]), 2.0)
        self.assertEqual(result["measure_resolution"]["strategy"], "converted_from_weight")

    def test_add_search_result_to_day_meal_raises_when_unit_unresolvable_in_strict_mode(self) -> None:
        client = FakeClient(
            _make_day_payload(),
            search_results={
                "jogurt": [
                    {
                        "id": 24881725,
                        "foodId": 24881725,
                        "type": "PRODUCT",
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

    def test_sync_single_day_posts_to_the_single_days_route(self) -> None:
        client = Mock()
        client.request.return_value = [{"date": TEST_DAY.isoformat(), "revision": "r", "errorMessage": None}]
        PlannerModule(client).sync_single_day(TEST_USER_ID, TEST_DAY, day_payload={"dietPlan": {}}, synchronous=True)

        client.request.assert_called_once_with(
            "POST",
            f"/diet-plan/{TEST_USER_ID}/days",
            json_data={TEST_DAY.isoformat(): {"dietPlan": {}}},
            params={"synchronous": "true"},
            idempotent=True,
        )
