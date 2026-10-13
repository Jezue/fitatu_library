"""Unit tests for FitatuLibrary (high-level facade) with a mocked client."""

from __future__ import annotations

import base64
import inspect
import json
import stat
from collections.abc import Iterator
from datetime import date
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from fitatu_api.exceptions import (
    FitatuApiError,
    FitatuAuthError,
    FitatuRateLimitError,
    describe_error,
)
from fitatu_api.facade import FitatuLibrary
from fitatu_api.service_modules import MeasurementsModule

TEST_DATE = date(2026, 1, 15)
TEST_USER = "99"


@pytest.fixture()
def client() -> MagicMock:
    client = MagicMock()
    client.auth.fitatu_user_id = TEST_USER
    return client


@pytest.fixture()
def lib(client: MagicMock) -> Iterator[FitatuLibrary]:
    with patch("fitatu_api.facade.FitatuApiClient", return_value=client):
        yield FitatuLibrary({"fitatu_user_id": TEST_USER})


PUBLIC_METHODS = [name for name, _ in inspect.getmembers(FitatuLibrary, inspect.isfunction) if not name.startswith("_")]


@pytest.mark.parametrize("name", PUBLIC_METHODS)
def test_public_methods_take_documented_keyword_arguments(name: str) -> None:
    method = getattr(FitatuLibrary, name)
    params = [param for param in inspect.signature(method).parameters.values() if param.name != "self"]

    assert all(param.kind is inspect.Parameter.KEYWORD_ONLY for param in params)
    doc = inspect.getdoc(method) or ""
    assert [param.name for param in params if f"{param.name}:" not in doc] == []


class TestEnvelope:
    def test_ok_wraps_the_result(self, lib: FitatuLibrary, client: MagicMock) -> None:
        client.get_recipe.return_value = {"id": 42}

        assert lib.get_recipe_via_api(recipe_id=42) == {"status": "ok", "result": {"id": 42}}
        client.get_recipe.assert_called_once_with(42)

    def test_a_planner_write_the_reread_did_not_confirm_is_partial(self, lib: FitatuLibrary, client: MagicMock) -> None:
        client.planner.update_day_item.return_value = {"ok": False, "validation": {"measureQuantity": False}}

        result = lib.update_day_item_via_api(target_date=TEST_DATE, meal_type="breakfast", item_id="i1", measure_quantity=2)

        assert result["status"] == "partial"
        assert result["result"]["validation"] == {"measureQuantity": False}

    def test_api_errors_are_described(self, lib: FitatuLibrary, client: MagicMock) -> None:
        error = FitatuRateLimitError("GET /day -> 429", status_code=429, retry_after=3)
        client.planner.get_day.side_effect = error

        result = lib.get_day_summary_via_api(target_date=TEST_DATE)

        assert result == {"status": "error", "operation": "get_day_summary_via_api", **describe_error(error)}
        assert (result["error_type"], result["retryable"], result["retry_after"]) == ("rate_limit", True, 3)

    def test_any_exception_becomes_an_error_result(self, lib: FitatuLibrary, client: MagicMock) -> None:
        client.get_recipes_catalog.side_effect = KeyError("categories")

        result = lib.get_recipes_catalog_via_api()

        assert (result["status"], result["operation"], result["error_type"]) == (
            "error",
            "get_recipes_catalog_via_api",
            "unexpected",
        )

    def test_a_session_without_user_id_is_an_auth_error(self, client: MagicMock) -> None:
        client.auth.fitatu_user_id = None
        with patch("fitatu_api.facade.FitatuApiClient", return_value=client):
            result = FitatuLibrary({}).get_day_summary_via_api(target_date=TEST_DATE)

        assert (result["status"], result["error_type"]) == ("error", "auth")
        assert "user id" in result["message"]
        client.planner.get_day.assert_not_called()


class TestSharedClient:
    def test_the_context_manager_closes_the_client(self, client: MagicMock) -> None:
        with patch("fitatu_api.facade.FitatuApiClient", return_value=client), FitatuLibrary({}) as lib:
            lib.get_recipe_via_api(recipe_id=1)

        client.close.assert_called_once_with()
        FitatuLibrary({}).close()  # nothing built yet, nothing to close

    def test_session_helpers_use_the_shared_client(self, lib: FitatuLibrary, client: MagicMock) -> None:
        client.describe_auth_state.return_value = {"has_bearer_token": False}
        client.auth.to_session_data.return_value = {"fitatu_user_id": TEST_USER}
        client.auth.bearer_token = client.auth.refresh_token = None

        assert lib.describe_session() == {"has_bearer_token": False}
        assert lib.export_session_context(include_tokens=True) == {"fitatu_user_id": TEST_USER}
        assert lib.clear_session(clear_token_store=False) == {"status": "ok", "session": {"has_bearer_token": False}}

        client.auth.to_session_data.assert_called_once_with(include_tokens=True)
        client.clear_auth.assert_called_once_with(clear_token_store=False)
        assert (lib.session_data["bearer_token"], lib.session_data["refresh_token"]) == (None, None)


class TestPlannerWrappers:
    def test_add_product_by_unit_resolves_the_measure(self, lib: FitatuLibrary, client: MagicMock) -> None:
        client.planner.add_product_to_day_meal_with_unit.return_value = {"ok": True, "rowId": "r1"}

        result = lib.add_product_to_day_meal_via_api(
            target_date=TEST_DATE, meal_type="breakfast", product_id=1001, measure_unit="g", measure_amount=180
        )

        assert result == {"status": "ok", "result": {"ok": True, "rowId": "r1"}}
        kwargs = client.planner.add_product_to_day_meal_with_unit.call_args.kwargs
        assert (kwargs["meal_type"], kwargs["amount"], kwargs["unit"]) == ("breakfast", 180, "g")

    def test_add_product_by_measure_id_takes_measure_amount_too(self, lib: FitatuLibrary, client: MagicMock) -> None:
        lib.add_product_to_day_meal_via_api(
            target_date=TEST_DATE, meal_type="lunch", product_id=1, measure_id=5, measure_amount=3
        )

        kwargs = client.planner.add_product_to_day_meal.call_args.kwargs
        assert (kwargs["measure_id"], kwargs["measure_quantity"]) == (5, 3)

    def test_add_product_needs_a_measure(self, lib: FitatuLibrary, client: MagicMock) -> None:
        result = lib.add_product_to_day_meal_via_api(target_date=TEST_DATE, meal_type="breakfast", product_id=1)

        assert (result["status"], result["error_type"]) == ("error", "validation")
        assert "measure_id" in result["message"]
        client.planner.add_product_to_day_meal.assert_not_called()

    def test_remove_by_kind_and_move_forward_their_scope(self, lib: FitatuLibrary, client: MagicMock) -> None:
        client.planner.remove_day_items_by_kind.return_value = {"ok": True, "removedCount": 2}
        client.planner.move_day_item.return_value = {"ok": True}

        lib.remove_day_items_by_kind_via_api(target_date=TEST_DATE, item_kind="auto", all_meals=True, max_items=3)
        lib.move_day_item_via_api(from_date=TEST_DATE, from_meal_type="breakfast", item_id="i1", to_meal_type="lunch")

        client.planner.remove_day_items_by_kind.assert_called_once_with(
            TEST_USER, TEST_DATE, item_kind="auto", meal_type=None, all_meals=True, row_ids=None, max_items=3,
            dry_run=False,
        )
        client.planner.move_day_item.assert_called_once_with(
            TEST_USER, TEST_DATE, from_meal_type="breakfast", item_id="i1", to_day=None, to_meal_type="lunch"
        )


_DAY_RESPONSE = {
    "dietPlan": {
        "breakfast": {
            "mealName": "Breakfast",
            "items": [
                {"planDayDietItemId": "item-a", "energy": 200.0, "protein": 14.0, "fiber": 0.0, "eaten": True},
                {"name": "Oats", "energy": 300.0, "protein": 8.0, "fiber": "7.5", "salt": None},
            ],
        },
        "snack": {"items": [{"name": "Yogurt", "energy": 100.0, "protein": 6.0, "fiber": 1.0}]},
        "dinner": {"items": []},
    }
}


class TestDayReads:
    def test_macros_sum_every_row_and_the_eaten_ones(self, lib: FitatuLibrary, client: MagicMock) -> None:
        client.planner.get_day.return_value = _DAY_RESPONSE

        result = lib.get_day_macros_via_api(target_date="2026-01-15")["result"]

        client.planner.get_day.assert_called_once_with(TEST_USER, TEST_DATE)
        assert result["date"] == "2026-01-15"
        assert (result["totals"]["energy"], result["totals"]["protein"], result["totals"]["fiber"]) == (600.0, 28.0, 8.5)
        assert result["eaten_totals"]["energy"] == 200.0
        assert "meals" not in result

    def test_a_malformed_date_is_a_validation_error(self, lib: FitatuLibrary, client: MagicMock) -> None:
        result = lib.get_range_summary_via_api(from_date="15.01.2026", to_date=TEST_DATE)

        assert (result["status"], result["error_type"]) == ("error", "validation")
        assert "YYYY-MM-DD" in result["message"]

    def test_macros_per_meal_on_request(self, lib: FitatuLibrary, client: MagicMock) -> None:
        client.planner.get_day.return_value = _DAY_RESPONSE

        meals = lib.get_day_macros_via_api(target_date=TEST_DATE, include_meal_breakdown=True)["result"]["meals"]

        assert {meal: totals["energy"] for meal, totals in meals.items()} == {
            "breakfast": 500.0,
            "snack": 100.0,
            "dinner": 0.0,
        }

    def test_summary_names_the_user_date_and_rows(self, lib: FitatuLibrary, client: MagicMock) -> None:
        client.planner.get_day.return_value = _DAY_RESPONSE

        summary = lib.get_day_summary_via_api(target_date=TEST_DATE)["result"]

        client.planner.get_day.assert_called_once_with(TEST_USER, TEST_DATE)
        assert (summary["user_id"], summary["date"]) == (TEST_USER, "2026-01-15")
        assert summary["meals"][0]["items"][0]["row_id"] == "item-a"
        assert summary["eaten_totals"]["energy"] == 200.0

    def test_api_error_while_reading_the_day(self, lib: FitatuLibrary, client: MagicMock) -> None:
        client.planner.get_day.side_effect = FitatuApiError("GET /day -> 500", status_code=500)

        result = lib.get_day_macros_via_api(target_date=TEST_DATE)

        assert (result["status"], result["status_code"], result["error_type"]) == ("error", 500, "server")


class TestAccountReads:
    def test_daily_targets_flatten_settings(self, lib: FitatuLibrary, client: MagicMock) -> None:
        settings = {
            "date": "2026-10-01",
            "userDietSettings": {"energy": 2400, "proteinWeight": 150, "proteinPercentage": 25},
            "calculatedValues": {"BMR": 1900, "TMR": 2700},
        }
        client.user_settings.get_new.return_value = settings

        result = lib.get_daily_targets_via_api(target_date=TEST_DATE)["result"]

        assert (result["energy"], result["protein"], result["bmr"]) == (2400, {"grams": 150, "percent": 25}, 1900)
        assert (result["date"], result["settings_since"]) == (TEST_DATE.isoformat(), "2026-10-01")
        assert result["raw"] == settings
        client.user_settings.get_new.assert_called_once_with(TEST_USER, TEST_DATE)

    def test_weight_history_walks_every_page(self, lib: FitatuLibrary, client: MagicMock) -> None:
        first = [{"date": f"d{i}", "value": 70.0} for i in range(41)]
        client.measurements.get_weight_history.side_effect = [first, [{"date": "last", "value": 71.0}]]

        result = lib.get_weight_history_via_api(from_date=date(2026, 1, 1), to_date=TEST_DATE)

        assert len(result["result"]) == 42
        assert [c.kwargs["page"] for c in client.measurements.get_weight_history.call_args_list] == [1, 2]

    def test_weight_history_that_never_ends_is_an_error_not_a_cut_list(self, lib: FitatuLibrary, client: MagicMock) -> None:
        client.measurements.get_weight_history.return_value = [{"date": "d", "value": 70.0}] * 41

        result = lib.get_weight_history_via_api()

        assert (result["status"], result["error_type"]) == ("error", "server")
        assert client.measurements.get_weight_history.call_count == 1000

    def test_a_malformed_weight_is_a_validation_error(self, lib: FitatuLibrary, client: MagicMock) -> None:
        client.measurements = MeasurementsModule(client)

        result = lib.log_weight_via_api(weight="72,4", target_date=TEST_DATE)  # type: ignore[arg-type]

        assert (result["status"], result["operation"], result["error_type"]) == ("error", "log_weight_via_api", "validation")
        client.request.assert_not_called()

    def test_a_bad_date_names_the_facade_parameter(self, lib: FitatuLibrary, client: MagicMock) -> None:
        added = lib.add_custom_item_to_day_meal_via_api(
            target_date="13.10.2026", meal_type="lunch", name="X", calories=1, protein_g=0, fat_g=0, carbs_g=0
        )
        moved = lib.move_day_item_via_api(from_date=TEST_DATE, from_meal_type="lunch", item_id="x", to_date="bad")
        weighed = lib.log_weight_via_api(weight=70, target_date="today")

        assert [result["message"].split()[0] for result in (added, moved, weighed)] == ["target_date", "to_date", "target_date"]
        assert client.planner.method_calls == [] and client.measurements.method_calls == []

    def test_an_empty_export_has_no_result(self, lib: FitatuLibrary, client: MagicMock) -> None:
        client.summary.export_diary.return_value = None

        assert lib.export_diary_via_api(from_date=TEST_DATE, to_date=TEST_DATE) == {"status": "ok", "result": None}

    def test_text_exports_come_back_as_text(self, lib: FitatuLibrary, client: MagicMock) -> None:
        client.summary.export_diary.return_value = "Data,Posiłek\n".encode()

        result = lib.export_diary_via_api(from_date="2026-01-01", to_date=TEST_DATE)

        assert result["result"] == {"format": "csv", "size_bytes": 14, "content": "Data,Posiłek\n"}
        client.summary.export_diary.assert_called_once_with(TEST_USER, "2026-01-01", TEST_DATE, fmt="csv")

    def test_binary_exports_come_back_as_base64(self, lib: FitatuLibrary, client: MagicMock) -> None:
        client.summary.export_diary.return_value = b"%PDF-1.7\x00\xff"

        result = lib.export_diary_via_api(from_date=TEST_DATE, to_date=TEST_DATE, fmt="pdf")["result"]

        assert (result["mime_type"], base64.b64decode(result["content_b64"])) == ("application/pdf", b"%PDF-1.7\x00\xff")
        json.dumps(result)

    def test_the_file_is_private_and_not_overwritten_by_default(
        self, lib: FitatuLibrary, client: MagicMock, tmp_path: Path
    ) -> None:
        client.summary.export_diary.return_value = b"a,b\n"
        target = tmp_path / "diary.csv"

        written = lib.export_diary_via_api(from_date=TEST_DATE, to_date=TEST_DATE, path=target)
        target.write_bytes(b"mine")
        refused = lib.export_diary_via_api(from_date=TEST_DATE, to_date=TEST_DATE, path=target)
        assert target.read_bytes() == b"mine"
        replaced = lib.export_diary_via_api(from_date=TEST_DATE, to_date=TEST_DATE, path=str(target), overwrite=True)

        assert written == {"status": "ok", "result": {"format": "csv", "size_bytes": 4, "path": str(target)}}
        assert stat.S_IMODE(target.stat().st_mode) == 0o600
        assert (refused["status"], "exists" in refused["message"]) == ("error", True)
        assert replaced["status"] == "ok"
        assert target.read_bytes() == b"a,b\n"

    def test_overwrite_makes_a_new_private_file(self, lib: FitatuLibrary, client: MagicMock, tmp_path: Path) -> None:
        client.summary.export_diary.return_value = b"a,b\n"
        shared = tmp_path / "shared.csv"
        shared.write_bytes(b"old export")
        shared.chmod(0o644)
        notes = tmp_path / "notes.txt"
        notes.write_bytes(b"precious")
        link = tmp_path / "link.csv"
        link.symlink_to(notes)

        for target in (shared, link):
            result = lib.export_diary_via_api(from_date=TEST_DATE, to_date=TEST_DATE, path=target, overwrite=True)

            assert result["status"] == "ok"
            assert not target.is_symlink()
            assert stat.S_IMODE(target.stat().st_mode) == 0o600
            assert target.read_bytes() == b"a,b\n"
        assert notes.read_bytes() == b"precious"

    @pytest.mark.parametrize(("overwrite", "move"), [(True, "replace"), (False, "link")])
    def test_a_failed_write_leaves_the_directory_as_it_was(
        self, lib: FitatuLibrary, client: MagicMock, tmp_path: Path, overwrite: bool, move: str
    ) -> None:
        client.summary.export_diary.return_value = b"a,b\n"
        target = tmp_path / "diary.csv"
        if overwrite:
            target.write_bytes(b"old export")

        with patch(f"fitatu_api.facade.os.{move}", side_effect=OSError(28, "No space left on device")):
            failed = lib.export_diary_via_api(from_date=TEST_DATE, to_date=TEST_DATE, path=target, overwrite=overwrite)

        assert failed["status"] == "error"
        assert [path.name for path in tmp_path.iterdir()] == (["diary.csv"] if overwrite else [])
        if overwrite:
            assert target.read_bytes() == b"old export"
        else:
            assert lib.export_diary_via_api(from_date=TEST_DATE, to_date=TEST_DATE, path=target)["status"] == "ok"

class TestCatalogWrites:
    def test_create_product_forwards_every_nutrient(self, lib: FitatuLibrary, client: MagicMock) -> None:
        lib.create_product_via_api(
            name="Jogurt", brand="Demo", energy=100, protein=10, fat=2, carbohydrate=8, sodium=40, salt=0.1
        )

        kwargs = client.create_product.call_args.kwargs
        assert (kwargs["name"], kwargs["energy"], kwargs["sodium"], kwargs["salt"], kwargs["fiber"]) == (
            "Jogurt",
            100,
            40,
            0.1,
            None,
        )

    def test_add_user_dish_takes_snake_case_options(self, lib: FitatuLibrary, client: MagicMock) -> None:
        items = [{"foodId": 1, "measureId": 2, "measureQuantity": 100}]

        lib.add_user_dish_via_api(name="Miska", items=items, cooking_time=3, serving=2)

        kwargs = client.create_recipe.call_args.kwargs
        assert (kwargs["cooking_time"], kwargs["serving"], kwargs["recipe_description"]) == (3, "2", "1. automation")


class TestSearchFood:
    def test_search_wraps_the_hits(self, lib: FitatuLibrary, client: MagicMock) -> None:
        client.search_food.return_value = [{"foodId": 1, "name": "Banan"}]

        assert lib.search_food_via_api(phrase="banan") == {"status": "ok", "result": [{"foodId": 1, "name": "Banan"}]}
        client.search_food.assert_called_once_with("banan", locale=None, limit=10, page=1)

    def test_an_expired_session_is_not_an_empty_result(self, lib: FitatuLibrary, client: MagicMock) -> None:
        client.search_food.side_effect = FitatuAuthError("GET /search/new/food -> 401", status_code=401)

        result = lib.search_food_via_api(phrase="banan")

        assert (result["status"], result["error_type"], result["status_code"]) == ("error", "auth", 401)
