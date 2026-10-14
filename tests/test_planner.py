"""Unit tests for PlannerModule using an in-process FakeClient."""

from __future__ import annotations

import inspect
import uuid
from copy import deepcopy
from datetime import date
from typing import Any
from unittest.mock import Mock

import pytest
from fake_server import FakeFitatuServer

from fitatu_api import (
    FitatuApiError,
    FitatuNotFoundError,
    FitatuPlannerSyncError,
    FitatuValidationError,
    PlannerModule,
)
from fitatu_api.planner import _new_row_id

TEST_DAY = date(2026, 1, 15)
TEST_USER = "42"


# ---------------------------------------------------------------------------
# FakeClient
# ---------------------------------------------------------------------------


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
            user_id=TEST_USER,
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


def _empty_day() -> dict[str, Any]:
    return {
        "dietPlan": {
            "breakfast": {"items": []},
            "lunch": {"items": []},
            "snack": {"items": []},
        },
        "toiletItems": [],
        "note": None,
        "tagsIds": [],
    }


class IgnoringFakeClient(FakeClient):
    """Accepts every sync (202, no errorMessage) but never changes the stored day."""

    def _sync(self, payload: dict[str, Any]) -> list[dict[str, Any]]:
        return [{"date": day, "revision": "rev-x", "errorMessage": None} for day in payload]


def _day_with_product(
    meal: str = "breakfast",
    item_id: str = "item-1",
    product_id: int = 1001,
    quantity: float = 1.0,
) -> dict[str, Any]:
    return {
        "dietPlan": {
            meal: {
                "items": [
                    {
                        "planDayDietItemId": item_id,
                        "foodType": "PRODUCT",
                        "productId": product_id,
                        "measureId": 11,
                        "measureQuantity": quantity,
                        "name": "Oats",
                        "eaten": False,
                        "source": "API",
                    }
                ]
            },
            "lunch": {"items": []},
            "snack": {"items": []},
        },
        "toiletItems": [],
        "note": None,
        "tagsIds": [],
    }


def _day_with_custom_item(
    item_id: str = "custom-1",
    quantity: float = 100.0,
    source: str = "WEB",
) -> dict[str, Any]:
    return {
        "dietPlan": {
            "breakfast": {
                "items": [
                    {
                        "planDayDietItemId": item_id,
                        "foodType": "CUSTOM_ITEM",
                        "name": "My custom",
                        "measureId": 1,
                        "measureQuantity": quantity,
                        "energy": 200,
                        "protein": 10,
                        "fat": 5,
                        "carbohydrate": 25,
                        "source": source,
                    }
                ]
            },
            "lunch": {"items": []},
            "snack": {"items": []},
        },
        "toiletItems": [],
        "note": None,
        "tagsIds": [],
    }


def _day_with_recipe(
    item_id: str = "recipe-1",
    recipe_id: int = 9999,
    quantity: float = 1.0,
) -> dict[str, Any]:
    return {
        "dietPlan": {
            "breakfast": {
                "items": [
                    {
                        "planDayDietItemId": item_id,
                        "foodType": "RECIPE",
                        "recipeId": recipe_id,
                        "measureId": 39,
                        "measureQuantity": quantity,
                        "ingredientsServing": 1,
                        "source": "API",
                    }
                ]
            },
            "lunch": {"items": []},
            "snack": {"items": []},
        },
        "toiletItems": [],
        "note": None,
        "tagsIds": [],
    }


def _simple_recipe_details(recipe_id: int = 9999) -> dict[str, Any]:
    return {
        "id": recipe_id,
        "name": "Veggie Bowl",
        "photoUrl": "https://cdn.example.com/recipes/veggie.jpg",
        "nutritionalValues": {
            "energy": 320,
            "protein": 15,
            "fat": 8,
            "carbohydrate": 40,
        },
        "ingredientsServing": 2,
        "measureId": 39,
        "measureQuantity": 1,
    }


def _simple_product_details(
    product_id: int = 1001,
    *,
    weight_per_unit: float = 1.0,
    measure_name: str = "g",
    measure_id: int = 11,
) -> dict[str, Any]:
    return {
        "id": product_id,
        "name": "Oats",
        "measures": [
            {
                "id": measure_id,
                "name": measure_name,
                "weightPerUnit": weight_per_unit,
            }
        ],
        "simpleMeasures": [],
    }


# ---------------------------------------------------------------------------
# TestStaticHelpers
# ---------------------------------------------------------------------------


class TestStaticHelpers:
    def test_values_match_none(self) -> None:
        assert PlannerModule._values_match(None, None) is True
        assert PlannerModule._values_match(None, 1) is False

    def test_values_match_numeric_tolerance(self) -> None:
        assert PlannerModule._values_match(1.0, 1.0) is True
        assert PlannerModule._values_match(1.5, 1.5 + 1e-10) is True
        assert PlannerModule._values_match(1.0, 2.0) is False

    def test_values_match_bool(self) -> None:
        assert PlannerModule._values_match(True, True) is True
        assert PlannerModule._values_match(False, True) is False

    def test_values_match_strings(self) -> None:
        assert PlannerModule._values_match("abc", "abc") is True
        assert PlannerModule._values_match("abc", "ABC") is False

    def test_normalize_meal_key_aliases(self) -> None:
        assert PlannerModule.normalize_meal_key("second-breakfast") == "second_breakfast"
        assert PlannerModule.normalize_meal_key("second breakfast") == "second_breakfast"

    def test_normalize_meal_key_passthrough(self) -> None:
        assert PlannerModule.normalize_meal_key("breakfast") == "breakfast"
        assert PlannerModule.normalize_meal_key("LUNCH") == "lunch"

    def test_sync_days_returns_per_day_results(self) -> None:
        client = FakeClient(_empty_day())
        [row] = client.planner.sync_days(TEST_USER, {TEST_DAY.isoformat(): {"dietPlan": {}}})
        assert row["date"] == TEST_DAY.isoformat()
        assert row["revision"]
        assert row["errorMessage"] is None

    def test_sync_days_raises_when_a_later_day_is_rejected(self) -> None:
        client = Mock()
        client.request.return_value = [
            {"date": "2026-10-12", "revision": "r1", "errorMessage": None},
            {"date": "2026-10-13", "revision": None, "errorMessage": "nope"},
        ]
        with pytest.raises(FitatuPlannerSyncError, match="2026-10-13: nope"):
            PlannerModule(client).sync_days(TEST_USER, {"2026-10-12": {}, "2026-10-13": {}})

    def test_sync_days_raises_when_a_day_is_rejected(self) -> None:
        client = Mock()
        rejected = [{"date": "2026-10-13", "revision": None, "errorMessage": "Invalid pddiId UUID version"}]
        client.request.return_value = rejected
        planner = PlannerModule(client)
        with pytest.raises(FitatuPlannerSyncError) as excinfo:
            planner.sync_days(TEST_USER, {"2026-10-13": {"dietPlan": {}}})
        assert excinfo.value.results == rejected
        assert "Invalid pddiId UUID version" in str(excinfo.value)

    def test_sync_days_supports_synchronous_param(self) -> None:
        client = FakeClient(_empty_day())
        client.planner.sync_days(TEST_USER, {TEST_DAY.isoformat(): {"dietPlan": {}}}, synchronous=True)
        assert client.last_request_kwargs is not None
        assert client.last_request_kwargs["params"] == {"synchronous": "true"}

    def test_extract_measure_id_from_measure_dict(self) -> None:
        product = {"measure": {"defaultMeasureId": 99}}
        assert PlannerModule._extract_measure_id(product) == 99

    def test_extract_measure_id_from_top_level(self) -> None:
        product = {"defaultMeasureId": 5}
        assert PlannerModule._extract_measure_id(product) == 5

    def test_extract_measure_id_does_not_guess(self) -> None:
        assert PlannerModule._extract_measure_id({}) is None
        assert PlannerModule._extract_measure_id({"measure": {"measureName": "g"}}) is None

    def test_normalize_measure_unit_aliases(self) -> None:
        assert PlannerModule._normalize_measure_unit("gram") == "g"
        assert PlannerModule._normalize_measure_unit("grams") == "g"
        assert PlannerModule._normalize_measure_unit("milliliter") == "ml"
        assert PlannerModule._normalize_measure_unit("portion") == "porcja"
        assert PlannerModule._normalize_measure_unit("piece") == "sztuka"
        assert PlannerModule._normalize_measure_unit("pack") == "opakowanie"

    def test_normalize_measure_unit_none(self) -> None:
        assert PlannerModule._normalize_measure_unit(None) is None
        assert PlannerModule._normalize_measure_unit("  ") is None

    def test_coerce_positive_float_valid(self) -> None:
        client = FakeClient(_empty_day())
        result = client.planner._coerce_positive_float(5.5, field_name="x")
        assert result == pytest.approx(5.5)

    def test_coerce_positive_float_zero_raises(self) -> None:
        client = FakeClient(_empty_day())
        with pytest.raises(FitatuApiError, match="must be > 0"):
            client.planner._coerce_positive_float(0, field_name="x")

    def test_coerce_positive_float_negative_raises(self) -> None:
        client = FakeClient(_empty_day())
        with pytest.raises(FitatuApiError, match="must be > 0"):
            client.planner._coerce_positive_float(-1, field_name="x")

    def test_measure_name_matches_unit_g(self) -> None:
        assert PlannerModule._measure_name_matches_unit("g", "g") is True
        assert PlannerModule._measure_name_matches_unit("gram", "g") is True
        assert PlannerModule._measure_name_matches_unit("gramy", "g") is True
        assert PlannerModule._measure_name_matches_unit("porcja", "g") is False

    def test_measure_name_matches_unit_ml(self) -> None:
        assert PlannerModule._measure_name_matches_unit("ml", "ml") is True
        assert PlannerModule._measure_name_matches_unit("milliliter", "ml") is True

    def test_measure_name_matches_unit_empty(self) -> None:
        assert PlannerModule._measure_name_matches_unit("", "g") is False

    def test_resolve_item_kind_product(self) -> None:
        item = {"foodType": "PRODUCT", "productId": 1}
        kind, reason = PlannerModule._resolve_item_kind(item)
        assert kind == "normal_item"
        assert "product" in reason

    def test_resolve_item_kind_custom_add(self) -> None:
        item = {"foodType": "CUSTOM_ITEM", "source": "WEB", "measureQuantity": 100.0}
        kind, reason = PlannerModule._resolve_item_kind(item)
        assert kind == "custom_add_item"

    def test_resolve_item_kind_custom_recipe_like(self) -> None:
        item = {"foodType": "CUSTOM_ITEM", "source": "API", "measureQuantity": 1.0}
        kind, reason = PlannerModule._resolve_item_kind(item)
        assert kind == "custom_recipe_item"

    def test_resolve_item_kind_recipe_food_type(self) -> None:
        item = {"foodType": "RECIPE", "recipeId": 1}
        kind, _ = PlannerModule._resolve_item_kind(item)
        assert kind == "normal_item"

# ---------------------------------------------------------------------------
# TestGetDayAndMeal
# ---------------------------------------------------------------------------


class TestGetDayAndMeal:
    def test_get_day_returns_full_payload(self) -> None:
        client = FakeClient(_day_with_product())
        result = client.planner.get_day(TEST_USER, TEST_DAY)
        assert "dietPlan" in result

    def test_get_meal_returns_meal_bucket(self) -> None:
        client = FakeClient(_day_with_product())
        meal = client.planner.get_meal(TEST_USER, TEST_DAY, meal_type="breakfast")
        assert "items" in meal

    def test_get_meal_raises_for_unknown_meal(self) -> None:
        client = FakeClient(_day_with_product())
        with pytest.raises(FitatuApiError, match="meal not found"):
            client.planner.get_meal(TEST_USER, TEST_DAY, meal_type="nonexistent")

    def test_get_meal_raises_when_diet_plan_missing(self) -> None:
        client = Mock()
        client.request.return_value = {"note": None, "tagsIds": []}
        with pytest.raises(FitatuApiError, match="dietPlan not available"):
            PlannerModule(client).get_meal(TEST_USER, TEST_DAY, meal_type="breakfast")

    def test_list_meal_items_returns_items(self) -> None:
        client = FakeClient(_day_with_product())
        items = client.planner.list_meal_items(TEST_USER, TEST_DAY, meal_type="breakfast")
        assert len(items) == 1
        assert items[0]["productId"] == 1001

    def test_list_meal_items_empty_meal(self) -> None:
        client = FakeClient(_day_with_product())
        items = client.planner.list_meal_items(TEST_USER, TEST_DAY, meal_type="lunch")
        assert items == []

    def test_find_meal_item_found(self) -> None:
        client = FakeClient(_day_with_product())
        result = client.planner.find_meal_item(TEST_USER, TEST_DAY, meal_type="breakfast", query="oat")
        assert result is not None
        assert result["productId"] == 1001

    def test_find_meal_item_not_found(self) -> None:
        client = FakeClient(_day_with_product())
        result = client.planner.find_meal_item(TEST_USER, TEST_DAY, meal_type="breakfast", query="xyz")
        assert result is None

    def test_find_meal_item_empty_query(self) -> None:
        client = FakeClient(_day_with_product())
        result = client.planner.find_meal_item(TEST_USER, TEST_DAY, meal_type="breakfast", query="  ")
        assert result is None

    def test_list_day_items_for_removal_all_meals(self) -> None:
        payload = {
            "dietPlan": {
                "breakfast": {"items": [{"planDayDietItemId": "a", "foodType": "PRODUCT", "productId": 1, "source": "API", "measureQuantity": 1.0}]},
                "lunch": {"items": [{"planDayDietItemId": "b", "foodType": "CUSTOM_ITEM", "source": "WEB", "measureQuantity": 100.0}]},
            },
            "toiletItems": [],
            "note": None,
            "tagsIds": [],
        }
        client = FakeClient(payload)
        rows = client.planner.list_day_items_for_removal(TEST_USER, TEST_DAY)
        assert len(rows) == 2

    def test_list_day_items_for_removal_filtered_by_meal(self) -> None:
        payload = {
            "dietPlan": {
                "breakfast": {"items": [{"planDayDietItemId": "a", "foodType": "PRODUCT", "productId": 1, "source": "API", "measureQuantity": 1.0}]},
                "lunch": {"items": [{"planDayDietItemId": "b", "foodType": "PRODUCT", "productId": 2, "source": "API", "measureQuantity": 1.0}]},
            },
            "toiletItems": [],
            "note": None,
            "tagsIds": [],
        }
        client = FakeClient(payload)
        rows = client.planner.list_day_items_for_removal(TEST_USER, TEST_DAY, meal_type="breakfast")
        assert len(rows) == 1
        assert rows[0]["meal"] == "breakfast"

    def test_list_day_items_for_removal_raises_for_unknown_meal(self) -> None:
        client = FakeClient(_empty_day())
        with pytest.raises(FitatuApiError, match="meal not found"):
            client.planner.list_day_items_for_removal(TEST_USER, TEST_DAY, meal_type="NONEXISTENT")


# ---------------------------------------------------------------------------
# TestAddProductToDayMeal
# ---------------------------------------------------------------------------


class TestAddProductToDayMeal:
    def test_returns_ok_true(self) -> None:
        client = FakeClient(_empty_day())
        result = client.planner.add_product_to_day_meal(
            TEST_USER, TEST_DAY, meal_type="breakfast", product_id=999, measure_id=1, measure_quantity=2.0
        )
        assert result["ok"] is True

    def test_product_appended_to_meal(self) -> None:
        client = FakeClient(_empty_day())
        client.planner.add_product_to_day_meal(
            TEST_USER, TEST_DAY, meal_type="breakfast", product_id=999, measure_id=1, measure_quantity=2.0
        )
        items = client.day_payload["dietPlan"]["breakfast"]["items"]
        assert len(items) == 1
        assert items[0]["productId"] == 999

    def test_measure_quantity_stored(self) -> None:
        client = FakeClient(_empty_day())
        client.planner.add_product_to_day_meal(
            TEST_USER, TEST_DAY, meal_type="breakfast", product_id=1, measure_id=5, measure_quantity=3.5
        )
        item = client.day_payload["dietPlan"]["breakfast"]["items"][0]
        assert item["measureQuantity"] == pytest.approx(3.5)
        assert item["measureId"] == 5

    def test_food_type_is_product(self) -> None:
        client = FakeClient(_empty_day())
        result = client.planner.add_product_to_day_meal(
            TEST_USER, TEST_DAY, meal_type="breakfast", product_id=1, measure_id=1
        )
        assert result["added_item"]["foodType"] == "PRODUCT"

    def test_item_id_generated(self) -> None:
        client = FakeClient(_empty_day())
        result = client.planner.add_product_to_day_meal(
            TEST_USER, TEST_DAY, meal_type="breakfast", product_id=1, measure_id=1
        )
        assert bool(result["added_item"]["planDayDietItemId"])

    def test_raises_for_missing_meal(self) -> None:
        client = FakeClient(_empty_day())
        with pytest.raises(FitatuApiError, match="meal not found"):
            client.planner.add_product_to_day_meal(
                TEST_USER, TEST_DAY, meal_type="dinner", product_id=1, measure_id=1
            )

    def test_adds_to_existing_items(self) -> None:
        client = FakeClient(_day_with_product())
        client.planner.add_product_to_day_meal(
            TEST_USER, TEST_DAY, meal_type="breakfast", product_id=2222, measure_id=1
        )
        items = client.day_payload["dietPlan"]["breakfast"]["items"]
        assert len(items) == 2

    def test_second_breakfast_alias(self) -> None:
        payload = deepcopy(_empty_day())
        payload["dietPlan"]["second_breakfast"] = {"items": []}
        client = FakeClient(payload)
        result = client.planner.add_product_to_day_meal(
            TEST_USER, TEST_DAY, meal_type="second-breakfast", product_id=1, measure_id=1
        )
        assert result["meal"] == "second_breakfast"

    def test_source_field_passed(self) -> None:
        client = FakeClient(_empty_day())
        result = client.planner.add_product_to_day_meal(
            TEST_USER, TEST_DAY, meal_type="breakfast", product_id=1, measure_id=1, source="WEB"
        )
        assert result["added_item"]["source"] == "WEB"

    def test_eaten_flag(self) -> None:
        client = FakeClient(_empty_day())
        result = client.planner.add_product_to_day_meal(
            TEST_USER, TEST_DAY, meal_type="breakfast", product_id=1, measure_id=1, eaten=True
        )
        assert result["added_item"]["eaten"] is True


# ---------------------------------------------------------------------------
# TestAddRecipeToDayMeal
# ---------------------------------------------------------------------------


class TestAddRecipeToDayMeal:
    def test_returns_ok_true(self) -> None:
        client = FakeClient(_empty_day())
        result = client.planner.add_recipe_to_day_meal(
            TEST_USER, TEST_DAY, meal_type="breakfast", recipe_id=42
        )
        assert result["ok"] is True

    def test_recipe_appended(self) -> None:
        client = FakeClient(_empty_day())
        client.planner.add_recipe_to_day_meal(
            TEST_USER, TEST_DAY, meal_type="breakfast", recipe_id=42
        )
        items = client.day_payload["dietPlan"]["breakfast"]["items"]
        assert len(items) == 1
        assert items[0]["recipeId"] == 42

    def test_food_type_recipe(self) -> None:
        client = FakeClient(_empty_day())
        result = client.planner.add_recipe_to_day_meal(
            TEST_USER, TEST_DAY, meal_type="breakfast", recipe_id=1, food_type="RECIPE"
        )
        assert result["added_item"]["foodType"] == "RECIPE"

    def test_food_type_recipe_ai(self) -> None:
        client = FakeClient(_empty_day())
        result = client.planner.add_recipe_to_day_meal(
            TEST_USER, TEST_DAY, meal_type="breakfast", recipe_id=1, food_type="RECIPE_AI"
        )
        assert result["added_item"]["foodType"] == "RECIPE_AI"

    def test_invalid_food_type_raises(self) -> None:
        client = FakeClient(_empty_day())
        with pytest.raises(FitatuApiError, match="food_type must be"):
            client.planner.add_recipe_to_day_meal(
                TEST_USER, TEST_DAY, meal_type="breakfast", recipe_id=1, food_type="PRODUCT"
            )

    def test_item_id_generated(self) -> None:
        client = FakeClient(_empty_day())
        result = client.planner.add_recipe_to_day_meal(
            TEST_USER, TEST_DAY, meal_type="breakfast", recipe_id=1
        )
        assert bool(result["added_item"]["planDayDietItemId"])

    def test_explicit_serving_is_sent(self) -> None:
        client = FakeClient(
            _empty_day(),
            recipe_details={"9999": _simple_recipe_details(9999)},
        )
        result = client.planner.add_recipe_to_day_meal(
            TEST_USER, TEST_DAY, meal_type="breakfast", recipe_id=9999, ingredients_serving=5
        )
        assert result["added_item"]["ingredientsServing"] == 5

    def test_raises_for_missing_meal(self) -> None:
        client = FakeClient(_empty_day())
        with pytest.raises(FitatuApiError, match="meal not found"):
            client.planner.add_recipe_to_day_meal(
                TEST_USER, TEST_DAY, meal_type="supper", recipe_id=1
            )

    def test_ingredients_serving_stored(self) -> None:
        client = FakeClient(_empty_day())
        result = client.planner.add_recipe_to_day_meal(
            TEST_USER, TEST_DAY, meal_type="breakfast", recipe_id=1, ingredients_serving=3
        )
        assert result["added_item"]["ingredientsServing"] == 3

    def test_measure_id_and_quantity_stored(self) -> None:
        client = FakeClient(_empty_day())
        result = client.planner.add_recipe_to_day_meal(
            TEST_USER, TEST_DAY, meal_type="breakfast", recipe_id=1, measure_id=39, measure_quantity=2
        )
        assert result["added_item"]["measureId"] == 39
        assert result["added_item"]["measureQuantity"] == 2


# ---------------------------------------------------------------------------
# TestAddCustomItemToDayMeal
# ---------------------------------------------------------------------------


class TestAddCustomItemToDayMeal:
    def test_returns_ok_true(self) -> None:
        client = FakeClient(_empty_day())
        result = client.planner.add_custom_item_to_day_meal(
            TEST_USER, TEST_DAY, meal_type="breakfast", name="Soup", calories=100, protein_g=5, fat_g=2, carbs_g=10
        )
        assert result["ok"] is True

    def test_food_type_custom_item(self) -> None:
        client = FakeClient(_empty_day())
        result = client.planner.add_custom_item_to_day_meal(
            TEST_USER, TEST_DAY, meal_type="breakfast", name="Soup", calories=100, protein_g=5, fat_g=2, carbs_g=10
        )
        assert result["added_item"]["foodType"] == "CUSTOM_ITEM"

    def test_name_stored(self) -> None:
        client = FakeClient(_empty_day())
        result = client.planner.add_custom_item_to_day_meal(
            TEST_USER, TEST_DAY, meal_type="breakfast", name="  My Salad  ", calories=0, protein_g=0, fat_g=0, carbs_g=0
        )
        assert result["added_item"]["name"] == "My Salad"

    def test_empty_name_raises(self) -> None:
        client = FakeClient(_empty_day())
        with pytest.raises(FitatuApiError, match="name is required"):
            client.planner.add_custom_item_to_day_meal(
                TEST_USER, TEST_DAY, meal_type="breakfast", name="  ", calories=0, protein_g=0, fat_g=0, carbs_g=0
            )

    def test_macros_stored(self) -> None:
        client = FakeClient(_empty_day())
        result = client.planner.add_custom_item_to_day_meal(
            TEST_USER, TEST_DAY, meal_type="breakfast", name="X", calories=300, protein_g=25, fat_g=10, carbs_g=30
        )
        item = result["added_item"]
        assert item["energy"] == pytest.approx(300.0)
        assert item["protein"] == pytest.approx(25.0)
        assert item["fat"] == pytest.approx(10.0)
        assert item["carbohydrate"] == pytest.approx(30.0)

    def test_raises_for_missing_meal(self) -> None:
        client = FakeClient(_empty_day())
        with pytest.raises(FitatuApiError, match="meal not found"):
            client.planner.add_custom_item_to_day_meal(
                TEST_USER, TEST_DAY, meal_type="dinner", name="X", calories=0, protein_g=0, fat_g=0, carbs_g=0
            )

    def test_appended_to_existing(self) -> None:
        client = FakeClient(_day_with_product())
        client.planner.add_custom_item_to_day_meal(
            TEST_USER, TEST_DAY, meal_type="breakfast", name="Extra", calories=0, protein_g=0, fat_g=0, carbs_g=0
        )
        items = client.day_payload["dietPlan"]["breakfast"]["items"]
        assert len(items) == 2


# ---------------------------------------------------------------------------
# TestUpdateDayItem
# ---------------------------------------------------------------------------


class TestUpdateDayItem:
    def test_update_measure_quantity(self) -> None:
        client = FakeClient(_day_with_product())
        result = client.planner.update_day_item(
            TEST_USER, TEST_DAY, meal_type="breakfast", item_id="item-1", measure_quantity=5.0
        )
        assert result["ok"] is True
        assert result["validation"]["measureQuantity"] is True

    def test_update_eaten_flag(self) -> None:
        client = FakeClient(_day_with_product())
        result = client.planner.update_day_item(
            TEST_USER, TEST_DAY, meal_type="breakfast", item_id="item-1", eaten=True
        )
        assert result["validation"]["eaten"] is True

    def test_update_name(self) -> None:
        client = FakeClient(_day_with_product())
        result = client.planner.update_day_item(
            TEST_USER, TEST_DAY, meal_type="breakfast", item_id="item-1", name="Renamed"
        )
        assert result["validation"]["name"] is True

    def test_update_via_product_id(self) -> None:
        client = FakeClient(_day_with_product())
        result = client.planner.update_day_item(
            TEST_USER, TEST_DAY, meal_type="breakfast", item_id=1001, measure_quantity=3.0
        )
        assert result["ok"] is True

    def test_update_nonexistent_item_raises(self) -> None:
        client = FakeClient(_day_with_product())
        with pytest.raises(FitatuApiError, match="item not found"):
            client.planner.update_day_item(
                TEST_USER, TEST_DAY, meal_type="breakfast", item_id="ghost", measure_quantity=1.0
            )

    def test_update_missing_meal_raises(self) -> None:
        client = FakeClient(_day_with_product())
        with pytest.raises(FitatuApiError, match="meal not found"):
            client.planner.update_day_item(
                TEST_USER, TEST_DAY, meal_type="supper", item_id="item-1", measure_quantity=1.0
            )

    def test_patch_dict_applied(self) -> None:
        client = FakeClient(_day_with_product())
        result = client.planner.update_day_item(
            TEST_USER, TEST_DAY, meal_type="breakfast", item_id="item-1", patch={"custom_flag": True}
        )
        assert result["updates"]["custom_flag"] is True

    def test_before_and_after_returned(self) -> None:
        client = FakeClient(_day_with_product())
        result = client.planner.update_day_item(
            TEST_USER, TEST_DAY, meal_type="breakfast", item_id="item-1", measure_quantity=2.0
        )
        assert result["before"]["measureQuantity"] == pytest.approx(1.0)
        assert result["after"] is not None


# ---------------------------------------------------------------------------
# TestMoveAndReplaceDayItem
# ---------------------------------------------------------------------------


class TestMoveAndReplaceDayItem:
    def test_move_day_item_builds_deleted_marker_and_new_item(self) -> None:
        client = FakeClient(_day_with_product())
        result = client.planner.move_day_item(
            TEST_USER,
            TEST_DAY,
            from_meal_type="breakfast",
            item_id="item-1",
            to_meal_type="lunch",
        )

        assert result["ok"] is True
        assert result["from_meal"] == "breakfast"
        assert result["to_meal"] == "lunch"
        assert client.last_request_kwargs is not None
        assert client.last_request_kwargs["params"] == {"synchronous": "true"}

        payload = result["sync_payload"][TEST_DAY.isoformat()]["dietPlan"]
        assert payload["breakfast"]["items"][0]["deletedAt"]
        assert payload["breakfast"]["items"][0]["productId"] == 1001
        assert payload["lunch"]["items"][0]["productId"] == 1001
        assert payload["lunch"]["items"][0]["planDayDietItemId"] != "item-1"

    def test_move_day_item_across_dates_builds_two_day_payload(self) -> None:
        client = FakeClient(_day_with_product())
        next_day = date(2026, 1, 16)
        result = client.planner.move_day_item(
            TEST_USER,
            TEST_DAY,
            from_meal_type="breakfast",
            item_id="item-1",
            to_day=next_day,
            to_meal_type="snack",
        )

        assert set(result["sync_payload"]) == {TEST_DAY.isoformat(), next_day.isoformat()}
        assert "breakfast" in result["sync_payload"][TEST_DAY.isoformat()]["dietPlan"]
        assert "snack" in result["sync_payload"][next_day.isoformat()]["dietPlan"]

    def test_replace_day_item_with_custom_item_builds_delete_and_replacement(self) -> None:
        client = FakeClient(_day_with_product())
        result = client.planner.replace_day_item_with_custom_item(
            TEST_USER,
            TEST_DAY,
            meal_type="breakfast",
            item_id="item-1",
            name="Updated meal",
            calories=400,
            protein_g=30,
            fat_g=10,
            carbs_g=40,
        )

        assert result["ok"] is True
        payload_items = result["sync_payload"][TEST_DAY.isoformat()]["dietPlan"]["breakfast"]["items"]
        assert payload_items[0]["deletedAt"]
        assert payload_items[1]["foodType"] == "CUSTOM_ITEM"
        assert payload_items[1]["name"] == "Updated meal"
        assert payload_items[1]["energy"] == pytest.approx(400.0)
        assert client.last_request_kwargs is not None
        assert client.last_request_kwargs["params"] == {"synchronous": "true"}

    def test_replace_keeps_an_eaten_row_eaten(self) -> None:
        client = FakeClient(_day_with_product())
        client.planner.update_day_item(TEST_USER, TEST_DAY, meal_type="breakfast", item_id="item-1", eaten=True)
        replaced = client.planner.replace_day_item_with_custom_item(
            TEST_USER, TEST_DAY, meal_type="breakfast", item_id="item-1", name="Jajecznica", calories=200, protein_g=12, fat_g=15, carbs_g=2
        )
        (row,) = client.day_payload["dietPlan"]["breakfast"]["items"]
        assert (row["planDayDietItemId"], row["name"], row["eaten"]) == (replaced["row_id"], "Jajecznica", True)

    def test_move_checks_the_target_meal_on_the_target_day_before_syncing(self) -> None:
        client = FakeClient(_day_with_product())
        with pytest.raises(FitatuNotFoundError, match="dinner"):
            client.planner.move_day_item(
                TEST_USER, TEST_DAY, from_meal_type="breakfast", item_id="item-1", to_day="2026-01-16", to_meal_type="dinner"
            )
        assert client.request_log == [
            ("GET", f"/diet-and-activity-plan/{TEST_USER}/day/2026-01-15"),
            ("GET", f"/diet-and-activity-plan/{TEST_USER}/day/2026-01-16"),
        ]

    def test_move_to_another_day_keeps_the_row_meal_by_default(self) -> None:
        client = FakeClient(_day_with_product())
        result = client.planner.move_day_item(
            TEST_USER, TEST_DAY, from_meal_type="lunch", item_id="item-1", to_day="2026-01-16"
        )
        assert result["ok"] is True
        assert result["to_meal"] == "breakfast"
        # The fake seeds every day with the same rows, so the next day also has its own item-1.
        moved = client.day_view("2026-01-16")["dietPlan"]["breakfast"]["items"]
        assert result["row_id"] in {row["planDayDietItemId"] for row in moved}
        assert client.day_payload["dietPlan"]["breakfast"]["items"] == []

    def test_move_and_replace_report_not_ok_when_the_day_did_not_change(self) -> None:
        planner = IgnoringFakeClient(_day_with_product()).planner
        moved = planner.move_day_item(TEST_USER, TEST_DAY, from_meal_type="breakfast", item_id="item-1", to_meal_type="lunch")
        replaced = planner.replace_day_item_with_custom_item(
            TEST_USER, TEST_DAY, meal_type="breakfast", item_id="item-1", name="X", calories=1, protein_g=0, fat_g=0, carbs_g=0
        )
        assert moved["ok"] is False
        assert replaced["ok"] is False


def _day_with_a_meal_to_copy() -> dict[str, Any]:
    """Breakfast with an eaten product (item-1), a recipe (recipe-1) and a custom row (custom-1)."""
    day = _day_with_product()
    breakfast = day["dietPlan"]["breakfast"]["items"]
    breakfast[0]["eaten"] = True
    breakfast += [_day_with_recipe()["dietPlan"]["breakfast"]["items"][0], _day_with_custom_item()["dietPlan"]["breakfast"]["items"][0]]
    return day


class TestCopyMeal:
    NEXT_DAY = "2026-01-16"

    def test_copies_every_row_in_its_write_shape_in_one_sync(self) -> None:
        client = FakeClient(_day_with_a_meal_to_copy())
        seed = _new_row_id()
        result = client.planner.copy_meal(
            TEST_USER, TEST_DAY, from_meal_type="breakfast", to_day=self.NEXT_DAY, to_meal_type="lunch", row_id=seed
        )
        assert result["ok"] is True and (result["from_meal"], result["to_date"], result["to_meal"]) == ("breakfast", self.NEXT_DAY, "lunch")
        [payload] = client.sync_payloads
        product, recipe, custom = payload[self.NEXT_DAY]["dietPlan"]["lunch"]["items"]
        assert result["row_ids"] == [product["planDayDietItemId"], recipe["planDayDietItemId"], custom["planDayDietItemId"]]
        assert result["row_ids"][0] == seed and len(set(result["row_ids"])) == 3
        assert all(uuid.UUID(row_id).version == 1 for row_id in result["row_ids"])
        assert {key: value for key, value in product.items() if key != "planDayDietItemId"} == {
            "foodType": "PRODUCT", "productId": 1001, "measureId": 11, "measureQuantity": 1.0, "ingredientsServing": None,
            "source": "API", "eaten": True,  # kept from the row
        }
        assert {key: value for key, value in recipe.items() if key != "planDayDietItemId"} == {
            "foodType": "RECIPE", "recipeId": 9999, "measureId": 39, "measureQuantity": 1.0, "ingredientsServing": 1,
            "source": "API", "eaten": False,
        }
        assert (custom["foodType"], custom["name"], custom["energy"], custom["protein"], custom["fat"], custom["carbohydrate"]) == (
            "CUSTOM_ITEM", "My custom", 200, 10, 5, 25
        )
        lunch = client.day_view(self.NEXT_DAY)["dietPlan"]["lunch"]["items"]
        assert [row["planDayDietItemId"] for row in lunch] == result["row_ids"]
        assert len(client.day_payload["dietPlan"]["breakfast"]["items"]) == 3  # the source is left as it was

    def test_copies_only_the_listed_rows_with_the_given_eaten(self) -> None:
        client = FakeClient(_day_with_a_meal_to_copy())
        result = client.planner.copy_meal(
            TEST_USER, TEST_DAY, from_meal_type="breakfast", to_day=TEST_DAY, to_meal_type="snack", item_ids=["custom-1", "item-1"], eaten=False
        )
        snack = client.day_payload["dietPlan"]["snack"]["items"]
        assert [(row["planDayDietItemId"], row["foodType"], row["eaten"]) for row in snack] == [
            (result["row_ids"][0], "PRODUCT", False),  # in the meal's order
            (result["row_ids"][1], "CUSTOM_ITEM", False),
        ]

    def test_a_repeat_with_the_same_row_id_writes_nothing_new(self) -> None:
        client = FakeClient(_day_with_a_meal_to_copy())
        seed = _new_row_id()
        first, again = (
            client.planner.copy_meal(TEST_USER, TEST_DAY, from_meal_type="breakfast", to_day=self.NEXT_DAY, to_meal_type="lunch", row_id=seed)
            for _ in range(2)  # e.g. the first reply was lost
        )
        assert again["ok"] is True and again["row_ids"] == first["row_ids"]
        lunch = client.day_view(self.NEXT_DAY)["dietPlan"]["lunch"]["items"]
        assert [row["planDayDietItemId"] for row in lunch] == first["row_ids"]

    def test_a_copy_id_that_another_row_has_is_refused_before_anything_is_sent(self) -> None:
        client = FakeClient(_day_with_a_meal_to_copy())
        seed = _new_row_id()
        arguments: dict[str, Any] = {"from_meal_type": "breakfast", "to_day": self.NEXT_DAY, "to_meal_type": "lunch", "row_id": seed}
        first = client.planner.copy_meal(TEST_USER, TEST_DAY, **arguments)
        # the copied recipe was marked eaten since, e.g. on the phone
        client.planner.update_day_item(TEST_USER, self.NEXT_DAY, meal_type="lunch", item_id=first["row_ids"][1], eaten=True)
        with pytest.raises(FitatuValidationError, match=rf"row_id {first['row_ids'][1]} already belongs to another row.*do not add it again"):
            client.planner.copy_meal(TEST_USER, TEST_DAY, **arguments)
        assert len(client.sync_payloads) == 2  # the copy and the update

    def test_a_row_without_a_write_shape_is_skipped_and_reported(self) -> None:
        day = _day_with_a_meal_to_copy()
        ai_row = {**day["dietPlan"]["breakfast"]["items"][1], "planDayDietItemId": "ai-1", "foodType": "RECIPE_AI"}
        day["dietPlan"]["lunch"]["items"] = [ai_row]
        client = FakeClient(day)
        result = client.planner.copy_meal(TEST_USER, TEST_DAY, from_meal_type="lunch", to_day=self.NEXT_DAY)
        assert (result["ok"], result["row_ids"], [row["planDayDietItemId"] for row in result["skipped"]]) == (False, [], ["ai-1"])
        assert client.sync_payloads == []

    def test_mistakes_raise_before_anything_is_sent(self) -> None:
        client = FakeClient(_day_with_a_meal_to_copy())
        planner = client.planner
        with pytest.raises(FitatuValidationError, match="not onto itself"):
            planner.copy_meal(TEST_USER, TEST_DAY, from_meal_type="breakfast", to_day=TEST_DAY)
        with pytest.raises(FitatuNotFoundError, match="not in breakfast.*ghost.*item-1"):
            planner.copy_meal(TEST_USER, TEST_DAY, from_meal_type="breakfast", to_day=self.NEXT_DAY, item_ids=["item-1", "ghost"])
        with pytest.raises(FitatuNotFoundError, match="lunch on 2026-01-15 has no rows"):
            planner.copy_meal(TEST_USER, TEST_DAY, from_meal_type="lunch", to_day=self.NEXT_DAY)
        with pytest.raises(FitatuNotFoundError, match="dinner"):
            planner.copy_meal(TEST_USER, TEST_DAY, from_meal_type="breakfast", to_day=self.NEXT_DAY, to_meal_type="dinner")
        with pytest.raises(FitatuValidationError, match="at least one row"):
            planner.copy_meal(TEST_USER, TEST_DAY, from_meal_type="breakfast", to_day=self.NEXT_DAY, item_ids=[])
        with pytest.raises(FitatuValidationError, match="UUID v1"):
            planner.copy_meal(TEST_USER, TEST_DAY, from_meal_type="breakfast", to_day=self.NEXT_DAY, row_id=str(uuid.uuid4()))
        assert client.sync_payloads == []


class TestAddFoods:
    ITEMS: list[dict[str, Any]] = [{"product_id": 1001, "measure_id": 11, "measure_quantity": 2}, {"recipe_id": 9999}]

    def test_adds_every_item_in_one_sync_as_the_single_adds_send_it(self) -> None:
        client = FakeClient(_empty_day())
        seed = _new_row_id()
        result = client.planner.add_foods_to_day_meal(TEST_USER, TEST_DAY, meal_type="lunch", items=self.ITEMS, eaten=True, row_id=seed)
        [payload] = client.sync_payloads
        sent = payload[TEST_DAY.isoformat()]["dietPlan"]["lunch"]["items"]
        assert (result["ok"], result["meal"], result["added_items"]) == (True, "lunch", sent)
        assert result["row_ids"] == [row["planDayDietItemId"] for row in sent] and result["row_ids"][0] == seed
        single = FakeClient(_empty_day()).planner
        common: dict[str, Any] = {"meal_type": "lunch", "eaten": True}
        assert sent == [
            single.add_product_to_day_meal(TEST_USER, TEST_DAY, product_id=1001, measure_id=11, measure_quantity=2, row_id=seed, **common)["added_item"],
            single.add_recipe_to_day_meal(TEST_USER, TEST_DAY, recipe_id=9999, row_id=result["row_ids"][1], **common)["added_item"],
        ]
        assert uuid.UUID(result["row_ids"][1]).version == 1

    def test_a_repeat_with_the_same_row_id_writes_the_same_rows(self) -> None:
        client = FakeClient(_empty_day())
        arguments: dict[str, Any] = {"meal_type": "lunch", "items": self.ITEMS, "row_id": _new_row_id()}
        first, again = (client.planner.add_foods_to_day_meal(TEST_USER, TEST_DAY, **arguments) for _ in range(2))
        assert again["row_ids"] == first["row_ids"]
        assert [row["planDayDietItemId"] for row in client.day_payload["dietPlan"]["lunch"]["items"]] == first["row_ids"]

    def test_an_id_another_row_has_is_refused(self) -> None:
        client = FakeClient(_empty_day())
        seed = _new_row_id()
        client.planner.add_custom_item_to_day_meal(
            TEST_USER, TEST_DAY, meal_type="lunch", name="X", calories=1, protein_g=0, fat_g=0, carbs_g=0, row_id=seed
        )
        with pytest.raises(FitatuValidationError, match=f"row_id {seed} already belongs to another row"):
            client.planner.add_foods_to_day_meal(TEST_USER, TEST_DAY, meal_type="lunch", items=self.ITEMS, row_id=seed)
        assert len(client.sync_payloads) == 1

    def test_mistakes_raise_before_anything_is_sent(self) -> None:
        client = FakeClient(_empty_day())
        planner = client.planner
        product = self.ITEMS[0]
        with pytest.raises(FitatuValidationError, match=r"items\[1\]: measure_quantity must be > 0"):
            planner.add_foods_to_day_meal(TEST_USER, TEST_DAY, meal_type="lunch", items=[product, {**product, "measure_quantity": 0}])
        with pytest.raises(FitatuValidationError, match=r"items\[0\]: .*unexpected keyword argument 'unit'"):
            planner.add_foods_to_day_meal(TEST_USER, TEST_DAY, meal_type="lunch", items=[{**product, "unit": "g"}])
        with pytest.raises(FitatuValidationError, match=r"items\[0\]: .*measure_id"):
            planner.add_foods_to_day_meal(TEST_USER, TEST_DAY, meal_type="lunch", items=[{"product_id": 1001}])
        with pytest.raises(FitatuValidationError, match="at least one"):
            planner.add_foods_to_day_meal(TEST_USER, TEST_DAY, meal_type="lunch", items=[])
        with pytest.raises(FitatuValidationError, match="UUID v1"):
            planner.add_foods_to_day_meal(TEST_USER, TEST_DAY, meal_type="lunch", items=self.ITEMS, row_id=str(uuid.uuid4()))
        assert client.requests == []
        with pytest.raises(FitatuNotFoundError, match="dinner"):
            planner.add_foods_to_day_meal(TEST_USER, TEST_DAY, meal_type="dinner", items=self.ITEMS)
        assert client.sync_payloads == []


class TestWriteValidation:
    @pytest.mark.parametrize("key", ["planDayDietItemId", "deletedAt", "foodType", "productId", "recipeId"])
    def test_patch_cannot_change_row_identity(self, key: str) -> None:
        client = FakeClient(_day_with_product())
        with pytest.raises(FitatuValidationError, match=key):
            client.planner.update_day_item(TEST_USER, TEST_DAY, meal_type="breakfast", item_id="item-1", patch={key: "x"})
        assert client.requests == []

    @pytest.mark.parametrize("quantity", [0, -1, "abc", float("nan")])
    def test_quantities_must_be_positive(self, quantity: Any) -> None:
        client = FakeClient(_day_with_product())
        planner = client.planner
        calls = (
            lambda: planner.add_product_to_day_meal(TEST_USER, TEST_DAY, meal_type="breakfast", product_id=1, measure_id=1, measure_quantity=quantity),
            lambda: planner.add_recipe_to_day_meal(TEST_USER, TEST_DAY, meal_type="breakfast", recipe_id=1, measure_quantity=quantity),
            lambda: planner.update_day_item(TEST_USER, TEST_DAY, meal_type="breakfast", item_id="item-1", measure_quantity=quantity),
            lambda: planner.update_day_item(
                TEST_USER, TEST_DAY, meal_type="breakfast", item_id="item-1", patch={"measureQuantity": quantity}
            ),
        )
        for call in calls:
            with pytest.raises(FitatuValidationError, match="measure_quantity|measureQuantity"):
                call()
        assert client.requests == []

    def test_custom_item_values_are_not_clamped(self) -> None:
        client = FakeClient(_empty_day())
        with pytest.raises(FitatuValidationError, match="fat_g must be >= 0"):
            client.planner.add_custom_item_to_day_meal(
                TEST_USER, TEST_DAY, meal_type="breakfast", name="X", calories=100, protein_g=1, fat_g=-5, carbs_g=1
            )
        with pytest.raises(FitatuValidationError, match="calories"):
            client.planner.replace_day_item_with_custom_item(
                TEST_USER, TEST_DAY, meal_type="breakfast", item_id="x", name="X", calories=-1, protein_g=0, fat_g=0, carbs_g=0
            )
        assert client.requests == []


class TestOneNamePerConcept:
    def test_meal_and_row_are_keyword_only(self) -> None:
        planner = FakeClient(_day_with_product()).planner
        with pytest.raises(TypeError):
            planner.get_meal(TEST_USER, TEST_DAY, "breakfast")  # type: ignore[misc]
        with pytest.raises(TypeError):
            planner.remove_day_item(TEST_USER, TEST_DAY, "breakfast", "item-1")  # type: ignore[misc]

    def test_add_move_and_replace_return_the_new_row_id(self) -> None:
        client = FakeClient(_empty_day())
        planner = client.planner
        added = planner.add_custom_item_to_day_meal(
            TEST_USER, TEST_DAY, meal_type="lunch", name="Soup", calories=90, protein_g=3, fat_g=2, carbs_g=12
        )
        moved = planner.move_day_item(
            TEST_USER, TEST_DAY, from_meal_type="lunch", item_id=added["row_id"], to_meal_type="snack"
        )
        replaced = planner.replace_day_item_with_custom_item(
            TEST_USER, TEST_DAY, meal_type="snack", item_id=moved["row_id"], name="Stew", calories=200, protein_g=9, fat_g=8, carbs_g=20
        )
        assert added["row_id"] == added["added_item"]["planDayDietItemId"]
        assert len({added["row_id"], moved["row_id"], replaced["row_id"]}) == 3
        snack = client.day_payload["dietPlan"]["snack"]["items"]
        assert [row["planDayDietItemId"] for row in snack] == [replaced["row_id"]]


# ---------------------------------------------------------------------------
# TestClassifyDayItemForRemoval
# ---------------------------------------------------------------------------


class TestClassifyDayItemForRemoval:
    def test_product_classified_normal(self) -> None:
        client = FakeClient(_day_with_product())
        result = client.planner.classify_day_item_for_removal(TEST_USER, TEST_DAY, meal_type="breakfast", item_id="item-1")
        assert result["found"] is True
        assert result["resolved_kind"] == "normal_item"

    def test_custom_add_classified_correctly(self) -> None:
        client = FakeClient(_day_with_custom_item(source="WEB", quantity=100.0))
        result = client.planner.classify_day_item_for_removal(TEST_USER, TEST_DAY, meal_type="breakfast", item_id="custom-1")
        assert result["resolved_kind"] == "custom_add_item"

    def test_custom_api_small_qty_classified_recipe_like(self) -> None:
        client = FakeClient(_day_with_custom_item(source="API", quantity=1.0))
        result = client.planner.classify_day_item_for_removal(TEST_USER, TEST_DAY, meal_type="breakfast", item_id="custom-1")
        assert result["resolved_kind"] == "custom_recipe_item"

    def test_not_found_lists_the_day_rows(self) -> None:
        client = FakeClient(_day_with_product())
        with pytest.raises(FitatuNotFoundError, match=r"'ghost'; rows on this day: item-1 \(breakfast"):
            client.planner.classify_day_item_for_removal(TEST_USER, TEST_DAY, meal_type="breakfast", item_id="ghost")

    def test_item_key_in_result(self) -> None:
        client = FakeClient(_day_with_product())
        result = client.planner.classify_day_item_for_removal(TEST_USER, TEST_DAY, meal_type="breakfast", item_id="item-1")
        assert result["item"] is not None
        assert result["item"]["productId"] == 1001


# ---------------------------------------------------------------------------
# TestRemoveDayItem
# ---------------------------------------------------------------------------


class TestRemoveDayItem:
    def test_removes_product_item(self) -> None:
        client = FakeClient(_day_with_product())
        result = client.planner.remove_day_item(TEST_USER, TEST_DAY, meal_type="breakfast", item_id="item-1")
        assert result["ok"] is True
        assert client.day_payload["dietPlan"]["breakfast"]["items"] == []

    def test_item_not_found_returns_ok_false(self) -> None:
        client = FakeClient(_day_with_product())
        result = client.planner.remove_day_item(TEST_USER, TEST_DAY, meal_type="breakfast", item_id="ghost")
        assert result["ok"] is False
        assert result["reason"] == "item_not_found"
        assert result["message"] == "item not found: 'ghost'; rows on this day: item-1 (breakfast, 'Oats', measureQuantity=1.0)"
        assert client.sync_payloads == []

    def test_raises_for_missing_meal(self) -> None:
        client = FakeClient(_day_with_product())
        with pytest.raises(FitatuApiError, match="meal not found"):
            client.planner.remove_day_item(TEST_USER, TEST_DAY, meal_type="supper", item_id="item-1")

    def test_remove_by_product_id(self) -> None:
        client = FakeClient(_day_with_product())
        result = client.planner.remove_day_item(TEST_USER, TEST_DAY, meal_type="breakfast", item_id=1001)
        assert result["ok"] is True

    def test_sends_the_row_unchanged_plus_deleted_at(self) -> None:
        client = FakeClient(_day_with_product(quantity=2.0))
        client.planner.remove_day_item(TEST_USER, TEST_DAY, meal_type="breakfast", item_id="item-1")
        [sent] = client.last_sync_payload[TEST_DAY.isoformat()]["dietPlan"]["breakfast"]["items"]
        assert sent["deletedAt"]
        assert sent["measureQuantity"] == 2.0
        assert sent.get("visible") is not False

    def test_row_is_kept_server_side_with_deleted_at(self) -> None:
        client = FakeClient(_day_with_product())
        client.planner.remove_day_item(TEST_USER, TEST_DAY, meal_type="breakfast", item_id="item-1")
        assert client.all_rows(TEST_DAY.isoformat())["item-1"]["deletedAt"]


# ---------------------------------------------------------------------------
# TestRemovalChecksAndRollback
# ---------------------------------------------------------------------------


class TestRemovalChecksAndRollback:
    def test_every_kind_is_deleted_with_deleted_at(self) -> None:
        for day, item_id in ((_day_with_product(), "item-1"), (_day_with_custom_item(), "custom-1")):
            client = FakeClient(day)
            result = client.planner.remove_day_item(TEST_USER, TEST_DAY, meal_type="breakfast", item_id=item_id)
            assert result["ok"] is True
            assert result["cleanup_mode"] == "removed"

    def test_remove_checks_the_whole_day_when_the_row_is_in_another_meal(self) -> None:
        day = _day_with_product(meal="dinner")
        day["dietPlan"]["breakfast"] = {"items": []}
        client = IgnoringFakeClient(day)

        result = client.planner.remove_day_item(TEST_USER, TEST_DAY, meal_type="breakfast", item_id="item-1")

        assert result["ok"] is False
        assert result["removed_absent_after_sync"] is False

    def test_rollback_sends_only_a_deleted_at_marker(self) -> None:
        day = _day_with_product()
        day["dietPlan"]["lunch"]["items"] = [
            {"planDayDietItemId": "keep", "foodType": "PRODUCT", "productId": 2, "measureId": 1, "measureQuantity": 1}
        ]
        client = FakeClient(day)

        result = client.planner.rollback_added_item(
            TEST_USER, TEST_DAY, meal_type="breakfast", item_id="item-1"
        )

        assert result["ok"] is True
        assert result["cleanup_mode"] == "removed"
        sent = client.last_sync_payload[TEST_DAY.isoformat()]
        assert list(sent) == ["dietPlan"]
        assert [row["planDayDietItemId"] for row in sent["dietPlan"]["breakfast"]["items"]] == ["item-1"]
        assert sent["dietPlan"]["breakfast"]["items"][0]["deletedAt"]
        assert [row["planDayDietItemId"] for row in client.day_payload["dietPlan"]["lunch"]["items"]] == ["keep"]

    def test_a_repeated_rollback_never_deletes_the_user_row_of_the_same_product(self) -> None:
        client = FakeClient(_day_with_product())  # user's own oats: item-1, product 1001, in breakfast
        added = client.planner.add_product_to_day_meal(TEST_USER, TEST_DAY, meal_type="breakfast", product_id=1001, measure_id=11)

        first = client.planner.rollback_added_item(TEST_USER, TEST_DAY, meal_type="breakfast", item_id=added["row_id"])
        again = client.planner.rollback_added_item(TEST_USER, TEST_DAY, meal_type="breakfast", item_id=added["row_id"])

        assert (first["ok"], first["removed_id"]) == (True, added["row_id"])
        assert (again["ok"], again["reason"]) == (False, "item_not_found")
        assert [row["planDayDietItemId"] for row in client.day_payload["dietPlan"]["breakfast"]["items"]] == ["item-1"]

    @pytest.mark.parametrize("item_id", [_new_row_id(), "1001"])
    def test_rollback_of_an_add_that_never_landed_leaves_the_day_alone(self, item_id: str) -> None:
        client = FakeClient(_day_with_product())
        result = client.planner.rollback_added_item(TEST_USER, TEST_DAY, meal_type="breakfast", item_id=item_id)
        assert result["ok"] is False
        assert client.sync_payloads == []

    def test_rollback_does_not_reach_into_other_meals(self) -> None:
        day = _day_with_product(item_id="lunch-oats")
        day["dietPlan"]["lunch"]["items"] = day["dietPlan"]["breakfast"]["items"]
        day["dietPlan"]["breakfast"] = {"items": []}
        client = FakeClient(day)

        result = client.planner.rollback_added_item(TEST_USER, TEST_DAY, meal_type="breakfast", item_id="lunch-oats")

        assert result["ok"] is False
        assert [row["planDayDietItemId"] for row in client.day_payload["dietPlan"]["lunch"]["items"]] == ["lunch-oats"]

    def test_lookup_prefers_the_requested_meal(self) -> None:
        day = _day_with_product(item_id="breakfast-oats")
        day["dietPlan"]["lunch"]["items"] = [
            {"planDayDietItemId": "lunch-oats", "foodType": "PRODUCT", "productId": 1001, "measureId": 11, "measureQuantity": 1}
        ]
        client = FakeClient(day)
        result = client.planner.remove_day_item(TEST_USER, TEST_DAY, meal_type="lunch", item_id=1001)
        assert result["removed_id"] == "lunch-oats"

    def test_a_product_logged_twice_is_never_picked_by_guess(self) -> None:
        day = _day_with_product(item_id="oats-1")
        day["dietPlan"]["breakfast"]["items"].append({**day["dietPlan"]["breakfast"]["items"][0], "planDayDietItemId": "oats-2"})
        client = FakeClient(day)
        planner = client.planner
        calls = (
            lambda: planner.remove_day_item(TEST_USER, TEST_DAY, meal_type="breakfast", item_id=1001),
            lambda: planner.update_day_item(TEST_USER, TEST_DAY, meal_type="breakfast", item_id=1001, eaten=True),
            lambda: planner.move_day_item(TEST_USER, TEST_DAY, from_meal_type="breakfast", item_id=1001, to_meal_type="lunch"),
        )
        for call in calls:
            with pytest.raises(FitatuValidationError, match=r"matches 2 rows in breakfast.*oats-1 .*oats-2 "):
                call()
        assert client.sync_payloads == []
        assert planner.remove_day_item(TEST_USER, TEST_DAY, meal_type="breakfast", item_id="oats-2")["removed_id"] == "oats-2"

    def test_a_row_id_is_found_in_any_meal_but_a_product_id_only_in_the_given_one(self) -> None:
        client = FakeClient(_day_with_product())  # item-1 / product 1001 in breakfast
        planner = client.planner
        assert planner.update_day_item(TEST_USER, TEST_DAY, meal_type="lunch", item_id="item-1", eaten=True)["meal"] == "breakfast"
        with pytest.raises(FitatuNotFoundError):
            planner.update_day_item(TEST_USER, TEST_DAY, meal_type="lunch", item_id=1001, eaten=True)
        assert planner.remove_day_item(TEST_USER, TEST_DAY, meal_type="lunch", item_id=1001)["reason"] == "item_not_found"

    def test_every_write_is_synchronous(self) -> None:
        client = FakeClient(_day_with_product())
        planner = client.planner
        planner.add_product_to_day_meal(TEST_USER, TEST_DAY, meal_type="breakfast", product_id=5, measure_id=1)
        planner.update_day_item(TEST_USER, TEST_DAY, meal_type="breakfast", item_id="item-1", eaten=True)
        planner.remove_day_item(TEST_USER, TEST_DAY, meal_type="breakfast", item_id="item-1")
        posts = [r for r in client.requests if r["method"] == "POST"]
        assert len(posts) == 3
        assert all(r["params"] == {"synchronous": "true"} for r in posts)

# ---------------------------------------------------------------------------
# TestRemoveDayItemsByKind
# ---------------------------------------------------------------------------


class TestRemoveDayItemsByKind:
    def _multi_item_payload(self) -> dict[str, Any]:
        return {
            "dietPlan": {
                "breakfast": {
                    "items": [
                        {"planDayDietItemId": "p1", "foodType": "PRODUCT", "productId": 1, "source": "API", "measureQuantity": 1.0},
                        {"planDayDietItemId": "p2", "foodType": "PRODUCT", "productId": 2, "source": "API", "measureQuantity": 1.0},
                    ]
                }
            },
            "toiletItems": [],
            "note": None,
            "tagsIds": [],
        }

    def test_removes_all_matching_kind(self) -> None:
        client = FakeClient(self._multi_item_payload())
        result = client.planner.remove_day_items_by_kind(TEST_USER, TEST_DAY, item_kind="normal_item", all_meals=True)
        assert result["targeted_count"] == 2
        assert result["removed_count"] == 2

    def test_invalid_kind_raises(self) -> None:
        client = FakeClient(self._multi_item_payload())
        with pytest.raises(FitatuApiError, match="item_kind must be"):
            client.planner.remove_day_items_by_kind(TEST_USER, TEST_DAY, item_kind="unknown_kind", all_meals=True)

    def test_max_items_limits_removal(self) -> None:
        client = FakeClient(self._multi_item_payload())
        result = client.planner.remove_day_items_by_kind(TEST_USER, TEST_DAY, item_kind="normal_item", all_meals=True, max_items=1)
        assert result["targeted_count"] == 1

    def test_auto_kind_targets_all(self) -> None:
        client = FakeClient(self._multi_item_payload())
        result = client.planner.remove_day_items_by_kind(TEST_USER, TEST_DAY, item_kind="auto", all_meals=True)
        assert result["targeted_count"] == 2

    def test_meal_filter_applied(self) -> None:
        payload = deepcopy(self._multi_item_payload())
        payload["dietPlan"]["lunch"] = {
            "items": [{"planDayDietItemId": "p3", "foodType": "PRODUCT", "productId": 3, "source": "API", "measureQuantity": 1.0}]
        }
        client = FakeClient(payload)
        result = client.planner.remove_day_items_by_kind(TEST_USER, TEST_DAY, item_kind="normal_item", meal_type="breakfast")
        assert result["targeted_count"] == 2

    @pytest.mark.parametrize("kind", ["auto", "normal_item", "custom_add_item", "custom_recipe_item"])
    def test_the_whole_day_must_be_asked_for(self, kind: str) -> None:
        client = FakeClient(self._multi_item_payload())
        with pytest.raises(FitatuValidationError, match=r"or all_meals=True for the whole day$"):
            client.planner.remove_day_items_by_kind(TEST_USER, TEST_DAY, item_kind=kind)
        with pytest.raises(FitatuValidationError, match="not both"):
            client.planner.remove_day_items_by_kind(TEST_USER, TEST_DAY, item_kind=kind, meal_type="breakfast", all_meals=True)
        assert client.sync_payloads == []

    def test_row_ids_limit_the_targets(self) -> None:
        client = FakeClient(self._multi_item_payload())
        result = client.planner.remove_day_items_by_kind(
            TEST_USER, TEST_DAY, item_kind="auto", all_meals=True, row_ids=["p2", "not-there"]
        )
        assert [attempt["item_id"] for attempt in result["attempts"]] == ["p2"]
        assert (result["ok"], result["missing_row_ids"]) == (False, ["not-there"])
        assert [row["planDayDietItemId"] for row in client.day_payload["dietPlan"]["breakfast"]["items"]] == ["p1"]

    def test_a_requested_row_of_another_kind_is_reported_not_skipped(self) -> None:
        client = FakeClient(_empty_day())
        added = client.planner.add_recipe_to_day_meal(TEST_USER, TEST_DAY, meal_type="lunch", recipe_id=9999)
        for dry_run in (True, False):
            result = client.planner.remove_day_items_by_kind(
                TEST_USER, TEST_DAY, item_kind="custom_recipe_item", all_meals=True, row_ids=[added["row_id"]], dry_run=dry_run
            )
            assert (result["ok"], result["targeted_count"], result["missing_row_ids"]) == (False, 0, [added["row_id"]])
        assert [row["planDayDietItemId"] for row in client.day_payload["dietPlan"]["lunch"]["items"]] == [added["row_id"]]

    def test_dry_run_lists_the_targets_and_sends_nothing(self) -> None:
        client = FakeClient(self._multi_item_payload())
        result = client.planner.remove_day_items_by_kind(
            TEST_USER, TEST_DAY, item_kind="normal_item", meal_type="breakfast", max_items=1, dry_run=True
        )
        assert result["dry_run"] is True
        assert [row["item_id"] for row in result["targets"]] == ["p1"]
        assert client.sync_payloads == []
        assert len(client.day_payload["dietPlan"]["breakfast"]["items"]) == 2

    def test_negative_max_items_is_rejected(self) -> None:
        client = FakeClient(self._multi_item_payload())
        with pytest.raises(FitatuValidationError, match="max_items"):
            client.planner.remove_day_items_by_kind(TEST_USER, TEST_DAY, item_kind="auto", all_meals=True, max_items=-1)

    def test_empty_day_returns_zero(self) -> None:
        client = FakeClient(_empty_day())
        result = client.planner.remove_day_items_by_kind(TEST_USER, TEST_DAY, item_kind="auto", all_meals=True)
        assert result["targeted_count"] == 0
        assert result["ok"] is True


# ---------------------------------------------------------------------------
# TestResolveProductMeasure
# ---------------------------------------------------------------------------


class TestResolveProductMeasure:
    def _product_with_measures(
        self,
        product_id: int = 1001,
        measures: list[dict[str, Any]] | None = None,
        simple_measures: list[dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        return {
            "id": product_id,
            "measures": measures or [],
            "simpleMeasures": simple_measures or [],
        }

    def test_direct_unit_match_g(self) -> None:
        product = self._product_with_measures(
            measures=[{"id": 10, "name": "g", "weightPerUnit": 1.0}]
        )
        client = FakeClient(_empty_day(), product_details={"1001": product})
        result = client.planner.resolve_product_measure(
            product_id=1001, requested_amount=100, requested_unit="g"
        )
        assert result["strategy"] == "direct_unit_match"
        assert result["measure_id"] == 10
        assert result["measure_quantity"] == pytest.approx(100.0)

    def test_converted_from_weight(self) -> None:
        product = self._product_with_measures(
            measures=[{"id": 20, "name": "opakowanie", "weightPerUnit": 200.0}]
        )
        client = FakeClient(_empty_day(), product_details={"1001": product})
        result = client.planner.resolve_product_measure(
            product_id=1001, requested_amount=100, requested_unit="g"
        )
        assert result["strategy"] == "converted_from_weight"
        assert result["measure_quantity"] == pytest.approx(0.5)

    def test_direct_match_ml(self) -> None:
        product = self._product_with_measures(
            measures=[{"id": 30, "name": "ml", "weightPerUnit": 0}],
            simple_measures=[{"id": 30, "portion": 1, "capacity": 1}],
        )
        client = FakeClient(_empty_day(), product_details={"1001": product})
        result = client.planner.resolve_product_measure(
            product_id=1001, requested_amount=200, requested_unit="ml"
        )
        assert result["strategy"] == "direct_unit_match"
        assert result["measure_quantity"] == pytest.approx(200.0)

    def test_strict_measure_raises_on_no_match(self) -> None:
        # No weight info on any measure, no "ml"-named measure → strict ml resolution must raise
        product = self._product_with_measures(
            measures=[{"id": 5, "name": "sztuka", "weightPerUnit": 0}]
        )
        client = FakeClient(_empty_day(), product_details={"1001": product})
        with pytest.raises(FitatuApiError, match="Cannot resolve unit"):
            client.planner.resolve_product_measure(
                product_id=1001, requested_amount=200, requested_unit="ml", strict_measure=True
            )

    def test_fallback_to_first_measure_when_not_strict(self) -> None:
        product = self._product_with_measures(
            measures=[{"id": 5, "name": "sztuka", "weightPerUnit": 50.0}]
        )
        client = FakeClient(_empty_day(), product_details={"1001": product})
        result = client.planner.resolve_product_measure(
            product_id=1001, requested_amount=100, requested_unit="ml", strict_measure=False
        )
        assert result["strategy"] == "fallback_first_measure"
        assert len(result["warnings"]) > 0

    def test_a_unit_without_a_size_never_falls_back_to_another_measure(self) -> None:
        # 2 tablespoons cannot be expressed in 100 g packages without knowing a tablespoon's weight.
        product = self._product_with_measures(measures=[{"id": 2, "name": "opakowanie", "weightPerUnit": 100.0}])
        client = FakeClient(_empty_day(), product_details={"1001": product})
        with pytest.raises(FitatuValidationError, match="its measures: opakowanie"):
            client.planner.resolve_product_measure(
                product_id=1001,
                requested_amount=2,
                requested_unit="tbsp",
                strict_measure=False,
                search_product={"measure": {"measureId": 2, "measureName": "opakowanie"}},
            )

    def test_fallback_search_measure_used(self) -> None:
        product = self._product_with_measures(
            measures=[{"id": 5, "name": "sztuka", "weightPerUnit": 50.0}]
        )
        search_item = {"measure": {"defaultMeasureId": 99, "measureName": "opak"}}
        client = FakeClient(_empty_day(), product_details={"1001": product})
        result = client.planner.resolve_product_measure(
            product_id=1001,
            requested_amount=1,
            requested_unit="ml",
            strict_measure=False,
            search_product=search_item,
        )
        assert result["strategy"] == "fallback_search_measure"
        assert result["measure_id"] == 99

    def test_direct_match_from_simple_measures(self) -> None:
        product = self._product_with_measures(
            measures=[{"id": 1, "name": "g", "weightPerUnit": 1.0}],
            simple_measures=[{"id": 19, "name": "łyżeczka", "weight": 5}],
        )
        client = FakeClient(_empty_day(), product_details={"1001": product})
        result = client.planner.resolve_product_measure(
            product_id=1001,
            requested_amount=2,
            requested_unit="lyzeczka",
        )
        assert result["strategy"] == "direct_unit_match_simpleMeasures"
        assert result["measure_id"] == 19
        assert result["measure_quantity"] == pytest.approx(2.0)

    def test_converts_grams_through_simple_measure_when_no_gram_measure(self) -> None:
        product = self._product_with_measures(
            measures=[{"id": 19, "name": "łyżeczka", "weightPerUnit": 0}],
            simple_measures=[{"id": 19, "name": "łyżeczka", "weight": 5}],
        )
        client = FakeClient(_empty_day(), product_details={"1001": product})
        result = client.planner.resolve_product_measure(
            product_id=1001,
            requested_amount=200,
            requested_unit="g",
        )
        assert result["strategy"] == "converted_from_weight"
        assert result["measure_id"] == 19
        assert result["measure_name"] == "łyżeczka"
        assert result["measure_quantity"] == pytest.approx(40.0)

    def test_converts_ml_through_simple_measure_capacity(self) -> None:
        product = self._product_with_measures(
            measures=[{"id": 32, "name": "szklanka", "weightPerUnit": 0}],
            simple_measures=[{"id": 32, "name": "szklanka", "capacity": 250}],
        )
        client = FakeClient(_empty_day(), product_details={"1001": product})
        result = client.planner.resolve_product_measure(
            product_id=1001,
            requested_amount=500,
            requested_unit="ml",
        )
        assert result["strategy"] == "converted_from_capacity"
        assert result["measure_id"] == 32
        assert result["measure_quantity"] == pytest.approx(2.0)

    def test_direct_match_from_search_measure(self) -> None:
        product = self._product_with_measures(measures=[{"id": 1, "name": "g", "weightPerUnit": 1.0}])
        search_item = {
            "measure": {
                "measureId": 77,
                "measureName": "plaster",
                "measureQuantity": 1,
                "measureWeight": 20,
            }
        }
        client = FakeClient(_empty_day(), product_details={"1001": product})
        result = client.planner.resolve_product_measure(
            product_id=1001,
            requested_amount=3,
            requested_unit="slice",
            search_product=search_item,
        )
        assert result["strategy"] == "direct_unit_match_searchMeasure"
        assert result["measure_id"] == 77
        assert result["measure_quantity"] == pytest.approx(3.0)

    def test_no_measures_strict_raises(self) -> None:
        product = self._product_with_measures(measures=[])
        client = FakeClient(_empty_day(), product_details={"1001": product})
        with pytest.raises(FitatuApiError):
            client.planner.resolve_product_measure(
                product_id=1001, requested_amount=100, requested_unit="ml", strict_measure=False
            )

    def test_empty_unit_raises(self) -> None:
        product = self._product_with_measures()
        client = FakeClient(_empty_day(), product_details={"1001": product})
        with pytest.raises(FitatuApiError, match="requested_unit is required"):
            client.planner.resolve_product_measure(
                product_id=1001, requested_amount=100, requested_unit="  "
            )

    def test_result_contains_product_id(self) -> None:
        product = self._product_with_measures(
            measures=[{"id": 10, "name": "g", "weightPerUnit": 1.0}]
        )
        client = FakeClient(_empty_day(), product_details={"1001": product})
        result = client.planner.resolve_product_measure(
            product_id=1001, requested_amount=50, requested_unit="g"
        )
        assert result["product_id"] == 1001


# ---------------------------------------------------------------------------
# TestInputsAndErrors
# ---------------------------------------------------------------------------


class TestInputsAndErrors:
    def test_days_can_be_iso_strings(self) -> None:
        client = FakeClient(_day_with_product())
        day = TEST_DAY.isoformat()
        added = client.planner.add_product_to_day_meal(TEST_USER, day, meal_type="lunch", product_id=7, measure_id=1)
        assert client.planner.get_meal(TEST_USER, day, meal_type="lunch")["items"][0]["planDayDietItemId"] == added["row_id"]
        assert client.planner.remove_day_item(TEST_USER, day, meal_type="lunch", item_id=added["row_id"])["ok"] is True
        moved = client.planner.move_day_item(
            TEST_USER, day, from_meal_type="breakfast", item_id="item-1", to_day="2026-01-16"
        )
        assert moved["to_date"] == "2026-01-16"

    @pytest.mark.parametrize("day", ["15.01.2026", "2026-13-01", 20260115, None])
    def test_a_bad_day_fails_before_any_request(self, day: Any) -> None:
        client = FakeClient(_day_with_product())
        with pytest.raises(FitatuValidationError, match="'YYYY-MM-DD'"):
            client.planner.add_custom_item_to_day_meal(
                TEST_USER, day, meal_type="breakfast", name="X", calories=1, protein_g=0, fat_g=0, carbs_g=0
            )
        assert client.requests == []

    @pytest.mark.parametrize("user_id", ["../users/1", "user-42", ""])
    def test_a_non_numeric_user_id_never_reaches_a_path(self, user_id: str) -> None:
        client = FakeClient(_day_with_product())
        with pytest.raises(FitatuValidationError, match="user_id"):
            client.planner.get_day(user_id, TEST_DAY)
        with pytest.raises(FitatuValidationError, match="user_id"):
            client.planner.sync_days(user_id, {TEST_DAY.isoformat(): {"dietPlan": {}}})
        assert client.requests == []

    def test_unknown_meal_is_not_found_and_lists_the_day_meals(self) -> None:
        client = FakeClient(_day_with_product())
        with pytest.raises(FitatuNotFoundError, match="this day has: breakfast, lunch, snack"):
            client.planner.add_product_to_day_meal(TEST_USER, TEST_DAY, meal_type="dinner", product_id=1, measure_id=1)
        assert client.sync_payloads == []

    def test_unknown_row_is_not_found_and_lists_the_day_rows(self) -> None:
        client = FakeClient(_day_with_product())
        with pytest.raises(FitatuNotFoundError, match=r"item-1 \(breakfast, 'Oats', measureQuantity=1.0\)"):
            client.planner.update_day_item(TEST_USER, TEST_DAY, meal_type="breakfast", item_id="ghost", eaten=True)
        with pytest.raises(FitatuNotFoundError):
            client.planner.move_day_item(TEST_USER, TEST_DAY, from_meal_type="breakfast", item_id="ghost", to_meal_type="lunch")
        assert client.sync_payloads == []

    @pytest.mark.parametrize("hit_type", ["RECIPE_AI", "PRODUCT_GROUP", None])
    def test_a_search_hit_that_is_neither_a_product_nor_a_recipe_is_refused(self, hit_type: str | None) -> None:
        client = FakeClient(_empty_day(), search_results={"x": [{"type": hit_type, "foodId": 777, "name": "X"}]})
        with pytest.raises(FitatuValidationError, match=f"'X' is of type {hit_type!r}"):
            client.planner.add_search_result_to_day_meal(TEST_USER, TEST_DAY, meal_type="breakfast", phrase="x")
        assert client.sync_payloads == []

    def test_a_search_hit_without_a_measure_is_refused_not_logged_in_measure_1(self) -> None:
        # The sync takes any measure id: measure 1 would store the quantity in whatever that is.
        client = FakeClient(_empty_day(), search_results={"x": [{"type": "PRODUCT", "foodId": 777, "name": "X"}]})
        with pytest.raises(FitatuValidationError, match="'X' names no measure; for a product pass measure_amount"):
            client.planner.add_search_result_to_day_meal(TEST_USER, TEST_DAY, meal_type="breakfast", phrase="x")
        assert client.sync_payloads == []

    def test_a_day_reply_that_is_not_an_object_is_a_server_error(self) -> None:
        client = Mock()
        client.request.return_value = ["not", "a", "day"]
        with pytest.raises(FitatuApiError, match="unexpected response shape") as excinfo:
            PlannerModule(client).get_day(TEST_USER, TEST_DAY)
        assert not isinstance(excinfo.value, FitatuValidationError)

    def test_caller_mistakes_are_validation_errors(self) -> None:
        planner = FakeClient(_day_with_product()).planner
        mistakes = (
            lambda: planner.add_recipe_to_day_meal(TEST_USER, TEST_DAY, meal_type="breakfast", recipe_id=1, food_type="PRODUCT"),
            lambda: planner.remove_day_items_by_kind(TEST_USER, TEST_DAY, item_kind="everything", all_meals=True),
            lambda: planner.add_search_result_to_day_meal(TEST_USER, TEST_DAY, meal_type="breakfast", phrase="x", index=-1),
            # what an agent sends as JSON null or a wrong type
            lambda: planner.get_meal(TEST_USER, TEST_DAY, meal_type=None),  # type: ignore[arg-type]
            lambda: planner.remove_day_item(TEST_USER, TEST_DAY, meal_type=None, item_id="item-1"),  # type: ignore[arg-type]
            lambda: planner.find_meal_item(TEST_USER, TEST_DAY, meal_type="breakfast", query=None),  # type: ignore[arg-type]
            lambda: planner.add_custom_item_to_day_meal(
                TEST_USER, TEST_DAY, meal_type="breakfast", name=None, calories=1, protein_g=0, fat_g=0, carbs_g=0  # type: ignore[arg-type]
            ),
            lambda: planner.update_day_item(
                TEST_USER, TEST_DAY, meal_type="breakfast", item_id="item-1", patch="measureQuantity"  # type: ignore[arg-type]
            ),
        )
        for mistake in mistakes:
            with pytest.raises(FitatuValidationError):
                mistake()


# ---------------------------------------------------------------------------
# Row ids and retry-safe writes
# ---------------------------------------------------------------------------


class TestRetrySafeWrites:
    def test_resending_an_add_with_the_same_row_id_does_not_duplicate_it(self) -> None:
        client = FakeClient(_empty_day())
        row_id = _new_row_id()
        for _ in range(2):
            result = client.planner.add_product_to_day_meal(
                TEST_USER, TEST_DAY, meal_type="breakfast", product_id=7, measure_id=1, row_id=row_id
            )
        assert result["row_id"] == row_id
        assert [row["planDayDietItemId"] for row in client.day_payload["dietPlan"]["breakfast"]["items"]] == [row_id]

    def test_a_row_id_that_belongs_to_another_row_is_refused(self) -> None:
        client = FakeClient(_empty_day())
        planner = client.planner
        row_id = planner.add_product_to_day_meal(TEST_USER, TEST_DAY, meal_type="breakfast", product_id=7, measure_id=1)["row_id"]
        other_rows = (
            lambda: planner.add_custom_item_to_day_meal(
                TEST_USER, TEST_DAY, meal_type="breakfast", name="Bar", calories=1, protein_g=0, fat_g=0, carbs_g=0, row_id=row_id
            ),
            lambda: planner.add_recipe_to_day_meal(TEST_USER, TEST_DAY, meal_type="breakfast", recipe_id=7, row_id=row_id),
            lambda: planner.add_product_to_day_meal(TEST_USER, TEST_DAY, meal_type="breakfast", product_id=8, measure_id=1, row_id=row_id),
            lambda: planner.add_product_to_day_meal(TEST_USER, TEST_DAY, meal_type="lunch", product_id=7, measure_id=1, row_id=row_id),
            # the same food, but not what is logged: resending it would overwrite the row
            lambda: planner.add_product_to_day_meal(TEST_USER, TEST_DAY, meal_type="breakfast", product_id=7, measure_id=2, row_id=row_id),
            lambda: planner.add_product_to_day_meal(
                TEST_USER, TEST_DAY, meal_type="breakfast", product_id=7, measure_id=1, measure_quantity=2, row_id=row_id
            ),
            lambda: planner.add_product_to_day_meal(
                TEST_USER, TEST_DAY, meal_type="breakfast", product_id=7, measure_id=1, eaten=True, row_id=row_id
            ),
        )
        for add in other_rows:
            with pytest.raises(FitatuValidationError, match=rf"row_id {row_id} already belongs to another row.*breakfast.*saved.*do not add it again"):
                add()
        assert len(client.sync_payloads) == 1
        retry = planner.add_product_to_day_meal(TEST_USER, TEST_DAY, meal_type="breakfast", product_id="7", measure_id=1, row_id=row_id)
        assert retry["ok"] is True
        assert [row["productId"] for row in client.day_payload["dietPlan"]["breakfast"]["items"]] == ["7"]

    def test_every_add_takes_a_row_id(self) -> None:
        client = FakeClient(_empty_day(), product_details={"1001": _simple_product_details()})
        planner = client.planner
        adds = (
            lambda rid: planner.add_product_to_day_meal(TEST_USER, TEST_DAY, meal_type="breakfast", product_id=1, measure_id=1, row_id=rid),
            lambda rid: planner.add_product_to_day_meal_with_unit(
                TEST_USER, TEST_DAY, meal_type="breakfast", product_id=1001, amount=50, unit="g", row_id=rid
            ),
            lambda rid: planner.add_recipe_to_day_meal(TEST_USER, TEST_DAY, meal_type="breakfast", recipe_id=1, row_id=rid),
            lambda rid: planner.add_custom_item_to_day_meal(
                TEST_USER, TEST_DAY, meal_type="breakfast", name="X", calories=1, protein_g=0, fat_g=0, carbs_g=0, row_id=rid
            ),
            lambda rid: planner.add_search_result_to_day_meal(TEST_USER, TEST_DAY, meal_type="breakfast", phrase="owsianka", row_id=rid),
        )
        for add in adds:
            row_id = _new_row_id()
            assert add(row_id)["row_id"] == row_id
            assert row_id in {row["planDayDietItemId"] for row in client.day_payload["dietPlan"]["breakfast"]["items"]}

    @pytest.mark.parametrize("row_id", [str(uuid.uuid4()), "row-1", ""])
    def test_a_row_id_that_is_not_a_uuid_v1_is_rejected_before_any_request(self, row_id: str) -> None:
        client = FakeClient(_empty_day())
        with pytest.raises(FitatuValidationError, match="UUID v1"):
            client.planner.add_custom_item_to_day_meal(
                TEST_USER, TEST_DAY, meal_type="breakfast", name="X", calories=1, protein_g=0, fat_g=0, carbs_g=0, row_id=row_id
            )
        with pytest.raises(FitatuValidationError, match="UUID v1"):
            client.planner.add_search_result_to_day_meal(TEST_USER, TEST_DAY, meal_type="breakfast", phrase="owsianka", row_id=row_id)
        assert client.requests == []

    def test_the_day_sync_is_synchronous_and_retryable_by_default(self) -> None:
        client = Mock()
        client.request.return_value = []
        payload = {TEST_DAY.isoformat(): {"dietPlan": {}}}
        PlannerModule(client).sync_days(TEST_USER, payload)
        assert client.request.call_args.kwargs == {
            "json_data": payload,
            "params": {"synchronous": "true"},
            "idempotent": True,
        }
        PlannerModule(client).sync_single_day(TEST_USER, TEST_DAY, day_payload={"dietPlan": {}}, synchronous=False)
        assert client.request.call_args.kwargs["params"] is None


# ---------------------------------------------------------------------------
# TestActivities
# ---------------------------------------------------------------------------


class TestActivities:
    def test_add_then_remove(self) -> None:
        client = FakeClient(_empty_day())
        row_id = _new_row_id()
        for _ in range(2):  # a retry with the same row_id does not log the workout twice
            added = client.planner.add_activity(TEST_USER, TEST_DAY.isoformat(), activity_id="535", minutes=45, energy=400, row_id=row_id)
        assert added["ok"] is True
        assert added["added_activity"]["activityId"] == 535
        assert [a["uuid"] for a in client.day_payload["activityPlan"]] == [row_id]

        removed = client.planner.remove_activity(TEST_USER, TEST_DAY, item_id=row_id)

        assert removed["ok"] is True
        assert client.day_payload["activityPlan"] == []

    @pytest.mark.parametrize(
        "kwargs",
        [
            {"minutes": 0},
            {"minutes": 1.5},
            {"energy": -1},
            {"energy": "lots"},
            {"activity_id": "running"},
            {"row_id": str(uuid.uuid4())},
        ],
    )
    def test_bad_input_is_rejected_before_any_request(self, kwargs: dict[str, Any]) -> None:
        client = FakeClient(_empty_day())
        with pytest.raises(FitatuValidationError):
            client.planner.add_activity(TEST_USER, TEST_DAY, **{"activity_id": 535, "minutes": 30, "energy": 300, **kwargs})
        assert client.requests == []

    def test_a_row_id_that_belongs_to_another_activity_is_refused(self) -> None:
        client = FakeClient(_empty_day())
        row_id = client.planner.add_activity(TEST_USER, TEST_DAY, activity_id=535, minutes=30, energy=300)["row_id"]
        for other in ({"activity_id": 1322}, {"minutes": 5}, {"energy": 10}):
            with pytest.raises(FitatuValidationError, match=rf"row_id {row_id} already belongs to another activity.*do not add it again"):
                client.planner.add_activity(TEST_USER, TEST_DAY, **{"activity_id": 535, "minutes": 30, "energy": 300, **other}, row_id=row_id)
        assert [(a["activityId"], a["energy"]) for a in client.day_payload["activityPlan"]] == [(535, 300)]

    def test_removing_an_unknown_activity_lists_the_day_activities(self) -> None:
        client = FakeClient(_empty_day())
        added = client.planner.add_activity(TEST_USER, TEST_DAY, activity_id=535, minutes=30, energy=300)
        result = client.planner.remove_activity(TEST_USER, TEST_DAY, item_id="ghost")
        assert (result["ok"], result["reason"]) == (False, "item_not_found")
        assert result["message"].endswith(f"activities on this day: {added['row_id']} (None, 300 kcal)")
        assert len(client.sync_payloads) == 1

    def test_ok_is_false_when_the_day_did_not_change(self) -> None:
        client = IgnoringFakeClient(_empty_day())
        assert client.planner.add_activity(TEST_USER, TEST_DAY, activity_id=535, minutes=30, energy=300)["ok"] is False


def test_row_ids_are_uuid_v1_without_the_machine_mac() -> None:
    ids = {uuid.UUID(_new_row_id()) for _ in range(20)}
    assert len(ids) == 20
    assert all(row_id.version == 1 for row_id in ids)
    assert all(row_id.node & (1 << 40) for row_id in ids)  # multicast bit = random node
    assert uuid.getnode() not in {row_id.node for row_id in ids}


def test_every_planner_argument_after_user_id_and_day_is_keyword_only() -> None:
    """The convention docs/API_REFERENCE.md states; a positional one could not be made keyword-only later."""
    positional = {}
    for name, method in inspect.getmembers(PlannerModule, inspect.isfunction):
        params = list(inspect.signature(method).parameters.values())
        if not name.startswith("_") and [p.name for p in params[1:3]] == ["user_id", "day"]:
            positional[name] = [p.name for p in params[3:] if p.kind is not p.KEYWORD_ONLY]
    assert "set_note" in positional and not any(positional.values()), positional
