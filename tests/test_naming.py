"""The result conventions of the core, enforced on real calls against the fake server.

- Every dict the library builds has snake_case keys. Fitatu's own payloads are passed through
  unchanged under the keys in ``RAW`` (docs/API_REFERENCE.md, "Naming"), so they are not walked.
- ``FitatuApiClient`` and its modules return data and raise; no result is a status envelope.
"""

from __future__ import annotations

import inspect
import re
from collections.abc import Iterator
from typing import Any
from unittest.mock import Mock

import pytest
from fake_server import RECORDED_USER_ID, FakeFitatuServer, recorded

from fitatu_api import FitatuApiClient, FitatuApiError, FitatuAuthContext, PlannerModule

DAY, NEXT_DAY = "2027-01-03", "2027-01-04"
USER = RECORDED_USER_ID
# Keys whose values are Fitatu payloads (rows sent or read, the day sync), or are keyed by a row's API field names.
RAW = frozenset({
    "added_item", "added_items", "added_activity", "replacement_item", "delete_marker", "removed", "before",
    "after", "item", "skipped", "sync_payload", "sync_response", "updates", "validation",
})
# Planner methods that return what Fitatu sent, or are not about results.
PASS_THROUGH = {"get_day", "get_meal", "list_meal_items", "find_meal_item", "get_day_revisions", "sync_days", "sync_single_day", "normalize_meal_key"}


def _planner_results() -> dict[str, Any]:
    day = recorded("day_three_rows")
    product_row, recipe_row, custom_row = (meal["items"][0]["planDayDietItemId"] for meal in day["dietPlan"].values() if meal["items"])
    server = FakeFitatuServer(day, products={"1001": {"measures": [{"id": 10, "name": "g", "weightPerUnit": 1.0}]}})
    planner = server.planner
    results: dict[str, Any] = {
        "set_note": planner.set_note(USER, DAY, note="naming"),
        "resolve_product_measure": planner.resolve_product_measure(product_id=1001, requested_amount=100, requested_unit="g"),
        "add_product_to_day_meal": planner.add_product_to_day_meal(USER, DAY, meal_type="breakfast", product_id=15956231, measure_id=39),
        "add_product_to_day_meal_with_unit": planner.add_product_to_day_meal_with_unit(
            USER, DAY, meal_type="breakfast", product_id=1001, amount=100, unit="g"
        ),
        "add_recipe_to_day_meal": planner.add_recipe_to_day_meal(USER, DAY, meal_type="lunch", recipe_id=590200),
        "add_custom_item_to_day_meal": planner.add_custom_item_to_day_meal(
            USER, DAY, meal_type="snack", name="naming", calories=100, protein_g=1, fat_g=1, carbs_g=1
        ),
        "add_foods_to_day_meal": planner.add_foods_to_day_meal(
            USER, DAY, meal_type="supper", items=[{"product_id": 1001, "measure_id": 10}, {"recipe_id": 590200}]
        ),
        "add_search_result_to_day_meal": planner.add_search_result_to_day_meal(USER, DAY, meal_type="dinner", phrase="jajko kurze"),
        "update_day_item": planner.update_day_item(USER, DAY, meal_type="breakfast", item_id=product_row, measure_quantity=2),
        "classify_day_item_for_removal": planner.classify_day_item_for_removal(USER, DAY, meal_type="breakfast", item_id=product_row),
        "list_day_items_for_removal": planner.list_day_items_for_removal(USER, DAY),
        "copy_meal": planner.copy_meal(USER, DAY, from_meal_type="breakfast", to_day=NEXT_DAY),
        "move_day_item": planner.move_day_item(USER, DAY, from_meal_type="lunch", item_id=recipe_row, to_meal_type="dinner"),
        "replace_day_item_with_custom_item": planner.replace_day_item_with_custom_item(
            USER, DAY, meal_type="snack", item_id=custom_row, name="naming", calories=1, protein_g=0, fat_g=0, carbs_g=0
        ),
        "remove_day_items_by_kind (dry run)": planner.remove_day_items_by_kind(USER, DAY, item_kind="auto", all_meals=True, dry_run=True),
        "remove_day_item": planner.remove_day_item(USER, DAY, meal_type="breakfast", item_id=product_row),
        "remove_day_item (not found)": planner.remove_day_item(USER, DAY, meal_type="breakfast", item_id=product_row),
    }
    added = results["add_custom_item_to_day_meal"]["row_id"]
    results["rollback_added_item"] = planner.rollback_added_item(USER, DAY, meal_type="snack", item_id=added)
    results["remove_day_items_by_kind"] = planner.remove_day_items_by_kind(
        USER, DAY, item_kind="auto", all_meals=True, row_ids=[results["add_recipe_to_day_meal"]["row_id"], "gone"]
    )
    results["add_activity"] = planner.add_activity(USER, DAY, activity_id=535, minutes=30, energy=123)
    results["remove_activity"] = planner.remove_activity(USER, DAY, item_id=results["add_activity"]["row_id"])
    results["remove_activity (not found)"] = planner.remove_activity(USER, DAY, item_id=results["add_activity"]["row_id"])
    return results


def _client_results() -> dict[str, Any]:
    server = FakeFitatuServer()
    client = FitatuApiClient(FitatuAuthContext(bearer_token="bearer", refresh_token="refresh", fitatu_user_id=USER), persist_tokens=False)
    client.request = server.request  # type: ignore[method-assign]
    server.routes[("POST", "/token/refresh")] = {"token": "new-bearer"}
    server.routes[("GET", f"/users/{USER}/subscriptions")] = {"subscriptions": []}
    server.routes[("GET", f"/search/food/user/{USER}")] = [{"type": "PRODUCT", "foodId": n, "brand": "B"} for n in (1, 2, 3)]
    client.delete_product = Mock(side_effect=[{}, FitatuApiError("boom", status_code=500)])  # type: ignore[method-assign]
    return {
        "describe_auth_state": client.describe_auth_state(),
        "refresh_access_token": client.refresh_access_token(),
        "user_settings.get_premium": client.user_settings.get_premium(USER),
        "cleanup_duplicate_user_products": client.cleanup_duplicate_user_products(
            USER, "naming", DAY, brand="B", keep_product_id=1, dry_run=False
        ),
    }


RESULTS = {**_planner_results(), **_client_results()}


def _library_dicts(value: Any, path: str) -> Iterator[tuple[str, dict[str, Any]]]:
    """Every dict the library built in ``value``, with where it is; Fitatu payloads under ``RAW`` keys are skipped."""
    if isinstance(value, dict):
        yield path, value
        for key, inner in value.items():
            if key not in RAW:
                yield from _library_dicts(inner, f"{path}.{key}")
    elif isinstance(value, list):
        for index, inner in enumerate(value):
            yield from _library_dicts(inner, f"{path}[{index}]")


def test_every_planner_result_is_checked() -> None:
    public = {name for name, _ in inspect.getmembers(PlannerModule, inspect.isfunction) if not name.startswith("_")}
    assert public - PASS_THROUGH <= {name.split(" ")[0] for name in RESULTS}, "add the new planner method to _planner_results"


@pytest.mark.parametrize("name", RESULTS)
def test_library_built_keys_are_snake_case(name: str) -> None:
    camel = {f"{path}.{key}" for path, built in _library_dicts(RESULTS[name], name) for key in built if re.search("[A-Z]", key)}
    assert not camel, f"camelCase keys in a library-built result (rename them, or list a raw payload in RAW): {sorted(camel)}"


@pytest.mark.parametrize("name", RESULTS)
def test_no_core_result_is_a_status_envelope(name: str) -> None:
    envelopes = [path for path, built in _library_dicts(RESULTS[name], name) if built.get("status") in ("ok", "partial", "error")]
    assert not envelopes, f"the core returns data and raises, but {envelopes} carry a status"
