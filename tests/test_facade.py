"""Unit tests for FitatuLibrary (high-level facade)."""

from __future__ import annotations

from datetime import date
from unittest.mock import MagicMock, patch

from fitatu_api.exceptions import FitatuApiError
from fitatu_api.facade import FitatuLibrary

TEST_DATE = date(2026, 1, 15)
TEST_USER = "user-99"

SESSION = {
    "bearer_token": "tok",
    "refresh_token": "ref",
    "fitatu_user_id": TEST_USER,
}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _lib(**extra: object) -> FitatuLibrary:
    data = {**SESSION, **extra}
    return FitatuLibrary(session_data=data)


def _mock_client(planner_result: object = None, **extra: object) -> MagicMock:
    """Return a MagicMock that masquerades as FitatuApiClient."""
    client = MagicMock()
    client.planner = MagicMock()
    client.auth.fitatu_user_id = TEST_USER
    if planner_result is not None:
        client.planner.add_product_to_day_meal.return_value = planner_result
        client.planner.add_recipe_to_day_meal.return_value = planner_result
        client.planner.add_custom_item_to_day_meal.return_value = planner_result
        client.planner.remove_day_item.return_value = planner_result
        client.planner.remove_day_item_with_strategy.return_value = planner_result
        client.planner.remove_day_items_by_kind.return_value = planner_result
        client.planner.update_day_item.return_value = planner_result
        client.planner.add_search_result_to_day_meal.return_value = planner_result
    for key, value in extra.items():
        setattr(client, key, value)
    return client


# ---------------------------------------------------------------------------
# TestStaticHelpers
# ---------------------------------------------------------------------------


class TestStaticHelpers:
    def test_error_result_shape(self) -> None:
        exc = FitatuApiError("boom", status_code=400, body="bad")
        result = FitatuLibrary._error_result("my_op", exc)
        assert result["status"] == "error"
        assert result["operation"] == "my_op"
        assert result["message"] == "boom"
        assert result["status_code"] == 400
        assert result["body"] == "bad"

    def test_error_result_none_status_code(self) -> None:
        exc = FitatuApiError("network err")
        result = FitatuLibrary._error_result("op", exc)
        assert result["status_code"] is None


# ---------------------------------------------------------------------------
# TestResolveUserId
# ---------------------------------------------------------------------------


class TestResolveUserId:
    def test_explicit_user_id_wins(self) -> None:
        lib = _lib()
        auth = MagicMock()
        auth.fitatu_user_id = "from_session"
        result = lib._resolve_user_id(auth, "explicit")
        assert result == "explicit"

    def test_falls_back_to_session(self) -> None:
        lib = _lib()
        auth = MagicMock()
        auth.fitatu_user_id = "from_session"
        result = lib._resolve_user_id(auth, None)
        assert result == "from_session"

    def test_returns_none_when_missing(self) -> None:
        lib = _lib()
        auth = MagicMock()
        auth.fitatu_user_id = None
        result = lib._resolve_user_id(auth, None)
        assert result is None


# ---------------------------------------------------------------------------
# TestAddProductToDayMealViaApi
# ---------------------------------------------------------------------------


class TestAddProductToDayMealViaApi:
    def test_success_with_measure_id(self) -> None:
        planner_payload = {"ok": True, "productId": 1001}
        lib = _lib()
        mock_client = _mock_client(planner_payload)

        with patch("fitatu_api.facade.FitatuApiClient", return_value=mock_client):
            result = lib.add_product_to_day_meal_via_api(
                target_date=TEST_DATE,
                meal_key="breakfast",
                product_id=1001,
                measure_id=5,
                measure_quantity=2.0,
                user_id=TEST_USER,
            )

        assert result["status"] == "ok"
        assert result["result"] == planner_payload
        mock_client.planner.add_product_to_day_meal.assert_called_once()

    def test_success_with_measure_unit(self) -> None:
        planner_payload = {"ok": True}
        lib = _lib()
        mock_client = _mock_client()
        mock_client.planner.add_product_to_day_meal_with_unit.return_value = planner_payload

        with patch("fitatu_api.facade.FitatuApiClient", return_value=mock_client):
            result = lib.add_product_to_day_meal_via_api(
                target_date=TEST_DATE,
                meal_key="breakfast",
                product_id=1001,
                measure_unit="g",
                measure_amount=100,
                user_id=TEST_USER,
            )

        assert result["status"] == "ok"
        mock_client.planner.add_product_to_day_meal_with_unit.assert_called_once()

    def test_missing_measure_id_returns_error(self) -> None:
        lib = _lib()
        mock_client = _mock_client()

        with patch("fitatu_api.facade.FitatuApiClient", return_value=mock_client):
            result = lib.add_product_to_day_meal_via_api(
                target_date=TEST_DATE,
                meal_key="breakfast",
                product_id=1001,
                user_id=TEST_USER,
                # no measure_id, no measure_unit
            )

        assert result["status"] == "error"
        assert "measure_id" in result["message"]

    def test_missing_user_id_returns_error(self) -> None:
        lib = FitatuLibrary(session_data={"bearer_token": "tok"})
        result = lib.add_product_to_day_meal_via_api(
            target_date=TEST_DATE,
            meal_key="breakfast",
            product_id=1,
            measure_id=1,
        )
        assert result["status"] == "error"
        assert "user id" in result["message"].lower()

    def test_api_error_returns_error_result(self) -> None:
        lib = _lib()
        mock_client = _mock_client()
        mock_client.planner.add_product_to_day_meal.side_effect = FitatuApiError("fail", 500)

        with patch("fitatu_api.facade.FitatuApiClient", return_value=mock_client):
            result = lib.add_product_to_day_meal_via_api(
                target_date=TEST_DATE,
                meal_key="breakfast",
                product_id=1,
                measure_id=1,
                user_id=TEST_USER,
            )

        assert result["status"] == "error"
        assert result["status_code"] == 500


# ---------------------------------------------------------------------------
# TestAddCustomItemToDayMealViaApi
# ---------------------------------------------------------------------------


class TestAddCustomItemToDayMealViaApi:
    def test_success(self) -> None:
        planner_payload = {"ok": True, "name": "Salad"}
        lib = _lib()
        mock_client = _mock_client(planner_payload)

        with patch("fitatu_api.facade.FitatuApiClient", return_value=mock_client):
            result = lib.add_custom_item_to_day_meal_via_api(
                target_date=TEST_DATE,
                meal_key="lunch",
                name="Salad",
                calories=200,
                protein_g=10,
                fat_g=5,
                carbs_g=20,
                user_id=TEST_USER,
            )

        assert result["status"] == "ok"
        mock_client.planner.add_custom_item_to_day_meal.assert_called_once()

    def test_missing_user_id_returns_error(self) -> None:
        lib = FitatuLibrary(session_data={"bearer_token": "tok"})
        result = lib.add_custom_item_to_day_meal_via_api(
            target_date=TEST_DATE,
            meal_key="lunch",
            name="X",
            calories=0,
            protein_g=0,
            fat_g=0,
            carbs_g=0,
        )
        assert result["status"] == "error"

    def test_api_error_caught(self) -> None:
        lib = _lib()
        mock_client = _mock_client()
        mock_client.planner.add_custom_item_to_day_meal.side_effect = FitatuApiError("bad name", 422)

        with patch("fitatu_api.facade.FitatuApiClient", return_value=mock_client):
            result = lib.add_custom_item_to_day_meal_via_api(
                target_date=TEST_DATE,
                meal_key="breakfast",
                name="",
                calories=0,
                protein_g=0,
                fat_g=0,
                carbs_g=0,
                user_id=TEST_USER,
            )

        assert result["status"] == "error"


# ---------------------------------------------------------------------------
# TestAddRecipeToDayMealViaApi
# ---------------------------------------------------------------------------


class TestAddRecipeToDayMealViaApi:
    def test_success(self) -> None:
        planner_payload = {"ok": True, "recipeId": 42}
        lib = _lib()
        mock_client = _mock_client(planner_payload)

        with patch("fitatu_api.facade.FitatuApiClient", return_value=mock_client):
            result = lib.add_recipe_to_day_meal_via_api(
                target_date=TEST_DATE,
                meal_key="snack",
                recipe_id=42,
                user_id=TEST_USER,
            )

        assert result["status"] == "ok"
        assert result["result"]["recipeId"] == 42
        mock_client.planner.add_recipe_to_day_meal.assert_called_once()

    def test_missing_user_id_returns_error(self) -> None:
        lib = FitatuLibrary(session_data={"bearer_token": "tok"})
        result = lib.add_recipe_to_day_meal_via_api(
            target_date=TEST_DATE, meal_key="snack", recipe_id=1
        )
        assert result["status"] == "error"

    def test_api_error_caught(self) -> None:
        lib = _lib()
        mock_client = _mock_client()
        mock_client.planner.add_recipe_to_day_meal.side_effect = FitatuApiError("not found", 404)

        with patch("fitatu_api.facade.FitatuApiClient", return_value=mock_client):
            result = lib.add_recipe_to_day_meal_via_api(
                target_date=TEST_DATE, meal_key="snack", recipe_id=999, user_id=TEST_USER
            )

        assert result["status"] == "error"
        assert result["status_code"] == 404


# ---------------------------------------------------------------------------
# TestRemoveDayItemViaApi
# ---------------------------------------------------------------------------


class TestRemoveDayItemViaApi:
    def test_success(self) -> None:
        planner_payload = {"ok": True, "cleanupMode": "removed"}
        lib = _lib()
        mock_client = _mock_client(planner_payload)

        with patch("fitatu_api.facade.FitatuApiClient", return_value=mock_client):
            result = lib.remove_day_item_via_api(
                target_date=TEST_DATE,
                meal_key="breakfast",
                item_id="item-1",
                user_id=TEST_USER,
            )

        assert result["status"] == "ok"
        mock_client.planner.remove_day_item.assert_called_once_with(
            TEST_USER,
            TEST_DATE,
            "breakfast",
            "item-1",
            delete_all_related_meals=False,
            use_aggressive_soft_delete=True,
        )

    def test_missing_user_id_returns_error(self) -> None:
        lib = FitatuLibrary(session_data={"bearer_token": "tok"})
        result = lib.remove_day_item_via_api(
            target_date=TEST_DATE, meal_key="breakfast", item_id="x"
        )
        assert result["status"] == "error"

    def test_api_error_caught(self) -> None:
        lib = _lib()
        mock_client = _mock_client()
        mock_client.planner.remove_day_item.side_effect = FitatuApiError("gone", 410)

        with patch("fitatu_api.facade.FitatuApiClient", return_value=mock_client):
            result = lib.remove_day_item_via_api(
                target_date=TEST_DATE, meal_key="breakfast", item_id="x", user_id=TEST_USER
            )

        assert result["status"] == "error"


# ---------------------------------------------------------------------------
# TestRemoveDayItemWithStrategyViaApi
# ---------------------------------------------------------------------------


class TestRemoveDayItemWithStrategyViaApi:
    def test_success_delegates_item_kind(self) -> None:
        planner_payload = {"ok": True, "resolvedKind": "custom_add_item"}
        lib = _lib()
        mock_client = _mock_client(planner_payload)

        with patch("fitatu_api.facade.FitatuApiClient", return_value=mock_client):
            result = lib.remove_day_item_with_strategy_via_api(
                target_date=TEST_DATE,
                meal_key="lunch",
                item_id="c1",
                item_kind="custom_add_item",
                user_id=TEST_USER,
            )

        assert result["status"] == "ok"
        mock_client.planner.remove_day_item_with_strategy.assert_called_once()
        call_kwargs = mock_client.planner.remove_day_item_with_strategy.call_args.kwargs
        assert call_kwargs["item_kind"] == "custom_add_item"

    def test_missing_user_id_returns_error(self) -> None:
        lib = FitatuLibrary(session_data={"bearer_token": "tok"})
        result = lib.remove_day_item_with_strategy_via_api(
            target_date=TEST_DATE, meal_key="lunch", item_id="x"
        )
        assert result["status"] == "error"


# ---------------------------------------------------------------------------
# TestRemoveDayItemsByKindViaApi
# ---------------------------------------------------------------------------


class TestRemoveDayItemsByKindViaApi:
    def test_success(self) -> None:
        planner_payload = {"ok": True, "removedCount": 2}
        lib = _lib()
        mock_client = _mock_client(planner_payload)

        with patch("fitatu_api.facade.FitatuApiClient", return_value=mock_client):
            result = lib.remove_day_items_by_kind_via_api(
                target_date=TEST_DATE,
                item_kind="normal_item",
                user_id=TEST_USER,
            )

        assert result["status"] == "ok"
        assert result["result"]["removedCount"] == 2

    def test_missing_user_id_returns_error(self) -> None:
        lib = FitatuLibrary(session_data={"bearer_token": "tok"})
        result = lib.remove_day_items_by_kind_via_api(
            target_date=TEST_DATE, item_kind="auto"
        )
        assert result["status"] == "error"


# ---------------------------------------------------------------------------
# TestUpdateDayItemViaApi
# ---------------------------------------------------------------------------


class TestUpdateDayItemViaApi:
    def test_success(self) -> None:
        planner_payload = {"ok": True}
        lib = _lib()
        mock_client = _mock_client(planner_payload)

        with patch("fitatu_api.facade.FitatuApiClient", return_value=mock_client):
            result = lib.update_day_item_via_api(
                target_date=TEST_DATE,
                meal_key="breakfast",
                item_id="i1",
                measure_quantity=3.0,
                user_id=TEST_USER,
            )

        assert result["status"] == "ok"
        mock_client.planner.update_day_item.assert_called_once()

    def test_missing_user_id_returns_error(self) -> None:
        lib = FitatuLibrary(session_data={"bearer_token": "tok"})
        result = lib.update_day_item_via_api(
            target_date=TEST_DATE, meal_key="breakfast", item_id="x", measure_quantity=1.0
        )
        assert result["status"] == "error"


# ---------------------------------------------------------------------------
# TestGetRecipesCatalogViaApi
# ---------------------------------------------------------------------------


class TestGetRecipesCatalogViaApi:
    def test_success_full_catalog(self) -> None:
        catalog = {"categories": [{"id": "main", "recipes": []}]}
        lib = _lib()
        mock_client = _mock_client()
        mock_client.get_recipes_catalog.return_value = catalog

        with patch.object(lib, "_build_client", return_value=mock_client):
            result = lib.get_recipes_catalog_via_api()

        assert result["status"] == "ok"
        assert result["result"] == catalog

    def test_success_single_category(self) -> None:
        category = {"id": "vegan", "recipes": []}
        lib = _lib()
        mock_client = _mock_client()
        mock_client.get_recipes_catalog_category.return_value = category

        with patch.object(lib, "_build_client", return_value=mock_client):
            result = lib.get_recipes_catalog_via_api(category_id="vegan")

        assert result["status"] == "ok"
        assert result["result"] == category

    def test_api_error_caught(self) -> None:
        lib = _lib()
        mock_client = _mock_client()
        mock_client.get_recipes_catalog.side_effect = FitatuApiError("down", 503)

        with patch.object(lib, "_build_client", return_value=mock_client):
            result = lib.get_recipes_catalog_via_api()

        assert result["status"] == "error"
        assert result["status_code"] == 503


# ---------------------------------------------------------------------------
# TestGetRecipeViaApi
# ---------------------------------------------------------------------------


class TestGetRecipeViaApi:
    def test_success(self) -> None:
        recipe = {"id": 42, "name": "Pancakes"}
        lib = _lib()
        mock_client = _mock_client()
        mock_client.get_recipe.return_value = recipe

        with patch.object(lib, "_build_client", return_value=mock_client):
            result = lib.get_recipe_via_api(recipe_id=42)

        assert result["status"] == "ok"
        assert result["result"]["name"] == "Pancakes"

    def test_api_error_caught(self) -> None:
        lib = _lib()
        mock_client = _mock_client()
        mock_client.get_recipe.side_effect = FitatuApiError("nope", 404)

        with patch.object(lib, "_build_client", return_value=mock_client):
            result = lib.get_recipe_via_api(recipe_id=999)

        assert result["status"] == "error"


# ---------------------------------------------------------------------------
# TestSearchFood
# ---------------------------------------------------------------------------


class TestSearchFood:
    def test_returns_list(self) -> None:
        items = [{"foodId": 1, "name": "Banana"}]
        lib = _lib()
        mock_client = MagicMock()
        mock_client.search_food.return_value = items

        with patch("fitatu_api.facade.FitatuApiClient", return_value=mock_client):
            result = lib.search_food("banana")

        assert result == items

    def test_returns_empty_on_api_error(self) -> None:
        lib = _lib()
        mock_client = MagicMock()
        mock_client.search_food.side_effect = FitatuApiError("search down")

        with patch("fitatu_api.facade.FitatuApiClient", return_value=mock_client):
            result = lib.search_food("banana")

        assert result == []


# ---------------------------------------------------------------------------
# TestDescribeSession
# ---------------------------------------------------------------------------


class TestDescribeSession:
    def test_delegates_to_client(self) -> None:
        state = {"lifecycle_state": "healthy"}
        lib = _lib()
        mock_client = _mock_client()
        mock_client.describe_auth_state.return_value = state

        with patch.object(lib, "_build_client", return_value=mock_client):
            result = lib.describe_session()

        assert result == state
        mock_client.describe_auth_state.assert_called_once()


# ---------------------------------------------------------------------------
# TestManagementReport
# ---------------------------------------------------------------------------


class TestManagementReport:
    def test_delegates_to_client(self) -> None:
        report = {"lifecycle_state": "healthy", "endpoints": {}}
        lib = _lib()
        mock_client = _mock_client()
        mock_client.management_report.return_value = report

        with patch.object(lib, "_build_client", return_value=mock_client):
            result = lib.management_report()

        assert result == report

    def test_include_tokens_passed_through(self) -> None:
        lib = _lib()
        mock_client = _mock_client()
        mock_client.management_report.return_value = {}

        with patch.object(lib, "_build_client", return_value=mock_client):
            lib.management_report(include_tokens=True)

        mock_client.management_report.assert_called_once_with(include_tokens=True)


# ---------------------------------------------------------------------------
# TestExportSessionContext
# ---------------------------------------------------------------------------


class TestExportSessionContext:
    def test_delegates_to_auth(self) -> None:
        snapshot = {"fitatu_user_id": TEST_USER}
        lib = _lib()
        mock_client = _mock_client()
        mock_client.auth.to_session_data.return_value = snapshot

        with patch.object(lib, "_build_client", return_value=mock_client):
            result = lib.export_session_context()

        assert result == snapshot
        mock_client.auth.to_session_data.assert_called_once_with(include_tokens=False)


# ---------------------------------------------------------------------------
# TestGetDayMacrosViaApi
# ---------------------------------------------------------------------------

_ITEM_A = {
    "planDayDietItemId": "item-a",
    "productId": 1,
    "name": "Eggs",
    "brand": "Farm",
    "measureName": "g",
    "measureQuantity": 100,
    "weight": 100,
    "energy": 200.0,
    "protein": 14.0,
    "fat": 10.0,
    "carbohydrate": 2.0,
    "fiber": 0.0,
    "sugars": 1.0,
    "salt": 0.3,
    "eaten": True,
}
_ITEM_B = {
    "name": "Oats",
    "energy": 300.0,
    "protein": 8.0,
    "fat": 5.0,
    "carbohydrate": 50.0,
    "fiber": "7.5",
    "sugars": "2.0",
    "salt": None,
}
_ITEM_C = {
    "name": "Yogurt",
    "energy": 100.0,
    "protein": 6.0,
    "fat": 2.0,
    "carbohydrate": 12.0,
    "fiber": 1.0,
    "sugars": 9.0,
    "salt": 0.1,
}

_DAY_RESPONSE = {
    "dietPlan": {
        "breakfast": {
            "mealName": "Breakfast",
            "mealTime": "08:00",
            "recommendedPercent": 30,
            "items": [_ITEM_A, _ITEM_B],
        },
        "snack": {"mealName": "Snack", "items": [_ITEM_C]},
        "dinner": {"items": []},
    }
}


class TestGetDayMacrosViaApi:
    def test_totals_are_summed_correctly(self) -> None:
        lib = _lib()
        mock_client = _mock_client()
        mock_client.planner.get_day.return_value = _DAY_RESPONSE

        with patch("fitatu_api.facade.FitatuApiClient", return_value=mock_client):
            result = lib.get_day_macros_via_api(target_date=TEST_DATE, user_id=TEST_USER)

        assert result["status"] == "ok"
        totals = result["result"]["totals"]
        assert totals["energy"] == 600.0
        assert totals["protein"] == 28.0
        assert totals["fat"] == 17.0
        assert totals["carbohydrate"] == 64.0
        assert totals["fiber"] == 8.5
        assert totals["sugars"] == 12.0
        assert totals["salt"] == 0.4

    def test_date_is_included_in_result(self) -> None:
        lib = _lib()
        mock_client = _mock_client()
        mock_client.planner.get_day.return_value = _DAY_RESPONSE

        with patch("fitatu_api.facade.FitatuApiClient", return_value=mock_client):
            result = lib.get_day_macros_via_api(target_date=TEST_DATE, user_id=TEST_USER)

        assert result["result"]["date"] == TEST_DATE.isoformat()

    def test_meal_breakdown_not_included_by_default(self) -> None:
        lib = _lib()
        mock_client = _mock_client()
        mock_client.planner.get_day.return_value = _DAY_RESPONSE

        with patch("fitatu_api.facade.FitatuApiClient", return_value=mock_client):
            result = lib.get_day_macros_via_api(target_date=TEST_DATE, user_id=TEST_USER)

        assert "meals" not in result["result"]

    def test_meal_breakdown_included_when_requested(self) -> None:
        lib = _lib()
        mock_client = _mock_client()
        mock_client.planner.get_day.return_value = _DAY_RESPONSE

        with patch("fitatu_api.facade.FitatuApiClient", return_value=mock_client):
            result = lib.get_day_macros_via_api(
                target_date=TEST_DATE,
                include_meal_breakdown=True,
                user_id=TEST_USER,
            )

        meals = result["result"]["meals"]
        assert meals["breakfast"]["energy"] == 500.0
        assert meals["breakfast"]["fiber"] == 7.5
        assert meals["snack"]["energy"] == 100.0
        assert meals["dinner"]["energy"] == 0.0

    def test_empty_day_returns_zeros(self) -> None:
        lib = _lib()
        mock_client = _mock_client()
        mock_client.planner.get_day.return_value = {"dietPlan": {}}

        with patch("fitatu_api.facade.FitatuApiClient", return_value=mock_client):
            result = lib.get_day_macros_via_api(target_date=TEST_DATE, user_id=TEST_USER)

        assert result["status"] == "ok"
        assert result["result"]["totals"] == {
            "energy": 0.0,
            "protein": 0.0,
            "fat": 0.0,
            "carbohydrate": 0.0,
            "fiber": 0.0,
            "sugars": 0.0,
            "salt": 0.0,
        }

    def test_missing_user_id_returns_error(self) -> None:
        lib = FitatuLibrary(session_data={"bearer_token": "tok"})
        result = lib.get_day_macros_via_api(target_date=TEST_DATE)
        assert result["status"] == "error"
        assert "user id" in result["message"].lower()

    def test_api_error_caught(self) -> None:
        lib = _lib()
        mock_client = _mock_client()
        mock_client.planner.get_day.side_effect = FitatuApiError("server error", 500)

        with patch("fitatu_api.facade.FitatuApiClient", return_value=mock_client):
            result = lib.get_day_macros_via_api(target_date=TEST_DATE, user_id=TEST_USER)

        assert result["status"] == "error"
        assert result["status_code"] == 500


class TestGetDaySummaryViaApi:
    def test_summary_contains_day_meals_items_and_totals(self) -> None:
        lib = _lib()
        mock_client = _mock_client()
        mock_client.planner.get_day.return_value = _DAY_RESPONSE

        with patch("fitatu_api.facade.FitatuApiClient", return_value=mock_client):
            result = lib.get_day_summary_via_api(target_date=TEST_DATE, user_id=TEST_USER)

        assert result["status"] == "ok"
        summary = result["result"]
        assert summary["user_id"] == TEST_USER
        assert summary["date"] == TEST_DATE.isoformat()
        assert summary["totals"]["energy"] == 600.0
        assert summary["totals"]["fiber"] == 8.5
        assert len(summary["meals"]) == 3

        breakfast = summary["meals"][0]
        assert breakfast["meal_key"] == "breakfast"
        assert breakfast["meal_name"] == "Breakfast"
        assert breakfast["meal_time"] == "08:00"
        assert breakfast["recommended_percent"] == 30
        assert breakfast["item_count"] == 2
        assert breakfast["totals"]["energy"] == 500.0
        assert breakfast["items"][0]["plan_day_diet_item_id"] == "item-a"
        assert breakfast["items"][0]["product_id"] == 1
        assert breakfast["items"][0]["eaten"] is True

    def test_summary_missing_user_id_returns_error(self) -> None:
        lib = FitatuLibrary(session_data={"bearer_token": "tok"})
        result = lib.get_day_summary_via_api(target_date=TEST_DATE)

        assert result["status"] == "error"
        assert "user id" in result["message"].lower()
