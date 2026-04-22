"""Unit tests for PlannerModule using an in-process FakeClient."""

from __future__ import annotations

from copy import deepcopy
from datetime import date
from typing import Any

import pytest

from fitatu_api.api_client import FitatuApiError, PlannerModule

TEST_DAY = date(2026, 1, 15)
TEST_USER = "user-42"


# ---------------------------------------------------------------------------
# FakeClient
# ---------------------------------------------------------------------------


class FakeClient:
    """Minimal in-process stand-in for FitatuApiClient."""

    def __init__(
        self,
        day_payload: dict[str, Any],
        *,
        product_details: dict[str, dict[str, Any]] | None = None,
        recipe_details: dict[str, dict[str, Any]] | None = None,
        search_results: dict[str, list[dict[str, Any]]] | None = None,
        delete_ok: bool = False,
    ) -> None:
        self.day_payload = deepcopy(day_payload)
        self.product_details: dict[str, dict[str, Any]] = product_details or {}
        self.recipe_details: dict[str, dict[str, Any]] = recipe_details or {}
        self.search_results: dict[str, list[dict[str, Any]]] = search_results or {}
        self.delete_ok = delete_ok
        self.request_log: list[tuple[str, str]] = []
        self.last_request_kwargs: dict[str, Any] | None = None
        self.last_sync_payload: dict[str, Any] | None = None
        self.planner = PlannerModule(self)  # type: ignore[arg-type]

    def request(self, method: str, path: str, **kwargs: Any) -> Any:
        self.request_log.append((method, path))
        expected = f"/diet-and-activity-plan/{TEST_USER}/day/{TEST_DAY.isoformat()}"
        if method == "GET" and path == expected:
            return deepcopy(self.day_payload)
        raise AssertionError(f"Unexpected GET: {method} {path}")

    def request_first_success(
        self,
        method: str,
        paths: list[str],
        json_data: dict[str, Any] | None = None,
        **kwargs: Any,
    ) -> Any:
        self.request_log.append((method, paths[0]))
        self.last_request_kwargs = deepcopy(kwargs)
        if method == "DELETE":
            if self.delete_ok:
                return {"status": "ok", "deleted": True}
            raise FitatuApiError("not found", status_code=404)
        if method != "POST":
            raise AssertionError(f"Unexpected method: {method}")
        if json_data is None:
            raise AssertionError("missing sync payload")
        self.last_sync_payload = deepcopy(json_data)
        day_iso = TEST_DAY.isoformat()
        payload = json_data.get(day_iso)
        if not isinstance(payload, dict):
            raise AssertionError(f"day key {day_iso!r} missing from payload")
        self.day_payload = deepcopy(payload)
        return {"status": "ok", "savedDay": day_iso, "path": paths[0]}

    def get_product_details(self, product_id: int | str) -> dict[str, Any]:
        details = self.product_details.get(str(product_id))
        if details is None:
            raise FitatuApiError(f"product {product_id} not found", status_code=404)
        return deepcopy(details)

    def get_recipe(self, recipe_id: int | str) -> dict[str, Any]:
        details = self.recipe_details.get(str(recipe_id))
        if details is None:
            raise FitatuApiError(f"recipe {recipe_id} not found", status_code=404)
        return deepcopy(details)

    def search_food(self, phrase: str, **kwargs: Any) -> list[dict[str, Any]]:
        return deepcopy(self.search_results.get(phrase.lower(), []))


# ---------------------------------------------------------------------------
# Payload helpers
# ---------------------------------------------------------------------------


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
    def test_as_dict_returns_dict(self) -> None:
        result = PlannerModule._as_dict({"a": 1})
        assert result == {"a": 1}

    def test_as_dict_returns_none_for_non_dict(self) -> None:
        assert PlannerModule._as_dict([1, 2]) is None
        assert PlannerModule._as_dict("str") is None
        assert PlannerModule._as_dict(None) is None

    def test_as_dict_list_filters_non_dicts(self) -> None:
        result = PlannerModule._as_dict_list([{"a": 1}, "bad", None, {"b": 2}])
        assert result == [{"a": 1}, {"b": 2}]

    def test_as_dict_list_empty_on_non_list(self) -> None:
        assert PlannerModule._as_dict_list(None) == []
        assert PlannerModule._as_dict_list("str") == []

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

    def test_first_non_empty_skips_none_and_blank(self) -> None:
        assert PlannerModule._first_non_empty(None, "", "  ", "hello") == "hello"

    def test_first_non_empty_returns_first_value(self) -> None:
        assert PlannerModule._first_non_empty(1, 2, 3) == 1

    def test_first_non_empty_all_empty_returns_none(self) -> None:
        assert PlannerModule._first_non_empty(None, "", "   ") is None

    def test_parse_optional_float_valid(self) -> None:
        assert PlannerModule._parse_optional_float("3.14") == pytest.approx(3.14)
        assert PlannerModule._parse_optional_float(42) == pytest.approx(42.0)

    def test_parse_optional_float_none(self) -> None:
        assert PlannerModule._parse_optional_float(None) is None

    def test_parse_optional_float_invalid(self) -> None:
        assert PlannerModule._parse_optional_float("bad") is None

    def test_normalize_meal_key_aliases(self) -> None:
        assert PlannerModule.normalize_meal_key("second-breakfast") == "second_breakfast"
        assert PlannerModule.normalize_meal_key("second breakfast") == "second_breakfast"

    def test_normalize_meal_key_passthrough(self) -> None:
        assert PlannerModule.normalize_meal_key("breakfast") == "breakfast"
        assert PlannerModule.normalize_meal_key("LUNCH") == "lunch"

    def test_build_day_sync_payload(self) -> None:
        day = {"dietPlan": {"b": {}}, "toiletItems": [1], "note": "hi", "tagsIds": [2]}
        result = PlannerModule._build_day_sync_payload(day)
        assert result["dietPlan"] == {"b": {}}
        assert result["toiletItems"] == [1]
        assert result["note"] == "hi"

    def test_build_day_sync_payload_defaults(self) -> None:
        result = PlannerModule._build_day_sync_payload({})
        assert result["dietPlan"] == {}
        assert result["toiletItems"] == []
        assert result["tagsIds"] == []

    def test_sync_days_supports_synchronous_param(self) -> None:
        client = FakeClient(_empty_day())
        client.planner.sync_days(TEST_USER, {TEST_DAY.isoformat(): {"dietPlan": {}}}, synchronous=True)
        assert client.last_request_kwargs is not None
        assert client.last_request_kwargs["params"] == {"synchronous": "true"}

    def test_compact_diet_item_for_sync_product(self) -> None:
        item = {
            "planDayDietItemId": "x1",
            "foodType": "PRODUCT",
            "measureId": 5,
            "measureQuantity": 2.0,
            "productId": 777,
            "source": "API",
        }
        result = PlannerModule._compact_diet_item_for_sync(item)
        assert result["productId"] == 777
        assert "name" not in result

    def test_compact_diet_item_for_sync_custom_item(self) -> None:
        item = {
            "planDayDietItemId": "c1",
            "foodType": "CUSTOM_ITEM",
            "measureId": 1,
            "measureQuantity": 100.0,
            "name": "Salad",
            "energy": 150,
            "source": "WEB",
        }
        result = PlannerModule._compact_diet_item_for_sync(item)
        assert result["name"] == "Salad"
        assert result["energy"] == 150
        assert "productId" not in result

    def test_compact_diet_item_for_sync_recipe_preserves_recipe_id(self) -> None:
        item = {
            "planDayDietItemId": "r1",
            "foodType": "RECIPE",
            "recipeId": 42,
            "measureId": 39,
            "measureQuantity": 1.0,
            "source": "API",
        }
        result = PlannerModule._compact_diet_item_for_sync(item)
        assert result["recipeId"] == 42

    def test_extract_measure_id_from_measure_dict(self) -> None:
        product = {"measure": {"defaultMeasureId": 99}}
        assert PlannerModule._extract_measure_id(product) == 99

    def test_extract_measure_id_from_top_level(self) -> None:
        product = {"defaultMeasureId": 5}
        assert PlannerModule._extract_measure_id(product) == 5

    def test_extract_measure_id_fallback(self) -> None:
        assert PlannerModule._extract_measure_id({}) == 1

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

    def test_meal_from_number_known(self) -> None:
        assert PlannerModule._meal_from_number(1) == "breakfast"
        assert PlannerModule._meal_from_number(3) == "lunch"
        assert PlannerModule._meal_from_number(5) == "snack"

    def test_meal_from_number_unknown_defaults_snack(self) -> None:
        assert PlannerModule._meal_from_number(99) == "snack"

    def test_is_cleanup_ok_dict_with_ok(self) -> None:
        assert PlannerModule._is_cleanup_ok({"ok": True}) is True
        assert PlannerModule._is_cleanup_ok({"ok": False}) is False

    def test_is_cleanup_ok_status_string(self) -> None:
        assert PlannerModule._is_cleanup_ok({"status": "ok"}) is True
        assert PlannerModule._is_cleanup_ok({"status": "success"}) is True
        assert PlannerModule._is_cleanup_ok({"status": "error"}) is False

    def test_is_cleanup_ok_non_dict(self) -> None:
        assert PlannerModule._is_cleanup_ok(True) is True
        assert PlannerModule._is_cleanup_ok(0) is False


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
        meal = client.planner.get_meal(TEST_USER, TEST_DAY, "breakfast")
        assert "items" in meal

    def test_get_meal_raises_for_unknown_meal(self) -> None:
        client = FakeClient(_day_with_product())
        with pytest.raises(FitatuApiError, match="meal not found"):
            client.planner.get_meal(TEST_USER, TEST_DAY, "nonexistent")

    def test_get_meal_raises_when_diet_plan_missing(self) -> None:
        payload = {"toiletItems": [], "note": None, "tagsIds": []}
        client = FakeClient(payload)
        with pytest.raises(FitatuApiError, match="dietPlan not available"):
            client.planner.get_meal(TEST_USER, TEST_DAY, "breakfast")

    def test_list_meal_items_returns_items(self) -> None:
        client = FakeClient(_day_with_product())
        items = client.planner.list_meal_items(TEST_USER, TEST_DAY, "breakfast")
        assert len(items) == 1
        assert items[0]["productId"] == 1001

    def test_list_meal_items_empty_meal(self) -> None:
        client = FakeClient(_day_with_product())
        items = client.planner.list_meal_items(TEST_USER, TEST_DAY, "lunch")
        assert items == []

    def test_find_meal_item_found(self) -> None:
        client = FakeClient(_day_with_product())
        result = client.planner.find_meal_item(TEST_USER, TEST_DAY, "breakfast", "oat")
        assert result is not None
        assert result["productId"] == 1001

    def test_find_meal_item_not_found(self) -> None:
        client = FakeClient(_day_with_product())
        result = client.planner.find_meal_item(TEST_USER, TEST_DAY, "breakfast", "xyz")
        assert result is None

    def test_find_meal_item_empty_query(self) -> None:
        client = FakeClient(_day_with_product())
        result = client.planner.find_meal_item(TEST_USER, TEST_DAY, "breakfast", "  ")
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
        rows = client.planner.list_day_items_for_removal(TEST_USER, TEST_DAY, meal_key="breakfast")
        assert len(rows) == 1
        assert rows[0]["meal"] == "breakfast"

    def test_list_day_items_for_removal_raises_for_unknown_meal(self) -> None:
        client = FakeClient(_empty_day())
        with pytest.raises(FitatuApiError, match="meal not found"):
            client.planner.list_day_items_for_removal(TEST_USER, TEST_DAY, meal_key="NONEXISTENT")


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
        assert result["addedItem"]["foodType"] == "PRODUCT"

    def test_item_id_generated(self) -> None:
        client = FakeClient(_empty_day())
        result = client.planner.add_product_to_day_meal(
            TEST_USER, TEST_DAY, meal_type="breakfast", product_id=1, measure_id=1
        )
        assert bool(result["addedItem"]["planDayDietItemId"])

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
        assert result["addedItem"]["source"] == "WEB"

    def test_eaten_flag(self) -> None:
        client = FakeClient(_empty_day())
        result = client.planner.add_product_to_day_meal(
            TEST_USER, TEST_DAY, meal_type="breakfast", product_id=1, measure_id=1, eaten=True
        )
        assert result["addedItem"]["eaten"] is True


# ---------------------------------------------------------------------------
# TestAddRecipeToDayMeal
# ---------------------------------------------------------------------------


class TestAddRecipeToDayMeal:
    def test_returns_ok_true(self) -> None:
        client = FakeClient(_empty_day())
        result = client.planner.add_recipe_to_day_meal(
            TEST_USER, TEST_DAY, meal_type="breakfast", recipe_id=42, hydrate_from_recipe_details=False
        )
        assert result["ok"] is True

    def test_recipe_appended(self) -> None:
        client = FakeClient(_empty_day())
        client.planner.add_recipe_to_day_meal(
            TEST_USER, TEST_DAY, meal_type="breakfast", recipe_id=42, hydrate_from_recipe_details=False
        )
        items = client.day_payload["dietPlan"]["breakfast"]["items"]
        assert len(items) == 1
        assert items[0]["recipeId"] == 42

    def test_food_type_recipe(self) -> None:
        client = FakeClient(_empty_day())
        result = client.planner.add_recipe_to_day_meal(
            TEST_USER, TEST_DAY, meal_type="breakfast", recipe_id=1, food_type="RECIPE", hydrate_from_recipe_details=False
        )
        assert result["addedItem"]["foodType"] == "RECIPE"

    def test_food_type_recipe_ai(self) -> None:
        client = FakeClient(_empty_day())
        result = client.planner.add_recipe_to_day_meal(
            TEST_USER, TEST_DAY, meal_type="breakfast", recipe_id=1, food_type="RECIPE_AI", hydrate_from_recipe_details=False
        )
        assert result["addedItem"]["foodType"] == "RECIPE_AI"

    def test_invalid_food_type_raises(self) -> None:
        client = FakeClient(_empty_day())
        with pytest.raises(FitatuApiError, match="food_type must be"):
            client.planner.add_recipe_to_day_meal(
                TEST_USER, TEST_DAY, meal_type="breakfast", recipe_id=1, food_type="PRODUCT", hydrate_from_recipe_details=False
            )

    def test_item_id_generated(self) -> None:
        client = FakeClient(_empty_day())
        result = client.planner.add_recipe_to_day_meal(
            TEST_USER, TEST_DAY, meal_type="breakfast", recipe_id=1, hydrate_from_recipe_details=False
        )
        assert bool(result["addedItem"]["planDayDietItemId"])

    def test_hydration_disabled(self) -> None:
        client = FakeClient(_empty_day())
        result = client.planner.add_recipe_to_day_meal(
            TEST_USER, TEST_DAY, meal_type="breakfast", recipe_id=1, hydrate_from_recipe_details=False
        )
        assert result["recipeDetailsHydration"]["status"] == "disabled"

    def test_hydration_from_recipe_details(self) -> None:
        client = FakeClient(
            _empty_day(),
            recipe_details={"9999": _simple_recipe_details(9999)},
        )
        result = client.planner.add_recipe_to_day_meal(
            TEST_USER, TEST_DAY, meal_type="breakfast", recipe_id=9999, ingredients_serving=None
        )
        assert result["recipeDetailsHydration"]["status"] == "hydrated"
        assert "name" in result["recipeDetailsHydration"]["fields"]
        assert "energy" in result["recipeDetailsHydration"]["fields"]

    def test_hydration_fills_name_on_item(self) -> None:
        client = FakeClient(
            _empty_day(),
            recipe_details={"9999": _simple_recipe_details(9999)},
        )
        result = client.planner.add_recipe_to_day_meal(
            TEST_USER, TEST_DAY, meal_type="breakfast", recipe_id=9999, ingredients_serving=None
        )
        assert result["addedItem"].get("name") == "Veggie Bowl"

    def test_hydration_does_not_overwrite_explicit_serving(self) -> None:
        client = FakeClient(
            _empty_day(),
            recipe_details={"9999": _simple_recipe_details(9999)},
        )
        result = client.planner.add_recipe_to_day_meal(
            TEST_USER, TEST_DAY, meal_type="breakfast", recipe_id=9999, ingredients_serving=5
        )
        assert result["addedItem"]["ingredientsServing"] == 5

    def test_hydration_missing_recipe_returns_unavailable(self) -> None:
        client = FakeClient(_empty_day())
        result = client.planner.add_recipe_to_day_meal(
            TEST_USER, TEST_DAY, meal_type="breakfast", recipe_id=999
        )
        assert result["recipeDetailsHydration"]["status"] == "unavailable"

    def test_raises_for_missing_meal(self) -> None:
        client = FakeClient(_empty_day())
        with pytest.raises(FitatuApiError, match="meal not found"):
            client.planner.add_recipe_to_day_meal(
                TEST_USER, TEST_DAY, meal_type="supper", recipe_id=1, hydrate_from_recipe_details=False
            )

    def test_ingredients_serving_stored(self) -> None:
        client = FakeClient(_empty_day())
        result = client.planner.add_recipe_to_day_meal(
            TEST_USER, TEST_DAY, meal_type="breakfast", recipe_id=1, ingredients_serving=3, hydrate_from_recipe_details=False
        )
        assert result["addedItem"]["ingredientsServing"] == 3

    def test_measure_id_and_quantity_stored(self) -> None:
        client = FakeClient(_empty_day())
        result = client.planner.add_recipe_to_day_meal(
            TEST_USER, TEST_DAY, meal_type="breakfast", recipe_id=1, measure_id=39, measure_quantity=2, hydrate_from_recipe_details=False
        )
        assert result["addedItem"]["measureId"] == 39
        assert result["addedItem"]["measureQuantity"] == 2


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
        assert result["addedItem"]["foodType"] == "CUSTOM_ITEM"

    def test_name_stored(self) -> None:
        client = FakeClient(_empty_day())
        result = client.planner.add_custom_item_to_day_meal(
            TEST_USER, TEST_DAY, meal_type="breakfast", name="  My Salad  ", calories=0, protein_g=0, fat_g=0, carbs_g=0
        )
        assert result["addedItem"]["name"] == "My Salad"

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
        item = result["addedItem"]
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
        assert result["experimental"] is True
        assert result["liveTested"] is True
        assert result["fromMeal"] == "breakfast"
        assert result["toMeal"] == "lunch"
        assert client.last_request_kwargs is not None
        assert client.last_request_kwargs["params"] == {"synchronous": "true"}

        payload = result["syncPayload"][TEST_DAY.isoformat()]["dietPlan"]
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

        assert set(result["syncPayload"]) == {TEST_DAY.isoformat(), next_day.isoformat()}
        assert "breakfast" in result["syncPayload"][TEST_DAY.isoformat()]["dietPlan"]
        assert "snack" in result["syncPayload"][next_day.isoformat()]["dietPlan"]

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
        assert result["experimental"] is True
        payload_items = result["syncPayload"][TEST_DAY.isoformat()]["dietPlan"]["breakfast"]["items"]
        assert payload_items[0]["deletedAt"]
        assert payload_items[1]["foodType"] == "CUSTOM_ITEM"
        assert payload_items[1]["name"] == "Updated meal"
        assert payload_items[1]["energy"] == pytest.approx(400.0)
        assert client.last_request_kwargs is not None
        assert client.last_request_kwargs["params"] == {"synchronous": "true"}


# ---------------------------------------------------------------------------
# TestClassifyDayItemForRemoval
# ---------------------------------------------------------------------------


class TestClassifyDayItemForRemoval:
    def test_product_classified_normal(self) -> None:
        client = FakeClient(_day_with_product())
        result = client.planner.classify_day_item_for_removal(TEST_USER, TEST_DAY, "breakfast", "item-1")
        assert result["found"] is True
        assert result["resolvedKind"] == "normal_item"

    def test_custom_add_classified_correctly(self) -> None:
        client = FakeClient(_day_with_custom_item(source="WEB", quantity=100.0))
        result = client.planner.classify_day_item_for_removal(TEST_USER, TEST_DAY, "breakfast", "custom-1")
        assert result["resolvedKind"] == "custom_add_item"

    def test_custom_api_small_qty_classified_recipe_like(self) -> None:
        client = FakeClient(_day_with_custom_item(source="API", quantity=1.0))
        result = client.planner.classify_day_item_for_removal(TEST_USER, TEST_DAY, "breakfast", "custom-1")
        assert result["resolvedKind"] == "custom_recipe_item"

    def test_not_found_returns_default(self) -> None:
        client = FakeClient(_day_with_product())
        result = client.planner.classify_day_item_for_removal(TEST_USER, TEST_DAY, "breakfast", "ghost")
        assert result["found"] is False
        assert result["resolvedKind"] == "normal_item"

    def test_item_key_in_result(self) -> None:
        client = FakeClient(_day_with_product())
        result = client.planner.classify_day_item_for_removal(TEST_USER, TEST_DAY, "breakfast", "item-1")
        assert result["item"] is not None
        assert result["item"]["productId"] == 1001


# ---------------------------------------------------------------------------
# TestRemoveDayItemViaSnapshot
# ---------------------------------------------------------------------------


class TestRemoveDayItemViaSnapshot:
    def test_removes_product_item(self) -> None:
        client = FakeClient(_day_with_product())
        result = client.planner.remove_day_item_via_snapshot(TEST_USER, TEST_DAY, "breakfast", "item-1")
        assert result["ok"] is True
        # After removal the compacted sync payload drops empty meal buckets
        breakfast = client.day_payload.get("dietPlan", {}).get("breakfast", {"items": []})
        items = breakfast.get("items", [])
        assert items == []

    def test_item_not_found_returns_ok_false(self) -> None:
        client = FakeClient(_day_with_product())
        result = client.planner.remove_day_item_via_snapshot(TEST_USER, TEST_DAY, "breakfast", "ghost")
        assert result["ok"] is False
        assert result["removed"] is None

    def test_raises_for_missing_meal(self) -> None:
        client = FakeClient(_day_with_product())
        with pytest.raises(FitatuApiError, match="meal not found"):
            client.planner.remove_day_item_via_snapshot(TEST_USER, TEST_DAY, "supper", "item-1")

    def test_remove_by_product_id(self) -> None:
        client = FakeClient(_day_with_product())
        result = client.planner.remove_day_item_via_snapshot(TEST_USER, TEST_DAY, "breakfast", 1001)
        assert result["ok"] is True

    def test_count_decreased_in_result(self) -> None:
        client = FakeClient(_day_with_product())
        result = client.planner.remove_day_item_via_snapshot(TEST_USER, TEST_DAY, "breakfast", "item-1")
        assert result["beforeCount"] == 1
        assert result["afterCount"] == 0
        assert result["countDecreased"] is True


# ---------------------------------------------------------------------------
# TestSoftRemoveDayItemViaSnapshot
# ---------------------------------------------------------------------------


class TestSoftRemoveDayItemViaSnapshot:
    def test_soft_delete_item_present_after_sync(self) -> None:
        client = FakeClient(_day_with_product())
        result = client.planner.soft_remove_day_item_via_snapshot(TEST_USER, TEST_DAY, "breakfast", "item-1")
        # FakeClient stores back the payload; item stays present
        assert result["cleanupMode"] in {"soft_deleted", "removed", "deleted_at"}

    def test_item_not_found_returns_ok_false(self) -> None:
        client = FakeClient(_day_with_product())
        result = client.planner.soft_remove_day_item_via_snapshot(TEST_USER, TEST_DAY, "breakfast", "ghost")
        assert result["ok"] is False

    def test_quantity_shrunk_in_sync_payload(self) -> None:
        client = FakeClient(_day_with_product())
        client.planner.soft_remove_day_item_via_snapshot(TEST_USER, TEST_DAY, "breakfast", "item-1", soft_delete_quantity=0.01)
        items = client.day_payload["dietPlan"]["breakfast"]["items"]
        assert float(items[0]["measureQuantity"]) == pytest.approx(0.01)

    def test_deleted_at_set_when_use_deleted_at(self) -> None:
        client = FakeClient(_day_with_product())
        client.planner.soft_remove_day_item_via_snapshot(TEST_USER, TEST_DAY, "breakfast", "item-1", use_deleted_at=True)
        items = client.day_payload["dietPlan"]["breakfast"]["items"]
        assert "deletedAt" in items[0]

    def test_mark_invisible_sets_flag(self) -> None:
        client = FakeClient(_day_with_product())
        client.planner.soft_remove_day_item_via_snapshot(
            TEST_USER, TEST_DAY, "breakfast", "item-1", mark_invisible=True, use_deleted_at=False
        )
        items = client.day_payload["dietPlan"]["breakfast"]["items"]
        assert items[0].get("visible") is False

    def test_raises_for_missing_meal(self) -> None:
        client = FakeClient(_day_with_product())
        with pytest.raises(FitatuApiError, match="meal not found"):
            client.planner.soft_remove_day_item_via_snapshot(TEST_USER, TEST_DAY, "supper", "item-1")


# ---------------------------------------------------------------------------
# TestRemoveDayItemWithStrategy
# ---------------------------------------------------------------------------


class TestRemoveDayItemWithStrategy:
    def test_normal_item_attempts_snapshot_first(self) -> None:
        client = FakeClient(_day_with_product())
        result = client.planner.remove_day_item_with_strategy(
            TEST_USER, TEST_DAY, "breakfast", "item-1", item_kind="normal_item"
        )
        steps = [a["step"] for a in result["attempts"] if not a.get("skipped")]
        assert "snapshot_remove" in steps

    def test_custom_add_item_attempts_soft_delete_first(self) -> None:
        client = FakeClient(_day_with_custom_item(source="WEB", quantity=100.0))
        result = client.planner.remove_day_item_with_strategy(
            TEST_USER, TEST_DAY, "breakfast", "custom-1", item_kind="custom_add_item"
        )
        steps = [a["step"] for a in result["attempts"] if not a.get("skipped")]
        assert steps[0] == "soft_deleted_at"

    def test_result_includes_classification(self) -> None:
        client = FakeClient(_day_with_product())
        result = client.planner.remove_day_item_with_strategy(
            TEST_USER, TEST_DAY, "breakfast", "item-1", item_kind="auto"
        )
        assert "classification" in result

    def test_resolved_kind_in_result(self) -> None:
        client = FakeClient(_day_with_product())
        result = client.planner.remove_day_item_with_strategy(
            TEST_USER, TEST_DAY, "breakfast", "item-1", item_kind="auto"
        )
        assert result["resolvedKind"] == "normal_item"

    def test_invalid_kind_raises(self) -> None:
        client = FakeClient(_day_with_product())
        with pytest.raises(FitatuApiError, match="item_kind must be"):
            client.planner.remove_day_item_with_strategy(
                TEST_USER, TEST_DAY, "breakfast", "item-1", item_kind="bad_kind"
            )


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
        result = client.planner.remove_day_items_by_kind(TEST_USER, TEST_DAY, item_kind="normal_item")
        assert result["targetedCount"] == 2
        assert result["removedCount"] == 2

    def test_invalid_kind_raises(self) -> None:
        client = FakeClient(self._multi_item_payload())
        with pytest.raises(FitatuApiError, match="item_kind must be"):
            client.planner.remove_day_items_by_kind(TEST_USER, TEST_DAY, item_kind="unknown_kind")

    def test_max_items_limits_removal(self) -> None:
        client = FakeClient(self._multi_item_payload())
        result = client.planner.remove_day_items_by_kind(TEST_USER, TEST_DAY, item_kind="normal_item", max_items=1)
        assert result["targetedCount"] == 1

    def test_auto_kind_targets_all(self) -> None:
        client = FakeClient(self._multi_item_payload())
        result = client.planner.remove_day_items_by_kind(TEST_USER, TEST_DAY, item_kind="auto")
        assert result["targetedCount"] == 2

    def test_meal_filter_applied(self) -> None:
        payload = deepcopy(self._multi_item_payload())
        payload["dietPlan"]["lunch"] = {
            "items": [{"planDayDietItemId": "p3", "foodType": "PRODUCT", "productId": 3, "source": "API", "measureQuantity": 1.0}]
        }
        client = FakeClient(payload)
        result = client.planner.remove_day_items_by_kind(TEST_USER, TEST_DAY, item_kind="normal_item", meal_key="breakfast")
        assert result["targetedCount"] == 2

    def test_empty_day_returns_zero(self) -> None:
        client = FakeClient(_empty_day())
        result = client.planner.remove_day_items_by_kind(TEST_USER, TEST_DAY, item_kind="auto")
        assert result["targetedCount"] == 0
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
        assert result["measureId"] == 10
        assert result["measureQuantity"] == pytest.approx(100.0)

    def test_converted_from_weight(self) -> None:
        product = self._product_with_measures(
            measures=[{"id": 20, "name": "opakowanie", "weightPerUnit": 200.0}]
        )
        client = FakeClient(_empty_day(), product_details={"1001": product})
        result = client.planner.resolve_product_measure(
            product_id=1001, requested_amount=100, requested_unit="g"
        )
        assert result["strategy"] == "converted_from_weight"
        assert result["measureQuantity"] == pytest.approx(0.5)

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
        assert result["measureQuantity"] == pytest.approx(200.0)

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
        assert result["measureId"] == 99

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
        assert result["measureId"] == 19
        assert result["measureQuantity"] == pytest.approx(2.0)

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
        assert result["measureId"] == 19
        assert result["measureName"] == "łyżeczka"
        assert result["measureQuantity"] == pytest.approx(40.0)

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
        assert result["measureId"] == 32
        assert result["measureQuantity"] == pytest.approx(2.0)

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
        assert result["measureId"] == 77
        assert result["measureQuantity"] == pytest.approx(3.0)

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
        assert result["productId"] == 1001


# ---------------------------------------------------------------------------
# TestHydrateRecipeItemFromDetails
# ---------------------------------------------------------------------------


class TestHydrateRecipeItemFromDetails:
    def test_hydrates_name_and_energy(self) -> None:
        client = FakeClient(_empty_day(), recipe_details={"42": _simple_recipe_details(42)})
        result = client.planner._hydrate_recipe_item_from_details(42, {})
        assert result["status"] == "hydrated"
        assert result["fields"]["name"] == "Veggie Bowl"
        assert result["fields"]["energy"] == pytest.approx(320.0)

    def test_hydrates_photo_string(self) -> None:
        recipe = {**_simple_recipe_details(42), "photoUrl": "https://cdn.example.com/img.jpg"}
        client = FakeClient(_empty_day(), recipe_details={"42": recipe})
        result = client.planner._hydrate_recipe_item_from_details(42, {})
        assert "photo" in result["fields"]

    def test_missing_recipe_returns_unavailable(self) -> None:
        client = FakeClient(_empty_day())
        result = client.planner._hydrate_recipe_item_from_details(99, {})
        assert result["status"] == "unavailable"
        assert result["statusCode"] == 404

    def test_non_dict_response_returns_unavailable(self) -> None:
        class _ListClient(FakeClient):
            def get_recipe(self, recipe_id: int | str) -> Any:  # type: ignore[override]
                return [1, 2, 3]

        lc = _ListClient(_empty_day())
        result = lc.planner._hydrate_recipe_item_from_details(42, {})
        assert result["status"] == "unavailable"

    def test_does_not_overwrite_base_item_measure_id(self) -> None:
        base = {"measureId": 77}
        client = FakeClient(_empty_day(), recipe_details={"42": _simple_recipe_details(42)})
        result = client.planner._hydrate_recipe_item_from_details(42, base)
        assert result["fields"].get("measureId") is None or result["fields"].get("measureId") == 39

    def test_recipe_id_in_result(self) -> None:
        client = FakeClient(_empty_day(), recipe_details={"42": _simple_recipe_details(42)})
        result = client.planner._hydrate_recipe_item_from_details(42, {})
        assert result["recipeId"] == 42

    def test_macros_hydrated(self) -> None:
        client = FakeClient(_empty_day(), recipe_details={"42": _simple_recipe_details(42)})
        result = client.planner._hydrate_recipe_item_from_details(42, {})
        fields = result["fields"]
        assert "protein" in fields
        assert "fat" in fields
        assert "carbohydrate" in fields

    def test_nested_recipe_key_explored(self) -> None:
        recipe = {
            "recipe": {
                "id": 42,
                "name": "Nested Name",
                "nutritionalValues": {"energy": 200, "protein": 10, "fat": 5, "carbohydrate": 20},
            }
        }
        client = FakeClient(_empty_day(), recipe_details={"42": recipe})
        result = client.planner._hydrate_recipe_item_from_details(42, {})
        assert result["fields"].get("name") == "Nested Name"
