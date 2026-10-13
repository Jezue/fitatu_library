"""FitatuTools: every tool through call() against a real client wired to the fake server."""

from __future__ import annotations

import inspect
import json
from pathlib import Path
from typing import Any

import pytest
from fake_server import RECORDED_USER_ID, FakeFitatuServer, recorded

from fitatu_api import (
    FitatuApiClient,
    FitatuApiError,
    FitatuAuthContext,
    FitatuAuthError,
    FitatuConnectionError,
    FitatuMaintenanceError,
    FitatuRateLimitError,
    FitatuTools,
    FitatuValidationError,
    describe_error,
)
from fitatu_api.agent import TOOLS, anthropic_tools, openai_tools

DAY = "2027-01-03"  # the recorded day
WEIGHT_DAY = "2020-01-01"  # the recorded measurement date
PRODUCT_ID = recorded("product_details")["id"]  # the first catalog hit the recorder logged
RECIPE_ID = 590200  # fixed in the recorder

# The recorded day with a product (breakfast), a recipe (lunch), a custom row (snack) and an activity.
SEED = {**recorded("day_three_rows"), "activityPlan": recorded("day_after_activity")["activityPlan"]}
ROWS = {meal: bucket["items"][0]["planDayDietItemId"] for meal, bucket in SEED["dietPlan"].items() if bucket["items"]}
ACTIVITY_ROW = SEED["activityPlan"][0]["uuid"]

# Every tool with arguments that work against the fake.
ARGUMENTS: dict[str, dict[str, Any]] = {
    "get_day": {"date": DAY},
    "get_targets": {"date": DAY},
    "get_range_summary": {"from_date": DAY, "to_date": DAY},
    "search_food": {"phrase": "jajko kurze", "limit": 2},
    "get_food_measures": {"food_id": PRODUCT_ID, "food_type": "PRODUCT"},
    "get_recent_foods": {"meal": "breakfast"},
    "get_favorite_foods": {},
    "get_weight_history": {"from_date": WEIGHT_DAY, "to_date": WEIGHT_DAY},
    "get_water": {"date": DAY},
    "search_activity": {"phrase": "bieganie"},
    "add_food": {"date": DAY, "meal": "dinner", "food_id": PRODUCT_ID, "food_type": "PRODUCT", "quantity": 70, "unit": "g"},
    "add_custom_item": {
        "date": DAY,
        "meal": "supper",
        "name": "Kanapka",
        "energy_kcal": 250,
        "protein_g": 10,
        "fat_g": 8.5,
        "carbohydrate_g": 30,
    },
    "update_item": {"date": DAY, "row_id": ROWS["breakfast"], "eaten": True},
    "move_item": {"date": DAY, "row_id": ROWS["snack"], "to_meal": "dinner"},
    "remove_items": {"date": DAY, "row_ids": [ROWS["lunch"]]},
    "log_weight": {"date": WEIGHT_DAY, "weight_kg": 70},
    "set_water": {"date": DAY, "water_ml": 1500},
    "add_activity": {"date": DAY, "activity_id": 535, "minutes": 30, "energy_kcal": 123},
    "remove_activity": {"date": DAY, "row_id": ACTIVITY_ROW},
}
READS = {"get_day", "get_targets", "get_range_summary", "search_food", "get_food_measures", "get_recent_foods"}
READS |= {"get_favorite_foods", "get_weight_history", "get_water", "search_activity"}


@pytest.fixture()
def fake() -> FakeFitatuServer:
    server = FakeFitatuServer(SEED)
    # Not in the recording. Recent and favourite foods are lists of food search hits in the
    # app; the water reply has the shape client.water already reads.
    server.routes[("GET", f"/search/food/{RECORDED_USER_ID}/last-used")] = recorded("search_food")
    server.routes[("GET", f"/users/{RECORDED_USER_ID}/favorite/foods")] = recorded("search_food_recipe")
    server.routes[("GET", f"/water/{RECORDED_USER_ID}/{DAY}")] = {"water": {"waterConsumption": 750}}
    server.routes[("PUT", f"/water/{RECORDED_USER_ID}/{DAY}")] = None
    return server


@pytest.fixture()
def tools(fake: FakeFitatuServer) -> FitatuTools:
    client = FitatuApiClient(FitatuAuthContext(bearer_token="t", fitatu_user_id=RECORDED_USER_ID))
    client.request = fake.request  # type: ignore[method-assign]
    return FitatuTools(client)


def data(tools: FitatuTools, tool: str, /, **arguments: Any) -> Any:
    result = tools.call(tool, arguments)
    json.dumps(result)
    assert result["ok"], result
    return result["data"]


def rows(fake: FakeFitatuServer, day: str = DAY) -> dict[str, tuple[str, dict[str, Any]]]:
    """The day's food rows as ``{row_id: (meal, row)}``."""
    return {
        row["planDayDietItemId"]: (meal, row)
        for meal, bucket in fake.day_view(day)["dietPlan"].items()
        for row in bucket["items"]
    }


def error_type(tools: FitatuTools, name: Any, arguments: Any) -> str:
    result = tools.call(name, arguments)
    json.dumps(result)
    assert result["ok"] is False
    return str(result["error"]["error_type"])


# -- tool definitions -------------------------------------------------------------------


def test_every_tool_has_a_json_schema_and_annotations() -> None:
    assert len({tool["name"] for tool in TOOLS}) == len(TOOLS)
    for tool in TOOLS:
        assert set(tool) == {"name", "description", "input_schema", "annotations"}
        assert tool["description"].strip()
        schema = tool["input_schema"]
        assert schema["type"] == "object" and isinstance(schema["properties"], dict)
        assert set(schema["required"]) <= set(schema["properties"])
        for prop in schema["properties"].values():
            assert prop["type"] in {"string", "integer", "number", "boolean", "array"} and prop["description"]
        assert {key: type(value) for key, value in tool["annotations"].items()} == {
            "readOnlyHint": bool,
            "destructiveHint": bool,
            "idempotentHint": bool,
        }
    json.dumps(TOOLS)


def test_every_tool_is_a_method_taking_its_schema_arguments() -> None:
    for tool in TOOLS:
        params = list(inspect.signature(getattr(FitatuTools, tool["name"])).parameters.values())[1:]
        schema = tool["input_schema"]
        assert [p.name for p in params] == list(schema["properties"]), tool["name"]
        assert [p.name for p in params if p.default is inspect.Parameter.empty] == schema["required"], tool["name"]


def test_annotations_mark_reads_and_what_can_overwrite_the_diary() -> None:
    hints = {tool["name"]: tool["annotations"] for tool in TOOLS}
    assert {name for name, hint in hints.items() if hint["readOnlyHint"]} == READS
    assert {name for name, hint in hints.items() if hint["destructiveHint"]} == {
        "update_item",
        "move_item",
        "remove_items",
        "remove_activity",
        "log_weight",
        "set_water",
    }
    assert {name for name, hint in hints.items() if hint["idempotentHint"]} == READS | {
        "remove_items",
        "remove_activity",
        "log_weight",
        "set_water",
    }


def test_provider_formats_carry_the_same_tools() -> None:
    names = [tool["name"] for tool in TOOLS]
    assert [tool["name"] for tool in anthropic_tools()] == names
    assert all(set(tool) == {"name", "description", "input_schema"} for tool in anthropic_tools())
    assert [tool["function"]["name"] for tool in openai_tools()] == names
    assert all(tool["type"] == "function" and "parameters" in tool["function"] for tool in openai_tools())


def test_provider_formats_are_copies_a_caller_may_adapt(tools: FitatuTools) -> None:
    # OpenAI strict mode: every property required, the optional ones nullable.
    adapted = openai_tools()
    for tool in adapted:
        params = tool["function"]["parameters"]
        for key, prop in params["properties"].items():
            if key not in params["required"]:
                prop["type"] = [prop["type"], "null"]
        params["required"] = list(params["properties"])
    properties = {tool["function"]["name"]: tool["function"]["parameters"]["properties"] for tool in adapted}
    # converted once each, although search_food and search_activity, or from_date and to_date, share a schema
    assert properties["search_activity"]["limit"]["type"] == ["integer", "null"]
    assert properties["get_weight_history"]["to_date"]["type"] == ["string", "null"]
    anthropic_tools()[0]["input_schema"]["required"].clear()
    assert data(tools, "search_food", phrase="owsianka") and error_type(tools, "get_day", {}) == "validation"
    assert openai_tools()[3]["function"]["parameters"]["required"] == ["phrase"]


def test_arguments_cover_every_tool() -> None:
    assert set(ARGUMENTS) == {tool["name"] for tool in TOOLS}


@pytest.mark.parametrize("name", list(ARGUMENTS))
def test_tool_returns_json_safe_data(tools: FitatuTools, name: str) -> None:
    result = data(tools, name, **ARGUMENTS[name])
    assert not isinstance(result, dict) or result.get("ok", True) is True


# -- reads --------------------------------------------------------------------------------


def test_get_day_summarises_the_recorded_day(tools: FitatuTools) -> None:
    day = data(tools, "get_day", date=DAY)
    assert day["date"] == DAY and day["user_id"] == RECORDED_USER_ID
    rows = {item["row_id"]: (meal["meal_key"], item["food_type"]) for meal in day["meals"] for item in meal["items"]}
    assert sorted(rows.values()) == [("breakfast", "PRODUCT"), ("lunch", "RECIPE"), ("snack", "CUSTOM_ITEM")]


def test_get_targets_and_range_summary(tools: FitatuTools) -> None:
    targets = data(tools, "get_targets", date=DAY)
    assert targets["energy"] == 2000 and targets["protein"] == {"grams": 100, "percent": 20}
    assert (targets["date"], targets["settings_since"]) == (DAY, recorded("settings_new")["date"])
    summary = data(tools, "get_range_summary", from_date=DAY, to_date=DAY)
    assert summary["nutrition"]["energy"] == 376 and summary["activity_energy"] == 0
    assert set(summary["nutrition"]) == {"energy", "protein", "fat", "carbohydrate", "fiber", "sugars", "salt"}


def test_food_search_hits_are_trimmed_to_what_add_food_needs(tools: FitatuTools, fake: FakeFitatuServer) -> None:
    hits = data(tools, "search_food", phrase="owsianka")
    assert hits[0] == {
        "food_id": 60477557,
        "type": "RECIPE",
        "name": "Owsianka",
        "brand": None,
        "energy_kcal": 206.76,
        "measure": {"measure_id": 39, "name": "portion", "weight_g": 186, "capacity_ml": None},
    }
    assert {hit["type"] for hit in hits} == {"PRODUCT", "RECIPE"}
    assert fake.requests[-1]["params"]["limit"] == 10
    assert data(tools, "get_recent_foods", meal="lunch")[0]["food_id"] == 24881865
    assert fake.requests[-1]["params"] == {"meal": "lunch"}
    assert len(data(tools, "get_recent_foods", meal="lunch", limit=1)) == 1  # the route itself sends up to 200
    assert tools.call("get_favorite_foods")["data"][0]["food_id"] == 60477557
    assert fake.requests[-1]["params"]["limit"] == 10


def test_get_food_measures_reads_the_product_or_recipe(tools: FitatuTools, fake: FakeFitatuServer) -> None:
    product = data(tools, "get_food_measures", food_id=PRODUCT_ID, food_type="PRODUCT")
    assert product["measures"] == [
        {"measure_id": 39, "name": "portion", "weight_g": 35, "capacity_ml": None, "energy_kcal": 16.8},
        {"measure_id": 1, "name": "g", "weight_g": 1, "capacity_ml": None, "energy_kcal": 0.48},
    ]
    recipe = data(tools, "get_food_measures", food_id=RECIPE_ID, food_type="RECIPE")
    assert recipe["name"] == "Gorąca czekolada" and fake.requests[-1]["path"] == f"/recipes/{RECIPE_ID}"


def test_get_food_measures_refuses_a_recipe_sent_as_a_product(tools: FitatuTools, fake: FakeFitatuServer) -> None:
    def server(method: str, path: str, **kwargs: Any) -> Any:
        if path == f"/products/{RECIPE_ID}":  # what Fitatu answers for a recipe id
            raise FitatuApiError(f"GET {path} -> 404", status_code=404)
        return fake.request(method, path, **kwargs)

    tools.client.request = server  # type: ignore[method-assign]
    assert error_type(tools, "get_food_measures", {"food_id": RECIPE_ID, "food_type": "PRODUCT"}) == "not_found"


def test_weight_water_and_activity_reads(tools: FitatuTools) -> None:
    assert data(tools, "get_weight_history", from_date=WEIGHT_DAY, to_date=WEIGHT_DAY) == [
        {"date": WEIGHT_DAY, "value": 70, "difference": None, "direction": 0}
    ]
    assert data(tools, "get_water", date=DAY) == {"date": DAY, "water_ml": 750}
    running = data(tools, "search_activity", phrase="bieganie")[0]
    assert running == {"activity_id": 535, "name": "Bieganie", "energy_kcal": 340, "energy_for": "5 km w 00:30:00"}


# -- writes -------------------------------------------------------------------------------


def test_add_food_logs_a_product_in_grams(tools: FitatuTools, fake: FakeFitatuServer) -> None:
    added = data(tools, "add_food", **{**ARGUMENTS["add_food"], "eaten": True})
    assert added["meal"] == "dinner" and added["ok"] is True
    meal, row = rows(fake)[added["row_id"]]
    assert (meal, row["productId"], row["measureId"], row["measureQuantity"], row["eaten"]) == ("dinner", PRODUCT_ID, 1, 70, True)


def test_add_food_by_measure_and_a_recipe_in_portions(tools: FitatuTools, fake: FakeFitatuServer) -> None:
    portions = data(tools, "add_food", date=DAY, meal="breakfast", food_id=PRODUCT_ID, food_type="PRODUCT", quantity=2, measure_id=39)
    recipe = data(tools, "add_food", date=DAY, meal="lunch", food_id=RECIPE_ID, food_type="RECIPE", quantity=1.5)
    day = rows(fake)
    assert (day[portions["row_id"]][1]["measureId"], day[portions["row_id"]][1]["measureQuantity"]) == (39, 2)
    assert {key: day[recipe["row_id"]][1][key] for key in ("foodType", "recipeId", "measureId", "measureQuantity")} == {
        "foodType": "RECIPE",
        "recipeId": RECIPE_ID,
        "measureId": 39,
        "measureQuantity": 1.5,
    }


def test_a_retried_add_with_the_same_row_id_logs_one_row(tools: FitatuTools, fake: FakeFitatuServer) -> None:
    row_id = "11111111-2222-1333-8444-555555555555"  # a UUID v1
    for _ in range(2):
        assert data(tools, "add_custom_item", **ARGUMENTS["add_custom_item"], row_id=row_id)["row_id"] == row_id
    assert [rid for rid, (meal, _) in rows(fake).items() if meal == "supper"] == [row_id]


def test_an_add_never_overwrites_a_logged_row(tools: FitatuTools, fake: FakeFitatuServer) -> None:
    data(tools, "update_item", date=DAY, row_id=ROWS["breakfast"], eaten=True)
    same_food = {"date": DAY, "meal": "breakfast", "food_id": PRODUCT_ID, "food_type": "PRODUCT", "quantity": 50, "unit": "g"}
    assert error_type(tools, "add_food", {**same_food, "row_id": ROWS["breakfast"]}) == "validation"
    snack = rows(fake)[ROWS["snack"]][1]  # 8 g of fat, not 8.5
    custom = {**ARGUMENTS["add_custom_item"], "meal": "snack", "name": snack["name"], "row_id": ROWS["snack"]}
    assert error_type(tools, "add_custom_item", custom) == "validation"
    activity = {**ARGUMENTS["add_activity"], "minutes": 5, "energy_kcal": 10, "row_id": ACTIVITY_ROW}
    assert error_type(tools, "add_activity", activity) == "validation"
    assert len(fake.sync_payloads) == 1 and rows(fake)[ROWS["breakfast"]][1]["eaten"] is True


def test_a_retry_after_the_saved_row_changed_says_not_to_add_it_again(tools: FitatuTools) -> None:
    added = data(tools, "add_food", **ARGUMENTS["add_food"])  # its reply was lost, say
    data(tools, "update_item", date=DAY, row_id=added["row_id"], eaten=True)  # e.g. on the phone
    error = tools.call("add_food", {**ARGUMENTS["add_food"], "row_id": added["row_id"]})["error"]
    assert error["error_type"] == "validation" and "do not add it again" in error["message"]
    assert "new row_id" not in error["message"]


def test_add_custom_item_can_log_it_as_eaten(tools: FitatuTools, fake: FakeFitatuServer) -> None:
    added = data(tools, "add_custom_item", **ARGUMENTS["add_custom_item"], eaten=True)
    meal, row = rows(fake)[added["row_id"]]
    assert (meal, row["name"], row["energy"], row["fat"], row["eaten"], added["ok"]) == ("supper", "Kanapka", 250, 8.5, True, True)
    assert len(fake.sync_payloads) == 1


def test_an_eaten_custom_item_is_one_sync_and_its_retry_is_safe(tools: FitatuTools, fake: FakeFitatuServer) -> None:
    arguments = {**ARGUMENTS["add_custom_item"], "eaten": True, "row_id": "4a98003d-c710-11f1-a18f-5d81dc4a5a19"}
    data(tools, "add_custom_item", **arguments)
    data(tools, "add_custom_item", **arguments)  # the same add again, e.g. after a timeout
    assert len(fake.sync_payloads) == 2 and rows(fake)[arguments["row_id"]][1]["eaten"] is True
    # Re-adding it as planned would reset the eaten row, so that id is refused.
    assert error_type(tools, "add_custom_item", {**arguments, "eaten": False}) == "validation"

def test_a_failed_add_names_the_row_id_that_makes_a_retry_safe(
    tools: FitatuTools, fake: FakeFitatuServer, monkeypatch: pytest.MonkeyPatch
) -> None:
    def saved_then_timed_out(payload: dict[str, Any]) -> Any:
        FakeFitatuServer._sync(fake, payload)
        raise FitatuConnectionError("read timeout")

    monkeypatch.setattr(fake, "_sync", saved_then_timed_out)
    error = tools.call("add_food", ARGUMENTS["add_food"])["error"]
    (row_id,) = [rid for rid, (meal, _) in rows(fake).items() if meal == "dinner"]
    assert (error["error_type"], error["retryable"]) == ("network", False) and row_id in error["hint"]
    again = tools.call("add_food", {**ARGUMENTS["add_food"], "row_id": row_id})["error"]
    assert again["retryable"] is True  # with its row_id the same call is safe to repeat
    assert [rid for rid, (meal, _) in rows(fake).items() if meal == "dinner"] == [row_id]


@pytest.mark.parametrize(
    "failure",
    [
        FitatuRateLimitError("POST /diet-plan/1/days -> 429", status_code=429, retry_after=60),
        FitatuAuthError("POST /diet-plan/1/days -> 401", status_code=401),
        FitatuMaintenanceError("technical break", display_to="2027-01-03 12:00"),
    ],
    ids=lambda failure: type(failure).__name__,
)
def test_an_add_that_may_have_landed_names_its_row_id_whatever_failed_next(
    tools: FitatuTools, fake: FakeFitatuServer, monkeypatch: pytest.MonkeyPatch, failure: FitatuApiError
) -> None:
    # e.g. the first sync attempt timed out after it was saved and the client's resends got a 429
    def saved_then_failed(payload: dict[str, Any]) -> Any:
        FakeFitatuServer._sync(fake, payload)
        raise failure

    monkeypatch.setattr(fake, "_sync", saved_then_failed)
    error = tools.call("add_custom_item", ARGUMENTS["add_custom_item"])["error"]
    (row_id,) = [rid for rid, (meal, _) in rows(fake).items() if meal == "supper"]
    assert error["retryable"] is False and row_id in error["hint"]
    assert error["hint"].startswith(describe_error(failure)["hint"] or "The add")
    monkeypatch.undo()
    data(tools, "add_custom_item", **ARGUMENTS["add_custom_item"], row_id=row_id)
    assert [rid for rid, (meal, _) in rows(fake).items() if meal == "supper"] == [row_id]
    # An add refused before anything was sent gets no such hint.
    assert tools.call("add_food", {**ARGUMENTS["add_food"], "measure_id": 1})["error"]["hint"] is None


def test_update_item_changes_quantity_and_eaten(tools: FitatuTools, fake: FakeFitatuServer) -> None:
    updated = data(tools, "update_item", date=DAY, row_id=ROWS["lunch"], quantity=2, eaten=True)
    assert updated == {"date": DAY, "meal": "lunch", "row_id": ROWS["lunch"], "ok": True}
    row = rows(fake)[ROWS["lunch"]][1]
    assert (row["measureQuantity"], row["eaten"]) == (2, True)


def test_move_item_to_another_meal_and_day(tools: FitatuTools, fake: FakeFitatuServer) -> None:
    moved = data(tools, "move_item", date=DAY, row_id=ROWS["snack"], to_meal="dinner", to_date="2027-01-04")
    assert (moved["date"], moved["meal"], moved["ok"]) == ("2027-01-04", "dinner", True)
    assert ROWS["snack"] not in rows(fake)
    assert rows(fake, "2027-01-04")[moved["row_id"]][0] == "dinner"


def test_remove_items_reports_removed_and_missing_row_ids(tools: FitatuTools, fake: FakeFitatuServer) -> None:
    preview = data(tools, "remove_items", date=DAY, row_ids=[ROWS["lunch"], "gone"], dry_run=True)
    assert preview == {"date": DAY, "ok": False, "dry_run": True, "removed": [ROWS["lunch"]], "failed": [], "missing": ["gone"]}
    assert fake.sync_payloads == []

    removed = data(tools, "remove_items", date=DAY, row_ids=[ROWS["lunch"], ROWS["snack"]])
    assert removed["ok"] and sorted(removed["removed"]) == sorted([ROWS["lunch"], ROWS["snack"]])
    assert set(rows(fake)) == {ROWS["breakfast"]}
    again = data(tools, "remove_items", date=DAY, row_ids=[ROWS["lunch"]])
    assert (again["ok"], again["removed"], again["missing"]) == (False, [], [ROWS["lunch"]])


def test_log_weight_and_set_water_send_absolute_values(tools: FitatuTools, fake: FakeFitatuServer) -> None:
    assert data(tools, "log_weight", date=WEIGHT_DAY, weight_kg=70) == {"date": WEIGHT_DAY, "weight_kg": 70}
    assert fake.requests[-1]["method"] == "PUT"
    assert {key: fake.requests[-1]["json_data"][key] for key in ("weight", "weightUnit")} == {"weight": "70.0", "weightUnit": "KG"}
    assert data(tools, "set_water", date=DAY, water_ml=1500) == {"date": DAY, "water_ml": 1500}
    assert fake.requests[-1]["json_data"] == {"waterConsumption": 1500}


def test_add_and_remove_an_activity(tools: FitatuTools) -> None:
    added = data(tools, "add_activity", date=DAY, activity_id=535, minutes=45, energy_kcal=400)
    assert added["ok"] is True and "meal" not in added
    activities = {activity["row_id"]: activity for activity in data(tools, "get_day", date=DAY)["activities"]}
    assert activities[added["row_id"]]["energy"] == 400
    assert data(tools, "remove_activity", date=DAY, row_id=added["row_id"]) == {
        "date": DAY,
        "row_id": added["row_id"],
        "ok": True,
        "reason": None,
        "message": None,
    }
    assert data(tools, "remove_activity", date=DAY, row_id=added["row_id"])["reason"] == "item_not_found"


def test_an_id_from_the_other_list_is_not_found_and_the_day_keeps_it(tools: FitatuTools, fake: FakeFitatuServer) -> None:
    food_row = data(tools, "remove_activity", date=DAY, row_id=ROWS["breakfast"])
    assert (food_row["ok"], food_row["reason"]) == (False, "item_not_found") and ACTIVITY_ROW in food_row["message"]
    assert data(tools, "remove_items", date=DAY, row_ids=[ACTIVITY_ROW])["missing"] == [ACTIVITY_ROW]
    assert ROWS["breakfast"] in rows(fake) and fake.sync_payloads == []


# -- errors -------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("name", "arguments", "expected"),
    [
        ("get_session_tokens", {}, "validation"),
        ("get_day", ["2027-01-03"], "validation"),
        ("get_day", {}, "validation"),
        ("get_day", {"date": None}, "validation"),
        ("get_day", {"date": DAY, "user_id": "1"}, "validation"),
        ("get_day", {"date": "03.01.2027"}, "validation"),
        ("get_day", {"date": 20270103}, "validation"),
        ("get_recent_foods", {"meal": "midnight_snack"}, "validation"),
        ("search_food", {"phrase": "jajko", "limit": 0}, "validation"),
        ("search_food", {"phrase": "jajko", "limit": True}, "validation"),
        ("get_food_measures", {"food_id": PRODUCT_ID, "food_type": "MEAL"}, "validation"),
        ("add_food", {**ARGUMENTS["add_food"], "meal": "midnight_snack"}, "validation"),
        ("add_food", {**ARGUMENTS["add_food"], "quantity": "70"}, "validation"),
        ("add_food", {**ARGUMENTS["add_food"], "quantity": 0}, "validation"),
        ("add_food", {**ARGUMENTS["add_food"], "unit": None}, "validation"),
        ("add_food", {**ARGUMENTS["add_food"], "measure_id": 1}, "validation"),
        ("add_food", {**ARGUMENTS["add_food"], "food_id": RECIPE_ID, "food_type": "RECIPE"}, "validation"),
        ("update_item", {"date": DAY, "row_id": ROWS["breakfast"]}, "validation"),
        ("remove_items", {"date": DAY, "row_ids": ROWS["lunch"]}, "validation"),
        # a remove_items that reached the planner without row_ids would empty the whole day
        ("remove_items", {"date": DAY}, "validation"),
        ("remove_items", {"date": DAY, "row_ids": None}, "validation"),
        ("remove_items", {"date": DAY, "row_ids": []}, "validation"),
        ("add_food", {"date": DAY, "meal": "lunch", "food_id": -5, "food_type": "PRODUCT", "quantity": 1, "measure_id": 39}, "validation"),
        ("add_food", {"date": DAY, "meal": "lunch", "food_id": 0, "food_type": "RECIPE", "quantity": 1}, "validation"),
        ("add_food", {"date": DAY, "meal": "lunch", "food_id": PRODUCT_ID, "food_type": "PRODUCT", "quantity": 1, "measure_id": -1}, "validation"),
        ("add_activity", {**ARGUMENTS["add_activity"], "activity_id": 0}, "validation"),
        ("add_activity", {**ARGUMENTS["add_activity"], "energy_kcal": 12.5}, "validation"),
        ("set_water", {"date": DAY, "water_ml": True}, "validation"),
    ],
)
def test_bad_calls_are_validation_errors_and_send_nothing(
    tools: FitatuTools, fake: FakeFitatuServer, name: str, arguments: Any, expected: str
) -> None:
    assert error_type(tools, name, arguments) == expected
    assert fake.requests == []


def test_remove_items_called_directly_never_takes_the_whole_day(tools: FitatuTools, fake: FakeFitatuServer) -> None:
    for row_ids in (None, []):
        with pytest.raises(FitatuValidationError, match="at least one row_id"):
            tools.remove_items(DAY, row_ids)  # type: ignore[arg-type]
    assert fake.requests == []


def test_whole_number_floats_count_as_integers(tools: FitatuTools, fake: FakeFitatuServer) -> None:
    # JSON Schema accepts 1500.0 for an integer, and json.loads gives a float for it.
    assert data(tools, "set_water", date=DAY, water_ml=1500.0) == {"date": DAY, "water_ml": 1500}
    assert type(fake.requests[-1]["json_data"]["waterConsumption"]) is int
    assert data(tools, "add_activity", **{**ARGUMENTS["add_activity"], "activity_id": 535.0, "minutes": 30.0})["ok"] is True


def test_update_and_move_say_an_activity_cannot_be_changed(tools: FitatuTools, fake: FakeFitatuServer) -> None:
    for name, arguments in (("update_item", {"quantity": 45}), ("move_item", {"to_meal": "dinner"})):
        error = tools.call(name, {"date": DAY, "row_id": ACTIVITY_ROW, **arguments})["error"]
        assert error["error_type"] == "not_found" and "remove_activity" in error["message"], name
    assert fake.sync_payloads == []


@pytest.mark.parametrize("row_id", ["not-on-this-day", str(PRODUCT_ID)])
def test_a_row_is_named_by_its_row_id_only(tools: FitatuTools, fake: FakeFitatuServer, row_id: str) -> None:
    assert error_type(tools, "update_item", {"date": DAY, "row_id": row_id, "eaten": True}) == "not_found"
    assert error_type(tools, "move_item", {"date": DAY, "row_id": row_id, "to_meal": "dinner"}) == "not_found"
    assert fake.sync_payloads == []


def test_a_rejected_sync_is_reported(tools: FitatuTools, fake: FakeFitatuServer, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(fake, "_sync", lambda payload: recorded("sync_rejected"))
    assert error_type(tools, "add_custom_item", ARGUMENTS["add_custom_item"]) == "sync_rejected"


def test_call_reports_an_unexpected_error_instead_of_raising(tools: FitatuTools) -> None:
    tools.client.request = lambda *args, **kwargs: 1 / 0  # type: ignore[method-assign]
    assert error_type(tools, "get_day", {"date": DAY}) == "unexpected"


def test_a_session_without_user_id_is_an_auth_error() -> None:
    tools = FitatuTools(FitatuApiClient(FitatuAuthContext(bearer_token="t")))
    assert error_type(tools, "get_day", {"date": DAY}) == "auth"


def test_from_session_and_context_manager(tmp_path: Path) -> None:
    path = tmp_path / "session.json"
    path.write_text(json.dumps({"bearer_token": "t", "fitatu_user_id": RECORDED_USER_ID}), encoding="utf-8")
    with FitatuTools.from_session(path) as tools:
        assert tools.client.auth.fitatu_user_id == RECORDED_USER_ID
        assert tools.client.token_store is not None and tools.client.token_store.path == path
