"""FitatuTools: every tool through call() against a real client wired to the fake server."""

from __future__ import annotations

import inspect
import json
import logging
import re
import time
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any
from unittest.mock import patch
from zoneinfo import ZoneInfo

import pytest
from fake_server import RECORDED_USER_ID, FakeFitatuServer, recorded

from fitatu_api import (
    FitatuApiClient,
    FitatuApiError,
    FitatuAuthContext,
    FitatuAuthError,
    FitatuConnectionError,
    FitatuMaintenanceError,
    FitatuNotFoundError,
    FitatuRateLimitError,
    FitatuTools,
    FitatuValidationError,
    describe_error,
)
from fitatu_api.agent import (
    _PRODUCT_FORM,
    READ_ONLY_TOOLS,
    SYSTEM_PROMPT,
    TOOLS,
    anthropic_tools,
    openai_tools,
)

DAY = "2027-01-03"  # the recorded day
WEIGHT_DAY = "2020-01-01"  # the recorded measurement date
PRODUCT_ID = recorded("product_details")["id"]  # the first catalog hit the recorder logged
RECIPE_ID = 590200  # fixed in the recorder
BARCODE = "5901234123457"  # an example EAN-13; the fake gives it to the first 'jajko kurze' hit
MY_RECIPE_ID = 98765432  # a private recipe of the session's user, as the fake serves it
MY_PRODUCT_ID = 98765433  # a private product of the session's user, as the fake serves it
GRAM_BASE = {"measureKey": "GRAM", "measureUnit": "g", "weight": 100}  # the first measure: the values stay per 100 g

# The recorded day with a product (breakfast), a recipe (lunch), a custom row (snack) and an activity.
SEED = {**recorded("day_three_rows"), "activityPlan": recorded("day_after_activity")["activityPlan"]}
ROWS = {meal: bucket["items"][0]["planDayDietItemId"] for meal, bucket in SEED["dietPlan"].items() if bucket["items"]}
ACTIVITY_ROW = SEED["activityPlan"][0]["uuid"]
NEXT_DAY = "2027-01-04"
TODAY = datetime.now(ZoneInfo("Europe/Warsaw")).date().isoformat()  # get_today's date for the fixture's client

# Checked live on 2026-10-14, not recorded yet (tests/fixtures/README.md). One nutrient day by day: every day
# of the range is a key, null when nothing is logged; here DAY has 200 kcal eaten and 100 kcal planned.
BY_NUTRIENT: dict[str, dict[str, Any]] = {
    "energy": {"measures": {DAY: 300, NEXT_DAY: None}, "targets": {DAY: 2000, NEXT_DAY: 2000}},
    **{
        nutrient: {"measures": {DAY: logged, NEXT_DAY: None}, "targetRanges": dict.fromkeys((DAY, NEXT_DAY), {"min": low, "max": high})}
        for nutrient, logged, low, high in (("protein", 12, 90, 110), ("fat", 6, 60, 74), ("carbohydrate", 30, 225, 275))
    },
}
# The range summary of those two days: current counts every row, eaten only the rows ticked as eaten,
# min and max are the goals summed over the days.
RANGE_SUMMARY = {
    "energy": {"current": 300, "eaten": 200, "min": None, "max": 4000},
    "protein": {"current": 12, "eaten": 10, "min": 180, "max": 220},
    "fat": {"current": 6, "eaten": 5, "min": 120, "max": 148},
    "carbohydrate": {"current": 30, "eaten": 20, "min": 450, "max": 550},
}
# Checked live on 2026-10-15 with an account without Premium, which gets 403 from the macros' day-by-day
# routes: the range summary of one day has that day's goals as min and max (a range for a macro, max only
# for energy); current and eaten are null when nothing is logged or ticked. The days of BY_NUTRIENT again.
DAY_SUMMARIES = {
    DAY: {
        "energy": {"current": 300, "eaten": 200, "min": None, "max": 2000},
        "protein": {"current": 12, "eaten": 10, "min": 90, "max": 110},
        "fat": {"current": 6, "eaten": 5, "min": 60, "max": 74},
        "carbohydrate": {"current": 30, "eaten": 20, "min": 225, "max": 275},
    },
    NEXT_DAY: {
        nutrient: {"current": None, "eaten": None, "min": low, "max": high}
        for nutrient, low, high in (("energy", None, 2000), ("protein", 90, 110), ("fat", 60, 74), ("carbohydrate", 225, 275))
    },
}

# Checked live on 2026-10-15 (the flag names only): a Premium account's accessControl in GET /users/{id} has the
# recipe catalog's filter and premium flags, a free one's lacks them. The reply carries the e-mail and name too.
FREE_FLAGS = ["ROLE_USER", "ROLE_FEATURE_RECIPE_DETAILS", "ROLE_FEATURE_SEARCH_FOOD"]
PREMIUM_FLAGS = [*FREE_FLAGS, "ROLE_FEATURE_PREMIUM_RECIPES", "ROLE_FEATURE_RECIPES_CATALOG_FILTERS", "ROLE_FEATURE_RECIPES_CATALOG_PREMIUM"]


def profile(flags: list[str]) -> dict[str, Any]:
    return {**recorded("user_info"), "email": "someone@example.com", "nickname": "Someone", "accessControl": flags}


def settings_after(diet: dict[str, Any] | None = None, glass: int | None = None) -> dict[str, Any]:
    """settings-new as the PATCH answers it: the whole new settings, as a GET then gives them (checked live
    2026-10-15, not recorded): the recorded ones with ``diet`` and the glass changed."""
    settings = recorded("settings_new")
    settings["userDietSettings"] |= diet or {}
    if glass is not None:
        settings["waterSettings"] = {"unitCapacity": glass}
    return settings


MANUAL_1800 = {
    "manualEnergyTarget": True,
    "energy": 1800,
    **{f"{macro}Percentage": pct for macro, pct in (("protein", 40), ("fat", 30), ("carbohydrate", 30))},
    **{f"{macro}Weight": grams for macro, grams in (("protein", 180), ("fat", 60), ("carbohydrate", 135))},
}


def range_summary(params: dict[str, Any]) -> dict[str, Any]:
    """GET .../summary/custom by its dates: one day's summary (any day but DAY as empty as NEXT_DAY), else RANGE_SUMMARY."""
    one_day = DAY_SUMMARIES.get(params["fromDate"], DAY_SUMMARIES[NEXT_DAY]) if params["fromDate"] == params["toDate"] else RANGE_SUMMARY
    return {**recorded("summary_nutrition"), **one_day}

# Every tool with arguments that work against the fake.
ARGUMENTS: dict[str, dict[str, Any]] = {
    "get_today": {},
    "get_day": {"date": DAY},
    "get_targets": {"date": DAY},
    "get_profile": {},
    "get_range_summary": {"from_date": DAY, "to_date": DAY},
    "get_progress": {"from_date": DAY, "to_date": NEXT_DAY},
    "get_top_foods": {"from_date": DAY, "to_date": DAY, "nutrient": "protein"},
    "get_report": {"from_date": DAY, "to_date": NEXT_DAY},
    "get_shopping_list": {"from_date": DAY, "to_date": NEXT_DAY},
    "search_food": {"phrase": "jajko kurze", "limit": 2},
    "search_recipes": {"phrase": "kurczak", "max_energy_kcal": 500},
    "get_recipe": {"recipe_id": RECIPE_ID},
    "find_by_barcode": {"barcode": BARCODE},
    "get_food_measures": {"food_id": PRODUCT_ID, "food_type": "PRODUCT"},
    "get_recent_foods": {"meal": "breakfast"},
    "get_favorite_foods": {},
    "get_my_foods": {"phrase": "owsianka"},
    "get_weight_history": {"from_date": WEIGHT_DAY, "to_date": WEIGHT_DAY},
    "get_body_measurements": {"from_date": WEIGHT_DAY, "to_date": WEIGHT_DAY},
    "get_measurement_history": {"body_part": "waist", "from_date": WEIGHT_DAY, "to_date": WEIGHT_DAY},
    "get_water": {"date": DAY},
    "search_activity": {"phrase": "bieganie"},
    "get_fasting": {"from_date": DAY, "to_date": DAY},
    "get_habits": {},
    "get_good_days": {"from_date": DAY},
    "preview_removal": {"date": DAY, "row_ids": [ROWS["lunch"]]},
    "add_food": {"date": DAY, "meal": "dinner", "food_id": PRODUCT_ID, "food_type": "PRODUCT", "quantity": 70, "unit": "g"},
    "add_foods": {
        "date": DAY,
        "meal": "dinner",
        "items": [
            {"food_id": PRODUCT_ID, "food_type": "PRODUCT", "quantity": 70, "unit": "g"},
            {"food_id": RECIPE_ID, "food_type": "RECIPE", "quantity": 1.5},
        ],
    },
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
    "copy_meal": {"from_date": DAY, "from_meal": "lunch", "to_date": NEXT_DAY, "to_meal": "dinner"},
    "remove_items": {"date": DAY, "row_ids": [ROWS["lunch"]]},
    "log_weight": {"date": WEIGHT_DAY, "weight_kg": 70},
    "log_measurements": {"date": WEIGHT_DAY, "waist": 79.5},
    "set_water": {"date": DAY, "water_ml": 1500},
    "set_targets": {"energy_kcal": 1800, "protein_pct": 40, "fat_pct": 30, "carbohydrate_pct": 30},
    "set_note": {"date": DAY, "note": "Dużo wody"},
    "add_activity": {"date": DAY, "activity_id": 535, "minutes": 30, "energy_kcal": 123},
    "remove_activity": {"date": DAY, "row_id": ACTIVITY_ROW},
    "set_favorite": {"food_id": PRODUCT_ID, "food_type": "PRODUCT"},
    "create_recipe": {
        "name": "Owsianka z jajkiem",
        "ingredients": [{"food_id": PRODUCT_ID, "food_type": "PRODUCT", "quantity": 70, "unit": "g"}],
        "servings": 2,
    },
    "update_recipe": {"recipe_id": MY_RECIPE_ID, "name": "Owsianka z kakao", "servings": 2},
    "delete_recipe": {"recipe_id": MY_RECIPE_ID},
    "create_food": {
        "name": "Baton owsiany", "energy_kcal": 420, "protein_g": 9, "fat_g": 18, "carbohydrate_g": 55, "servings": [{"name": "bar", "weight": 45}]
    },
    "update_food": {"food_id": MY_PRODUCT_ID, "energy_kcal": 410},
    "delete_food": {"food_id": MY_PRODUCT_ID},
}
READS = {"get_today", "get_day", "get_targets", "get_range_summary", "get_progress", "search_food", "find_by_barcode", "get_food_measures"}
READS |= {"get_recent_foods", "get_top_foods", "get_report", "get_shopping_list"}
READS |= {"get_favorite_foods", "get_my_foods", "get_weight_history", "get_body_measurements", "get_water", "search_activity"}
READS |= {"preview_removal", "search_recipes", "get_fasting", "get_habits", "get_good_days"}
READS |= {"get_profile", "get_measurement_history", "get_recipe"}


def fake_with_every_reply() -> FakeFitatuServer:
    """The fake with replies for every tool in ARGUMENTS (tests/test_mcp_server.py uses it too)."""
    server = FakeFitatuServer(SEED)
    # Not in the recording. Recent and favourite foods are lists of food search hits in the
    # app; the water reply has the shape the live API sends.
    server.routes[("GET", f"/search/food/{RECORDED_USER_ID}/last-used")] = recorded("search_food")
    server.routes[("GET", f"/users/{RECORDED_USER_ID}/favorite/foods")] = recorded("search_food_recipe")
    server.routes[("GET", f"/water/{RECORDED_USER_ID}/{DAY}")] = {
        "water": {"waterConsumption": 750, "target": 2000},
        "settings": {"waterUnitCapacity": 250},
    }
    server.routes[("PUT", f"/water/{RECORDED_USER_ID}/{DAY}")] = None
    server.routes[("PUT", f"/users/{RECORDED_USER_ID}/favorite/food/PRODUCT/{PRODUCT_ID}")] = None  # 202, no body
    for nutrient, reply in BY_NUTRIENT.items():  # energy is recorded too, but for DAY only
        server.routes[("GET", f"/v2/diet-plan/{RECORDED_USER_ID}/summary/{nutrient}/custom")] = reply
    # Checked live on 2026-10-14, not recorded: a barcode as the search phrase finds its product first,
    # and the product's details carry the barcode.
    egg = recorded("search_food")[0]
    server.search_results[BARCODE] = recorded("search_food")
    server.routes[("GET", f"/products/{egg['foodId']}")] = {
        **recorded("product_details"), "id": egg["foodId"], "name": egg["name"], "barcode": BARCODE, "hasBarcodes": True
    }
    # Checked live on 2026-10-14, not recorded: the top foods for protein, in the shape of the recorded energy reply.
    server.routes[("GET", f"/diet-plan/{RECORDED_USER_ID}/summary-product/protein/custom")] = {
        "list": [
            {"id": 12178460704, "name": "fitatu-api fixture", "amount": {"current": "10.00", "per100": None}},
            {"id": 1431, "name": "Mleko spożywcze 1.5 % tłuszczu", "amount": {"current": "3.68", "per100": "3.20"}},
        ],
        "count": 2,
    }
    # Checked live on 2026-10-14, not recorded: a private recipe is created with shared false and
    # read back with private true, shared false and the owner's userId; DELETE answers 200 [].
    server.routes[("GET", f"/search/food/user/{RECORDED_USER_ID}")] = recorded("search_food_recipe")
    server.routes[("POST", "/recipes")] = {"id": MY_RECIPE_ID, "name": "Owsianka z jajkiem", "energy": 33.6, "protein": 7.63, "fat": 0.14, "carbohydrate": 0.49}
    server.routes[("GET", f"/recipes/{MY_RECIPE_ID}")] = {
        **recorded("recipe_details"), "id": MY_RECIPE_ID, "name": "Owsianka z jajkiem",
        "private": True, "shared": False, "userId": int(RECORDED_USER_ID), "deleted": False,
    }
    server.routes[("DELETE", f"/recipes/{MY_RECIPE_ID}")] = []
    # The app's route and answer (UpdatedRecipeDTO), not checked live.
    server.routes[("PUT", f"/recipes/{MY_RECIPE_ID}")] = {"id": MY_RECIPE_ID, "name": "Owsianka z kakao", "energy": 62.96}
    # Checked live on 2026-10-15, not recorded: a product is created private (only its owner finds it) and read back
    # with private true, visible false and the owner's userId; a PUT answers whether Fitatu saved the change as a new
    # product, a DELETE {"deleted": true}.
    server.routes[("POST", "/products")] = {"id": MY_PRODUCT_ID, "name": "Baton owsiany"}
    server.routes[("GET", f"/products/{MY_PRODUCT_ID}")] = {
        **recorded("product_details"), "id": MY_PRODUCT_ID, "name": "Baton owsiany", "brand": None,
        "private": True, "visible": False, "verified": False, "editable": True, "userId": int(RECORDED_USER_ID), "deleted": False,
        "energy": 420, "protein": 9, "fat": 18, "carbohydrate": 55, "sugars": 20, "fiber": 6, "salt": 0.2, "saturatedFat": 2, "cholesterol": 5,
        "measures": [{"id": 54, "name": "bar", "energyPerUnit": 189, "weightPerUnit": 45}, {"id": 1, "name": "g", "energyPerUnit": 4.2, "weightPerUnit": 1}],
    }
    server.routes[("PUT", f"/products/{MY_PRODUCT_ID}")] = {"id": MY_PRODUCT_ID, "name": "Baton owsiany", "newProductCreated": False}
    server.routes[("DELETE", f"/products/{MY_PRODUCT_ID}")] = {"deleted": True}
    # Checked live on 2026-10-14, not recorded: recipe catalog hits in the food search's shape, one portion each.
    server.routes[("GET", "/search/catalog/recipes")] = [
        {**hit, "foodId": food_id, "name": name, "energy": kcal, "premiumInRecipesCatalog": premium, "rating": 4.5, "cookingTime": 20, "preparationTime": 10}
        for hit in [recorded("search_food_recipe")[0]]
        for food_id, name, kcal, premium in ((7000001, "Sałatka z kurczakiem", 420.5, False), (7000002, "Kurczak z ryżem", 480, True))
    ]
    # Checked live on 2026-10-14, not recorded (shapes only; this account fasts on no day and has no habit):
    # the fasting summary per day, the habit catalog, the user's habits and the good days.
    server.routes[("GET", f"/intermittent-fasting/{RECORDED_USER_ID}/summary")] = [
        {"date": DAY, "eatingPeriods": [{"date": DAY, "from": "09:00", "to": "17:30"}], "expectedEatingPeriods": [{"date": DAY, "from": "10:00", "to": "18:00"}]}
    ]
    water, veg = ({"id": 1, "name": "Pij wodę", "translationKey": "habit_water", "length": 21, "premium": False},
                  {"id": 2, "name": "Jedz warzywa", "translationKey": "habit_vegetables", "length": 30, "premium": True})
    server.routes[("GET", f"/habits/{RECORDED_USER_ID}")] = [water, veg]
    server.routes[("GET", f"/users/{RECORDED_USER_ID}/habits")] = [
        {"completed": False, "enrolledDate": "2027-01-01", "habit": water, "habitItems": [{"dayNumber": 3, "date": DAY}, {"dayNumber": 1, "date": "2027-01-01"}]}
    ]
    server.routes[("GET", f"/habits/{RECORDED_USER_ID}/good-days")] = ["2026-12-30", DAY, "2027-01-01"]
    # set_targets reads today's settings and PATCHes them; the reply is the whole new settings. get_profile
    # reads them too, and whether the account has Premium.
    server.routes[("GET", f"/users/{RECORDED_USER_ID}/settings-new/{TODAY}")] = recorded("settings_new")
    server.routes[("GET", f"/users/{RECORDED_USER_ID}/subscriptions")] = {"subscriptions": []}
    server.routes[("PATCH", f"/users/{RECORDED_USER_ID}/settings-new")] = settings_after(MANUAL_1800)
    return server


@pytest.fixture()
def fake() -> FakeFitatuServer:
    return fake_with_every_reply()


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
        assert set(tool) == {"name", "title", "description", "input_schema", "annotations"}
        assert tool["description"].strip() and 0 < len(tool["title"]) <= 30 and "_" not in tool["title"]
        schema = tool["input_schema"]
        assert schema["type"] == "object" and isinstance(schema["properties"], dict)
        assert set(schema["required"]) <= set(schema["properties"])
        for prop in schema["properties"].values():
            assert prop["type"] in {"string", "integer", "number", "boolean", "array"} and prop["description"]
        assert {key: type(value) for key, value in tool["annotations"].items()} == {
            "readOnlyHint": bool,
            "destructiveHint": bool,
            "idempotentHint": bool,
            "openWorldHint": bool,
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
    assert {name for name, hint in hints.items() if hint["readOnlyHint"]} == READS == set(READ_ONLY_TOOLS)
    assert {name for name, hint in hints.items() if hint["destructiveHint"]} == {
        "update_recipe",
        "delete_recipe",
        "update_food",
        "delete_food",
        "update_item",
        "move_item",
        "remove_items",
        "remove_activity",
        "log_weight",
        "log_measurements",
        "set_water",
        "set_targets",
        "set_note",
    }
    assert {name for name, hint in hints.items() if hint["idempotentHint"]} == READS | {
        "update_recipe",
        "delete_recipe",
        "update_food",
        "delete_food",
        "remove_items",
        "remove_activity",
        "log_weight",
        "log_measurements",
        "set_water",
        "set_targets",
        "set_note",
        "set_favorite",
    }


def test_only_the_shared_catalog_tools_are_open_world() -> None:
    open_world = {tool["name"] for tool in TOOLS if tool["annotations"]["openWorldHint"]}
    assert open_world == {"search_food", "search_recipes", "get_recipe", "find_by_barcode", "get_food_measures", "search_activity"}


def test_provider_formats_carry_the_same_tools() -> None:
    names = [tool["name"] for tool in TOOLS]
    assert [tool["name"] for tool in anthropic_tools()] == names
    assert all(set(tool) == {"name", "description", "input_schema"} for tool in anthropic_tools())
    assert [tool["function"]["name"] for tool in openai_tools()] == names
    # no title: both APIs reject keys they do not know
    assert all(tool["type"] == "function" and set(tool["function"]) == {"name", "description", "parameters"} for tool in openai_tools())


def test_provider_formats_carry_only_the_tools_named() -> None:
    assert [tool["name"] for tool in anthropic_tools(READ_ONLY_TOOLS)] == list(READ_ONLY_TOOLS)
    assert [tool["function"]["name"] for tool in openai_tools({"remove_items", "get_day"})] == ["get_day", "remove_items"]  # TOOLS order
    with pytest.raises(FitatuValidationError, match="unknown tool.*get_week"):
        anthropic_tools(["get_day", "get_week"])


def test_an_allowlist_limits_what_call_runs(fake: FakeFitatuServer) -> None:
    client = FitatuApiClient(FitatuAuthContext(bearer_token="t", fitatu_user_id=RECORDED_USER_ID))
    client.request = fake.request  # type: ignore[method-assign]
    tools = FitatuTools(client, tools=READ_ONLY_TOOLS)
    assert data(tools, "get_day", date=DAY)["meals"]
    for name in ("remove_items", "get_session_tokens"):
        error = tools.call(name, ARGUMENTS.get(name, {}))["error"]
        # the message names only what this instance offers
        assert error["error_type"] == "validation" and "get_day" in error["message"] and "add_food" not in error["message"], name
    assert fake.sync_payloads == []
    with pytest.raises(FitatuValidationError, match="unknown tool.*get_week"):
        FitatuTools(client, tools=["get_week"])


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
    next(tool for tool in anthropic_tools() if tool["name"] == "get_day")["input_schema"]["required"].clear()
    assert data(tools, "search_food", phrase="owsianka") and error_type(tools, "get_day", {}) == "validation"
    assert [tool["function"]["parameters"]["required"] for tool in openai_tools() if tool["function"]["name"] == "search_food"] == [["phrase"]]


def test_the_system_prompt_names_only_tools_that_exist() -> None:
    named = set(re.findall(r"\b(?:get|search|find|add|update|move|copy|remove|log|set|create|delete)_\w+", SYSTEM_PROMPT))
    assert named and named <= {tool["name"] for tool in TOOLS}
    destructive = {tool["name"] for tool in TOOLS if tool["annotations"]["destructiveHint"]}
    assert destructive <= named and "{" not in SYSTEM_PROMPT


def test_the_system_prompt_fits_the_hosts_limit_and_names_its_domain_first() -> None:
    assert len(SYSTEM_PROMPT) < 4000  # Claude Code cuts server instructions at 4096 characters
    first = SYSTEM_PROMPT.splitlines()[0]
    assert "Fitatu calorie counter" in first and "licznik kalorii" in first


def test_every_integer_with_a_limit_is_checked_and_every_list_limit_has_a_maximum(tools: FitatuTools) -> None:
    for tool in TOOLS:
        limit = tool["input_schema"]["properties"].get("limit")
        assert limit is None or limit["maximum"] <= 50, tool["name"]
    error = tools.call("get_top_foods", {"from_date": DAY, "to_date": DAY, "limit": 500})["error"]
    assert error["message"] == "limit must be at most 50, got 500"
    error = tools.call("get_progress", {"from_date": "2026-01-01", "to_date": "2026-12-31"})["error"]
    assert "at most 62 days, got 365" in error["message"] and "get_report" in error["message"] and "get_range_summary" in error["message"]


def test_arguments_cover_every_tool() -> None:
    assert set(ARGUMENTS) == {tool["name"] for tool in TOOLS}


@pytest.mark.parametrize("name", list(ARGUMENTS))
def test_tool_returns_json_safe_data(tools: FitatuTools, name: str) -> None:
    result = data(tools, name, **ARGUMENTS[name])
    assert not isinstance(result, dict) or result.get("ok", True) is True
    assert name in READS or result["ok"] is True  # every write says data.ok, which SYSTEM_PROMPT tells the model to check


def test_a_call_out_of_time_sends_nothing_and_an_add_can_be_repeated_safely() -> None:
    client = FitatuApiClient(FitatuAuthContext(bearer_token="t", fitatu_user_id=RECORDED_USER_ID))
    tools = FitatuTools(client, call_timeout=0)
    with patch("fitatu_api.client.requests.Session.request") as http:
        read = tools.call("get_day", {"date": DAY})["error"]
        add = tools.call("add_custom_item", ARGUMENTS["add_custom_item"])["error"]
    http.assert_not_called()
    assert (read["error_type"], read["retryable"]) == ("network", True) and "time limit" in read["message"]
    assert (add["error_type"], add["retryable"]) == ("network", False) and add["retry_row_id"] in add["hint"]
    assert FitatuTools(client).call_timeout == 45


# -- reads --------------------------------------------------------------------------------


@pytest.mark.parametrize("timezone", ["Pacific/Kiritimati", "Pacific/Pago_Pago"])  # UTC+14 and UTC-11
def test_get_today_is_the_date_in_the_account_time_zone(tools: FitatuTools, fake: FakeFitatuServer, timezone: str) -> None:
    tools.client.auth.app_timezone = timezone
    now = datetime.now(ZoneInfo(timezone))
    assert data(tools, "get_today") == {"date": now.date().isoformat(), "timezone": timezone, "weekday": now.strftime("%A")}
    assert fake.requests == []


@pytest.mark.parametrize("timezone", ["Nowhere/Atlantis", "", None])
def test_get_today_falls_back_to_the_host_date(tools: FitatuTools, timezone: Any) -> None:
    tools.client.auth.app_timezone = timezone
    now = datetime.now().astimezone()
    assert data(tools, "get_today") == {"date": now.date().isoformat(), "timezone": str(now.tzinfo), "weekday": now.strftime("%A")}


def test_get_day_summarises_the_recorded_day(tools: FitatuTools) -> None:
    day = data(tools, "get_day", date=DAY)
    assert day["date"] == DAY and RECORDED_USER_ID not in json.dumps(day)  # the account id stays out of the model's context
    rows = {item["row_id"]: (meal["meal_key"], item["food_type"]) for meal in day["meals"] for item in meal["items"]}
    assert sorted(rows.values()) == [("breakfast", "PRODUCT"), ("lunch", "RECIPE"), ("snack", "CUSTOM_ITEM")]
    assert (day["note"], day["tags_ids"]) == (None, [])


def _renamed_meals_tools(*, without: str | None = None) -> FitatuTools:
    """Tools over the recorded day whose user calls the dinner slot "Obiad" and the snack slot "Kolacja"."""
    seed = json.loads(json.dumps(SEED))
    seed["dietPlan"]["dinner"]["mealName"], seed["dietPlan"]["snack"]["mealName"] = "Obiad", "Kolacja"
    seed["dietPlan"].pop(without, None)
    client = FitatuApiClient(FitatuAuthContext(bearer_token="t", fitatu_user_id=RECORDED_USER_ID))
    client.request = FakeFitatuServer(seed).request  # type: ignore[method-assign]
    return FitatuTools(client)


def test_meals_are_matched_by_the_name_the_user_gave_them() -> None:
    tools = _renamed_meals_tools()
    meals = {meal["meal_name"]: meal for meal in data(tools, "get_day", date=DAY)["meals"]}
    assert (meals["Obiad"]["meal_key"], meals["Kolacja"]["meal_key"], meals["Breakfast"]["meal_time"]) == ("dinner", "snack", "08:00")
    # "move what I logged for Kolacja to Obiad": the user's words are meal_names, the tools take meal_keys
    (snack,) = meals["Kolacja"]["items"]
    data(tools, "move_item", date=DAY, row_id=snack["row_id"], to_meal=meals["Obiad"]["meal_key"])
    after = {meal["meal_name"]: [item["name"] for item in meal["items"]] for meal in data(tools, "get_day", date=DAY)["meals"]}
    assert (after["Obiad"], after["Kolacja"]) == ([snack["name"]], [])


def test_get_day_rounds_quantities_and_nutrition_to_two_decimals(tools: FitatuTools) -> None:
    day = data(tools, "get_day", date=DAY)
    breakfast = next(meal for meal in day["meals"] if meal["meal_key"] == "breakfast")["items"][0]
    assert (breakfast["energy"], breakfast["fat"]) == (16.8, 0.07)  # recorded as 16.799999999999997 and 0.06999999999999999
    assert day["totals"]["energy"] == 392.71
    numbers = [value for item in [day["totals"], *(i for m in day["meals"] for i in [m["totals"], *m["items"]])]
               for value in item.values() if isinstance(value, float)]
    assert numbers and all(value == round(value, 2) for value in numbers)


def test_get_targets_and_range_summary(tools: FitatuTools) -> None:
    targets = data(tools, "get_targets", date=DAY)
    assert targets["energy"] == 2000 and targets["protein"] == {"grams": 100, "percent": 20}
    assert (targets["date"], targets["settings_since"]) == (DAY, recorded("settings_new")["date"])
    summary = data(tools, "get_range_summary", from_date=DAY, to_date=DAY)
    assert summary["nutrition"]["energy"] == 376 and summary["activity_energy"] == 0
    assert set(summary["nutrition"]) == set(summary["eaten_nutrition"]) == {"energy", "protein", "fat", "carbohydrate", "fiber", "sugars", "salt"}
    assert summary["eaten_nutrition"]["energy"] == 0  # recorded as null: no row was ticked as eaten


def test_progress_is_each_day_against_its_goals_with_the_eaten_totals(tools: FitatuTools, fake: FakeFitatuServer) -> None:
    fake.routes[("GET", f"/v2/diet-plan/{RECORDED_USER_ID}/summary/custom")] = {**recorded("summary_nutrition"), **RANGE_SUMMARY}
    progress = data(tools, "get_progress", from_date=DAY, to_date=NEXT_DAY)
    assert progress["days"] == [
        {
            "date": DAY,
            "energy": {"logged": 300, "target": 2000, "left": 1700},
            "protein": {"logged": 12, "target_min": 90, "target_max": 110, "left": 98},
            "fat": {"logged": 6, "target_min": 60, "target_max": 74, "left": 68},
            "carbohydrate": {"logged": 30, "target_min": 225, "target_max": 275, "left": 245},
        },
        {  # nothing logged: null, not 0, and the whole goal is left
            "date": NEXT_DAY,
            "energy": {"logged": None, "target": 2000, "left": 2000},
            "protein": {"logged": None, "target_min": 90, "target_max": 110, "left": 110},
            "fat": {"logged": None, "target_min": 60, "target_max": 74, "left": 74},
            "carbohydrate": {"logged": None, "target_min": 225, "target_max": 275, "left": 275},
        },
    ]
    assert progress["totals"] == {
        "energy": {"logged": 300, "eaten": 200, "target": 4000},
        "protein": {"logged": 12, "eaten": 10, "target_min": 180, "target_max": 220},
        "fat": {"logged": 6, "eaten": 5, "target_min": 120, "target_max": 148},
        "carbohydrate": {"logged": 30, "eaten": 20, "target_min": 450, "target_max": 550},
    }
    assert data(tools, "get_range_summary", from_date=DAY, to_date=NEXT_DAY)["eaten_nutrition"]["energy"] == 200
    # four nutrients day by day (a macro first, which an account without Premium may not read) and one range summary, whatever the range
    assert [r["path"].split("/")[-2] for r in fake.requests[:5]] == ["carbohydrate", "fat", "protein", "energy", "summary"]
    assert all(r["params"] == {"fromDate": DAY, "toDate": NEXT_DAY} for r in fake.requests[:5])


def test_top_foods_come_with_numbers_not_strings(tools: FitatuTools, fake: FakeFitatuServer) -> None:
    top = data(tools, "get_top_foods", from_date=DAY, to_date=DAY)
    assert (top["nutrient"], top["foods"][:2]) == ("energy", [  # recorded: a custom row, then a recipe's ingredient
        {"name": "fitatu-api fixture", "amount": 250, "per100": None},
        {"name": "Mleko spożywcze 1.5 % tłuszczu", "amount": 54.05, "per100": 47},
    ])
    assert fake.requests[-1]["params"] == {"fromDate": DAY, "toDate": DAY, "page": 1, "limit": 10}
    assert data(tools, "get_top_foods", **ARGUMENTS["get_top_foods"], limit=2)["foods"][1]["amount"] == 3.68
    assert fake.requests[-1]["path"].endswith("/summary-product/protein/custom") and fake.requests[-1]["params"]["limit"] == 2


def test_report_sums_up_a_range_in_eight_requests(tools: FitatuTools, fake: FakeFitatuServer) -> None:
    fake.routes[("GET", f"/v2/diet-plan/{RECORDED_USER_ID}/summary/custom")] = {**recorded("summary_nutrition"), **RANGE_SUMMARY}
    fake.routes[("GET", f"/users/{RECORDED_USER_ID}/measurements/summary/weight")] = [  # newest first
        {"date": "2027-01-05", "value": 79.2, "difference": -0.4, "direction": -1},  # past to_date: the server would not send it
        {"date": NEXT_DAY, "value": 79.6, "difference": -0.4, "direction": -1},
        {"date": DAY, "value": 80, "difference": None, "direction": -1},
    ]
    report = data(tools, "get_report", **ARGUMENTS["get_report"])
    # DAY has 300 kcal logged (200 eaten), NEXT_DAY nothing: averages over the one logged day, goals per day
    assert (report["days"], report["days_logged"]) == (2, 1)
    assert report["nutrition"] == {
        "energy": {"logged": 300, "eaten": 200, "daily": {"logged": 300, "target": 2000}},
        "protein": {"logged": 12, "eaten": 10, "daily": {"logged": 12, "target_min": 90, "target_max": 110}},
        "fat": {"logged": 6, "eaten": 5, "daily": {"logged": 6, "target_min": 60, "target_max": 74}},
        "carbohydrate": {"logged": 30, "eaten": 20, "daily": {"logged": 30, "target_min": 225, "target_max": 275}},
    }
    assert report["weight"] == {"start": {"date": DAY, "value": 80}, "end": {"date": NEXT_DAY, "value": 79.6}, "change": -0.4}
    assert [len(report["top_foods"][n]) for n in ("energy", "protein")] == [5, 2] and report["top_foods_note"] is None
    assert report["top_foods"]["protein"][0] == {"name": "fitatu-api fixture", "amount": 10, "per100": None}
    assert len(fake.requests) == 8
    weight, *top = fake.requests[5:]
    assert weight["params"] == {"fromDate": DAY, "toDate": NEXT_DAY, "page": 1, "limit": 41}
    assert [r["params"]["limit"] for r in top] == [5, 5]
    # no weigh-in in the range (the recorded one is from 2020)
    del fake.routes[("GET", f"/users/{RECORDED_USER_ID}/measurements/summary/weight")]
    assert data(tools, "get_report", **ARGUMENTS["get_report"])["weight"] is None


def test_report_reads_the_next_weight_page_while_a_page_is_full(tools: FitatuTools, fake: FakeFitatuServer) -> None:
    first = datetime.fromisoformat(DAY)
    full_page = [{"date": (first + timedelta(days=40 - n)).date().isoformat(), "value": 80 - n / 10} for n in range(41)]  # newest first
    fake.routes[("GET", f"/users/{RECORDED_USER_ID}/measurements/summary/weight")] = full_page
    weight = data(tools, "get_report", from_date=DAY, to_date="2027-03-05")["weight"]  # 62 days: two pages at most
    assert (weight["start"], weight["end"]["date"], weight["change"]) == ({"date": DAY, "value": 76}, "2027-02-12", 4)
    assert [r["params"]["page"] for r in fake.requests if r["path"].endswith("/summary/weight")] == [1, 2]


def test_progress_left_is_negative_over_the_goal(tools: FitatuTools, fake: FakeFitatuServer) -> None:
    fake.routes[("GET", f"/v2/diet-plan/{RECORDED_USER_ID}/summary/fat/custom")] = {
        "measures": {DAY: 80.4}, "targetRanges": {DAY: {"min": 60, "max": 74}}
    }
    (day,) = data(tools, "get_progress", from_date=DAY, to_date=DAY)["days"]
    assert day["fat"] == {"logged": 80.4, "target_min": 60, "target_max": 74, "left": -6.4}


def test_progress_without_premium_reads_each_day_and_remembers_it(tools: FitatuTools, fake: FakeFitatuServer) -> None:
    fake.free = True
    fake.routes[("GET", f"/v2/diet-plan/{RECORDED_USER_ID}/summary/custom")] = range_summary
    progress = data(tools, "get_progress", from_date=DAY, to_date=NEXT_DAY)
    # The same days as with Premium, each with its own eaten too.
    assert progress["days"] == [
        {
            "date": DAY,
            "energy": {"logged": 300, "eaten": 200, "target": 2000, "left": 1700},
            "protein": {"logged": 12, "eaten": 10, "target_min": 90, "target_max": 110, "left": 98},
            "fat": {"logged": 6, "eaten": 5, "target_min": 60, "target_max": 74, "left": 68},
            "carbohydrate": {"logged": 30, "eaten": 20, "target_min": 225, "target_max": 275, "left": 245},
        },
        {
            "date": NEXT_DAY,
            "energy": {"logged": None, "eaten": 0, "target": 2000, "left": 2000},
            "protein": {"logged": None, "eaten": 0, "target_min": 90, "target_max": 110, "left": 110},
            "fat": {"logged": None, "eaten": 0, "target_min": 60, "target_max": 74, "left": 74},
            "carbohydrate": {"logged": None, "eaten": 0, "target_min": 225, "target_max": 275, "left": 275},
        },
    ]
    assert progress["totals"]["protein"] == {"logged": 12, "eaten": 10, "target_min": 180, "target_max": 220}
    # One refused macro, then a summary per day and one for the range.
    assert [(r["path"].split("/")[-2], r.get("params")) for r in fake.requests] == [
        ("carbohydrate", {"fromDate": DAY, "toDate": NEXT_DAY}),
        ("summary", {"fromDate": DAY, "toDate": DAY}),
        ("summary", {"fromDate": NEXT_DAY, "toDate": NEXT_DAY}),
        ("summary", {"fromDate": DAY, "toDate": NEXT_DAY}),
    ]
    # Known from then on; one day is a single request, whose summary is the totals too.
    fake.requests.clear()
    progress = data(tools, "get_progress", from_date=DAY, to_date=DAY)
    assert progress["totals"]["energy"] == {"logged": 300, "eaten": 200, "target": 2000}
    assert [(r["path"].split("/")[-2], r.get("params")) for r in fake.requests] == [("summary", {"fromDate": DAY, "toDate": DAY})]


def test_progress_without_premium_covers_31_days(tools: FitatuTools, fake: FakeFitatuServer) -> None:
    fake.free = True
    fake.routes[("GET", f"/v2/diet-plan/{RECORDED_USER_ID}/summary/custom")] = range_summary
    month = data(tools, "get_progress", from_date="2027-01-01", to_date="2027-01-31")
    assert len(month["days"]) == 31 and month["days"][2]["energy"]["logged"] == 300
    assert len(fake.requests) == 1 + 31 + 1
    fake.requests.clear()
    error = tools.call("get_progress", {"from_date": "2027-01-01", "to_date": "2027-02-01"})["error"]
    assert error["error_type"] == "validation" and "at most 31 days, got 32" in error["message"] and "get_range_summary" in error["message"]
    assert fake.requests == []


def test_report_without_premium_has_progress_and_no_top_foods(tools: FitatuTools, fake: FakeFitatuServer) -> None:
    fake.free = True
    fake.routes[("GET", f"/v2/diet-plan/{RECORDED_USER_ID}/summary/custom")] = range_summary
    report = data(tools, "get_report", **ARGUMENTS["get_report"])
    assert report["nutrition"]["protein"] == {"logged": 12, "eaten": 10, "daily": {"logged": 12, "target_min": 90, "target_max": 110}}
    assert report["top_foods"] is None and "Premium" in report["top_foods_note"]
    assert not [r for r in fake.requests if "summary-product" in r["path"]]
    # get_top_foods itself is refused, with the hint that the account has no premium plan.
    fake.routes[("GET", f"/users/{RECORDED_USER_ID}/subscriptions")] = {"subscriptions": []}
    error = tools.call("get_top_foods", ARGUMENTS["get_top_foods"])["error"]
    assert (error["error_type"], error["status_code"]) == ("forbidden", 403) and error["hint"].startswith("A premium feature")


def test_a_refusal_for_want_of_premium_is_not_trusted_for_good(tools: FitatuTools, fake: FakeFitatuServer) -> None:
    # The MCP server keeps one FitatuTools for its whole life, and the user may buy Premium meanwhile.
    fake.free = True
    fake.routes[("GET", f"/v2/diet-plan/{RECORDED_USER_ID}/summary/custom")] = range_summary
    assert data(tools, "get_report", **ARGUMENTS["get_report"])["top_foods"] is None
    fake.free = False
    data(tools, "get_top_foods", **ARGUMENTS["get_top_foods"])  # a Premium route answers: the refusal is forgotten
    report = data(tools, "get_report", **ARGUMENTS["get_report"])
    assert report["top_foods"] is not None and report["top_foods_note"] is None
    # Otherwise it holds for 10 minutes, then the Premium route is asked again.
    fake.free = True
    data(tools, "get_progress", from_date=DAY, to_date=DAY)
    fake.free = False
    fake.requests.clear()
    data(tools, "get_progress", from_date=DAY, to_date=DAY)
    assert [r["path"].split("/")[-2] for r in fake.requests] == ["summary"]
    with patch("fitatu_api.agent.time.monotonic", return_value=time.monotonic() + 601):
        data(tools, "get_progress", from_date=DAY, to_date=DAY)
    assert fake.requests[1]["path"].endswith("/summary/carbohydrate/custom")
    # A 403 on an account with an active plan is not about Premium either.
    tools._without_premium = True
    fake.free = True
    fake.routes[("GET", f"/users/{RECORDED_USER_ID}/subscriptions")] = {"subscriptions": [{"startDate": "2020-01-01", "endDate": None}]}
    assert tools.call("get_top_foods", ARGUMENTS["get_top_foods"])["error"]["hint"].startswith("Not a premium matter")
    assert tools._without_premium is False


def test_a_firewall_403_is_not_taken_for_an_account_without_premium(tools: FitatuTools, fake: FakeFitatuServer) -> None:
    blocked = FitatuApiError("GET -> 403", status_code=403, body="<html>blocked</html>")
    with patch.object(tools.client.summary, "get_nutrient_by_day", side_effect=blocked):
        error = tools.call("get_progress", {"from_date": DAY, "to_date": DAY})["error"]
    assert error["hint"].startswith("A firewall") and tools._without_premium is False


def test_shopping_list_sums_planned_products_and_the_ingredients_of_planned_recipes(tools: FitatuTools, fake: FakeFitatuServer) -> None:
    # Both days hold the recorded rows: a product (35 g), a recipe of 1 portion and a custom row, none eaten.
    data(tools, "update_item", date=NEXT_DAY, row_id=ROWS["breakfast"], eaten=True)  # an eaten row is not shopped for
    shopping = data(tools, "get_shopping_list", from_date=DAY, to_date=NEXT_DAY)
    products = {entry["name"]: entry for entry in shopping["products"]}
    assert products["Białko jaja kurzego"] == {"product_id": 15956231, "name": "Białko jaja kurzego", "measure_name": "portion", "quantity": 1, "weight_g": 35}
    # the recipe's ingredients twice: 0.5 glass (230 g a glass) of milk per portion
    assert products["Mleko spożywcze 1.5 % tłuszczu"] == {"product_id": 1431, "name": "Mleko spożywcze 1.5 % tłuszczu", "measure_name": "glass", "quantity": 1, "weight_g": 230}
    assert len(products) == 6 and [entry["name"] for entry in shopping["products"]] == sorted(products)
    assert [(r["date"], r["recipe_id"], r["portions"], r["expanded"]) for r in shopping["recipes"]] == [(DAY, RECIPE_ID, 1, True), (NEXT_DAY, RECIPE_ID, 1, True)]
    assert [(row["date"], row["name"], row["food_type"]) for row in shopping["other_items"]] == [
        (DAY, "fitatu-api fixture", "CUSTOM_ITEM"), (NEXT_DAY, "fitatu-api fixture", "CUSTOM_ITEM")
    ]
    assert [r["path"] for r in fake.requests].count(f"/recipes/{RECIPE_ID}") == 1  # each recipe is read once
    error = tools.call("get_shopping_list", {"from_date": DAY, "to_date": "2027-01-31"})["error"]
    assert error["error_type"] == "validation" and "at most 14 days" in error["message"]


def test_a_recipe_the_shopping_list_cannot_read_is_listed_unexpanded(tools: FitatuTools, fake: FakeFitatuServer) -> None:
    with patch.object(tools.client, "get_recipe", side_effect=FitatuApiError("GET /recipes -> 403", status_code=403)):
        shopping = data(tools, "get_shopping_list", from_date=DAY, to_date=DAY)
    assert [(r["recipe_id"], r["expanded"]) for r in shopping["recipes"]] == [(RECIPE_ID, False)]
    assert [entry["name"] for entry in shopping["products"]] == ["Białko jaja kurzego"]
    with patch.object(tools.client, "get_recipe", side_effect=FitatuApiError("GET /recipes -> 500", status_code=500)):
        assert error_type(tools, "get_shopping_list", {"from_date": DAY, "to_date": DAY}) == "server"


def test_food_search_hits_are_trimmed_to_what_add_food_needs(tools: FitatuTools, fake: FakeFitatuServer) -> None:
    hits = data(tools, "search_food", phrase="owsianka")
    assert hits[0] == {
        "food_id": 60477557,
        "type": "RECIPE",
        "name": "Owsianka",
        "brand": None,
        "energy_kcal": 206.76,
        "measure": {"measure_id": 39, "name": "portion", "weight_g": 186, "capacity_ml": None},
        "times_used": 0,
        "favorite": False,
    }
    assert {hit["type"] for hit in hits} == {"PRODUCT", "RECIPE"}
    assert fake.requests[-1]["params"]["limit"] == 10
    assert data(tools, "get_recent_foods", meal="lunch")[0]["food_id"] == 24881865
    assert fake.requests[-1]["params"] == {"meal": "lunch"}
    assert len(data(tools, "get_recent_foods", meal="lunch", limit=1)) == 1  # the route itself sends up to 200
    assert tools.call("get_favorite_foods")["data"][0]["food_id"] == 60477557
    assert fake.requests[-1]["params"]["limit"] == 10


def test_search_food_searches_another_countrys_catalog_with_locale(tools: FitatuTools, fake: FakeFitatuServer) -> None:
    data(tools, "search_food", phrase="jajko kurze", locale="en_GB")
    assert fake.requests[-1]["params"]["locale"] == "en_GB"
    data(tools, "search_food", phrase="jajko kurze")
    assert fake.requests[-1]["params"]["locale"] == "pl_PL"  # the session's own


def test_search_recipes_sends_the_energy_filter_and_flags_premium_recipes(tools: FitatuTools, fake: FakeFitatuServer) -> None:
    hits = data(tools, "search_recipes", phrase="kurczak", max_energy_kcal=500, limit=5)
    assert [(hit["food_id"], hit["type"], hit["energy_kcal"], hit["premium"]) for hit in hits] == [
        (7000001, "RECIPE", 420.5, False),
        (7000002, "RECIPE", 480, True),
    ]
    assert (hits[0]["cooking_time"], hits[0]["rating"], hits[0]["measure"]["measure_id"]) == (20, 4.5, 39)
    params = fake.requests[-1]["params"]
    assert {key: params[key] for key in ("phrase", "limit", "hasFilters", "maxEnergy")} == {"phrase": "kurczak", "limit": 5, "hasFilters": "true", "maxEnergy": 500}
    data(tools, "search_recipes")  # browsing: no filter, so the server is told there is none
    assert (fake.requests[-1]["params"]["hasFilters"], "maxEnergy" in fake.requests[-1]["params"]) == ("false", False)


def test_search_recipes_sends_whole_number_filters_and_refuses_the_ones_fitatu_lacks(tools: FitatuTools, fake: FakeFitatuServer) -> None:
    fake.routes[("GET", f"/users/{RECORDED_USER_ID}")] = profile(PREMIUM_FLAGS)
    filters = {"max_energy_kcal": 499.5, "min_protein_g": 30, "max_carbohydrate_g": 0, "max_cooking_time": 30, "diets": ["vegan", "gluten_free"], "meal": "dinner"}
    data(tools, "search_recipes", phrase="kurczak", **filters)
    params = fake.requests[-1]["params"]
    assert {key: params[key] for key in ("hasFilters", "maxEnergy", "minProtein", "maxCarbohydrate", "maxCookingTime", "tags[]", "meals[]")} == {
        "hasFilters": "true",
        "maxEnergy": 499,  # Fitatu answers 400 to a decimal
        "minProtein": 30,
        "maxCarbohydrate": 0,
        "maxCookingTime": 30,
        "tags[]": ["tag.vegan", "tag.gluten_free"],
        "meals[]": ["dinner"],
    }
    assert type(params["maxEnergy"]) is int
    data(tools, "search_recipes", diets=[])  # no diet, no filter
    assert fake.requests[-1]["params"]["hasFilters"] == "false" and "tags[]" not in fake.requests[-1]["params"]
    fake.requests.clear()
    assert tools.call("search_recipes", {"max_cooking_time": 20})["error"]["message"] == "max_cooking_time must be one of 10, 30; got 20"
    assert "paleo" in tools.call("search_recipes", {"diets": ["vegan", "paleo"]})["error"]["message"]
    assert "at least 1" in tools.call("search_recipes", {"min_protein_g": 0})["error"]["message"]
    assert error_type(tools, "search_recipes", {"min_protein_g": 30.5}) == "validation"
    assert fake.requests == []


def test_recipe_filters_and_premium_recipes_need_premium_as_in_the_app(tools: FitatuTools, fake: FakeFitatuServer) -> None:
    # Fitatu serves both to a free account; its app keeps them for Premium, and so do the tools.
    fake.routes[("GET", f"/users/{RECORDED_USER_ID}")] = profile(FREE_FLAGS)
    fake.routes[("GET", "/recipes/7000002")] = {**recorded("recipe_details"), "id": 7000002, "private": False, "premiumInRecipesCatalog": True}
    # What a free account had before still works, without reading the profile.
    assert len(data(tools, "search_recipes", phrase="kurczak", max_energy_kcal=500)) == 2
    assert data(tools, "get_recipe", recipe_id=RECIPE_ID)["premium"] is False
    assert f"/users/{RECORDED_USER_ID}" not in [r["path"] for r in fake.requests]
    fake.requests.clear()
    for name, arguments in (("search_recipes", {"phrase": "kurczak", "diets": ["vegan"]}), ("search_recipes", {"max_carbohydrate_g": 0}), ("get_recipe", {"recipe_id": 7000002})):
        result = tools.call(name, arguments)
        error = result["error"]
        assert (error["error_type"], error["status_code"], error["retryable"]) == ("forbidden", None, False)
        assert error["hint"].startswith("Needs Fitatu Premium") and not re.search(r"@|Someone|ROLE_", json.dumps(result))
    # The flags are read once and trusted for 10 minutes; no search went out.
    assert [r["path"] for r in fake.requests] == [f"/users/{RECORDED_USER_ID}", "/recipes/7000002"]
    fake.routes[("GET", f"/users/{RECORDED_USER_ID}")] = profile(PREMIUM_FLAGS)
    assert tools.call("search_recipes", {"meal": "dinner"})["error"]["error_type"] == "forbidden"
    with patch("fitatu_api.agent.time.monotonic", return_value=time.monotonic() + 601):  # a plan bought meanwhile shows
        hits = tools.call("search_recipes", {"phrase": "kurczak", "diets": ["vegan"], "meal": "dinner"})
        recipe = tools.call("get_recipe", {"recipe_id": 7000002})
    assert hits["ok"] and recipe["ok"] and recipe["data"]["premium"] is True
    assert not re.search(r"@|Someone|ROLE_", json.dumps([hits, recipe]))
    # A reply without accessControl grants nothing.
    fake.routes[("GET", f"/users/{RECORDED_USER_ID}")] = recorded("user_info")
    with patch("fitatu_api.agent.time.monotonic", return_value=time.monotonic() + 1202):
        assert tools.call("search_recipes", {"meal": "dinner"})["error"]["error_type"] == "forbidden"


def test_a_premium_recipes_measures_and_ingredients_need_premium(tools: FitatuTools, fake: FakeFitatuServer) -> None:
    # The recipe planned on DAY, marked premium: as get_recipe, its measures and its shopping are for Premium only.
    fake.routes[("GET", f"/recipes/{RECIPE_ID}")] = {**recorded("recipe_details"), "premiumInRecipesCatalog": True}
    fake.routes[("GET", f"/users/{RECORDED_USER_ID}")] = profile(FREE_FLAGS)
    assert error_type(tools, "get_food_measures", {"food_id": RECIPE_ID, "food_type": "RECIPE"}) == "forbidden"
    shopping = data(tools, "get_shopping_list", from_date=DAY, to_date=DAY)
    assert [(r["recipe_id"], r["expanded"]) for r in shopping["recipes"]] == [(RECIPE_ID, False)]
    assert [entry["name"] for entry in shopping["products"]] == ["Białko jaja kurzego"]  # none of its ingredients
    fake.routes[("GET", f"/users/{RECORDED_USER_ID}")] = profile(PREMIUM_FLAGS)
    with patch("fitatu_api.agent.time.monotonic", return_value=time.monotonic() + 601):
        assert data(tools, "get_food_measures", food_id=RECIPE_ID, food_type="RECIPE")["measures"]
        assert [r["expanded"] for r in data(tools, "get_shopping_list", from_date=DAY, to_date=DAY)["recipes"]] == [True]


def test_adding_a_premium_recipe_needs_premium(tools: FitatuTools, fake: FakeFitatuServer) -> None:
    fake.routes[("GET", f"/recipes/{RECIPE_ID}")] = {**recorded("recipe_details"), "premiumInRecipesCatalog": True}
    fake.routes[("GET", f"/users/{RECORDED_USER_ID}")] = profile(FREE_FLAGS)
    before = rows(fake)
    for name, arguments in (
        ("add_food", {"date": DAY, "meal": "lunch", "food_id": RECIPE_ID, "food_type": "RECIPE", "quantity": 1}),  # in portions
        ("add_food", {"date": DAY, "meal": "lunch", "food_id": RECIPE_ID, "food_type": "RECIPE", "quantity": 1, "measure_id": 39}),
        ("add_foods", ARGUMENTS["add_foods"]),
    ):
        error = tools.call(name, arguments)["error"]
        assert error["error_type"] == "forbidden" and error["hint"].startswith("Needs Fitatu Premium")
    assert rows(fake) == before and all(r["method"] == "GET" for r in fake.requests)  # nothing was written
    # With Premium a recipe in portions is added without reading it.
    fake.routes[("GET", f"/users/{RECORDED_USER_ID}")] = profile(PREMIUM_FLAGS)
    with patch("fitatu_api.agent.time.monotonic", return_value=time.monotonic() + 601):
        fake.requests.clear()
        data(tools, "add_food", date=DAY, meal="lunch", food_id=RECIPE_ID, food_type="RECIPE", quantity=1)
    assert f"/recipes/{RECIPE_ID}" not in [r["path"] for r in fake.requests]


def test_get_recipe_gives_ingredients_steps_and_the_portion_and_refuses_someone_elses_private_recipe(tools: FitatuTools, fake: FakeFitatuServer) -> None:
    recipe = data(tools, "get_recipe", recipe_id=RECIPE_ID)  # a community recipe: private, but shared
    assert (recipe["name"], recipe["servings"], recipe["own"], recipe["deleted"], recipe["rating"]) == ("Gorąca czekolada", 1, False, False, 3.33)
    assert recipe["ingredients"][0] == {
        "food_id": 14440, "food_type": "PRODUCT", "name": "Mąka ziemniaczana", "quantity": 1.0, "measure_id": 19, "measure_name": "teaspoon", "weight_g": 8.0
    }
    assert recipe["ingredients"][3]["weight_g"] == 110.0  # half a 220 g glass
    assert recipe["description"].startswith("1. ") and recipe["cooking_time"] == 5 and recipe["meals"][0] == "second_breakfast"
    assert (recipe["per_serving"]["measure_id"], recipe["per_serving"]["energy_kcal"], recipe["per_serving"]["protein_g"]) == (39, 125.91, 6.66)
    assert recipe["tags"] == [] and not {"username", "userId", "photos"} & set(recipe)  # the author stays out
    assert [r["path"] for r in fake.requests] == [f"/recipes/{RECIPE_ID}"]
    assert data(tools, "get_recipe", recipe_id=MY_RECIPE_ID)["own"] is True
    fake.routes[("GET", "/recipes/7000003")] = {
        **recorded("recipe_details"), "id": 7000003, "tags": [{"name": "tag.vegan"}, {"name": "pesto"}], "private": False, "shared": False
    }
    assert data(tools, "get_recipe", recipe_id=7000003)["tags"] == ["vegan"]  # a catalog recipe; free-text tags are left out
    fake.routes[("GET", "/recipes/7000004")] = {**recorded("recipe_details"), "name": "Cudzy przepis", "shared": False, "userId": 1}
    error = tools.call("get_recipe", {"recipe_id": 7000004})["error"]
    assert error["error_type"] == "not_found" and "Cudzy" not in json.dumps(error)

    def gone(params: Any) -> Any:
        raise FitatuNotFoundError("GET /recipes/999999999 -> 404", status_code=404)

    fake.routes[("GET", "/recipes/999999999")] = gone
    assert error_type(tools, "get_recipe", {"recipe_id": 999999999}) == "not_found"


def test_fasting_is_each_fasting_day_against_its_windows(tools: FitatuTools, fake: FakeFitatuServer) -> None:
    fasting = data(tools, "get_fasting", from_date=DAY, to_date=DAY)
    assert fasting == {
        "from_date": DAY,
        "to_date": DAY,
        "scheme": None,  # the recorded settings set none, so the schemes are not read
        "days": [{"date": DAY, "eating_periods": [{"from": "09:00", "to": "17:30"}], "expected": [{"from": "10:00", "to": "18:00"}]}],
    }
    assert fake.requests[-2]["params"] == {"from": DAY, "to": DAY}
    settings = recorded("settings_new")
    settings["userDietSettings"]["intermittentFastingConfig"] = {"weekDays": [1, 2, 3, 4, 5], "type": 2, "default": None, "startDate": "2027-01-01"}
    fake.routes[("GET", f"/users/{RECORDED_USER_ID}/settings-new/{DAY}")] = settings
    fake.routes[("GET", f"/intermittent-fasting/{RECORDED_USER_ID}/types")] = [{"id": 1, "symbol": "14_10_hours"}, {"id": 2, "symbol": "16_08_hours"}]
    scheme = data(tools, "get_fasting", from_date=DAY, to_date=DAY)["scheme"]
    assert scheme == {"type": "16_08_hours", "week_days": [1, 2, 3, 4, 5], "start_date": "2027-01-01"}
    assert "at most 62 days" in tools.call("get_fasting", {"from_date": "2026-01-01", "to_date": "2026-12-31"})["error"]["message"]


def test_habits_with_their_progress_and_the_ones_on_offer(tools: FitatuTools) -> None:
    assert data(tools, "get_habits") == {
        "habits": [
            {
                "habit_id": 1,
                "name": "Pij wodę",
                "length_days": 21,
                "days_done": 2,
                "checked_days": ["2027-01-01", DAY],
                "enrolled_date": "2027-01-01",
                "completed": False,
            }
        ],
        "available": [{"habit_id": 2, "name": "Jedz warzywa", "length_days": 30, "premium": True}],
    }


def test_good_days_are_filtered_here_and_sorted(tools: FitatuTools) -> None:
    assert data(tools, "get_good_days", from_date="2027-01-01") == {"from_date": "2027-01-01", "to_date": None, "good_days": ["2027-01-01", DAY], "count": 2}
    assert data(tools, "get_good_days")["good_days"] == ["2026-12-30", "2027-01-01", DAY]
    assert data(tools, "get_good_days", to_date="2026-12-31")["count"] == 1
    assert error_type(tools, "get_good_days", {"from_date": DAY, "to_date": "2027-01-01"}) == "validation"


def test_find_by_barcode_confirms_the_first_product_hit_by_its_details(tools: FitatuTools, fake: FakeFitatuServer) -> None:
    egg = recorded("search_food")[0]
    assert data(tools, "find_by_barcode", barcode=BARCODE) == data(tools, "search_food", phrase="jajko kurze")[0]
    assert [(r["path"], r.get("params", {}).get("phrase")) for r in fake.requests[:2]] == [
        ("/search/new/food", BARCODE), (f"/products/{egg['foodId']}", None)
    ]
    # the search also matches names: a hit whose details carry another barcode is not that product
    fake.routes[("GET", f"/products/{egg['foodId']}")]["barcode"] = "5900000000000"
    error = tools.call("find_by_barcode", {"barcode": BARCODE})["error"]
    assert error["error_type"] == "not_found" and "search_food by the product's name" in error["message"]
    # the user's own product keeps a barcode they gave it (create_food's) apart (checked live 2026-10-15)
    fake.routes[("GET", f"/products/{egg['foodId']}")] |= {"barcode": None, "currentUserPrivateBarcode": BARCODE}
    assert data(tools, "find_by_barcode", barcode=BARCODE)["food_id"] == egg["foodId"]
    fake.requests.clear()
    fake.search_results[BARCODE] = [hit for hit in recorded("search_food_recipe") if hit["type"] == "RECIPE"]
    assert error_type(tools, "find_by_barcode", {"barcode": BARCODE}) == "not_found"
    assert [r["path"] for r in fake.requests] == ["/search/new/food"]  # no product hit: nothing more to read


def test_get_food_measures_reads_the_product_or_recipe(tools: FitatuTools, fake: FakeFitatuServer) -> None:
    # The recorded product has 10.9 g protein, 0.2 g fat and 0.7 g carbohydrate per 100 g
    product = data(tools, "get_food_measures", food_id=PRODUCT_ID, food_type="PRODUCT")
    assert product["measures"] == [
        {"measure_id": 39, "name": "portion", "weight_g": 35, "capacity_ml": None, "energy_kcal": 16.8,
         "protein_g": 3.815, "fat_g": 0.07, "carbohydrate_g": 0.245},
        {"measure_id": 1, "name": "g", "weight_g": 1, "capacity_ml": None, "energy_kcal": 0.48,
         "protein_g": 0.109, "fat_g": 0.002, "carbohydrate_g": 0.007},
    ]
    # and the recorded recipe 6.66 g, 2.85 g and 18.6 g per portion of 248 g
    recipe = data(tools, "get_food_measures", food_id=RECIPE_ID, food_type="RECIPE")
    assert recipe["name"] == "Gorąca czekolada" and fake.requests[-1]["path"] == f"/recipes/{RECIPE_ID}"
    assert [(m["name"], m["protein_g"], m["fat_g"], m["carbohydrate_g"]) for m in recipe["measures"]] == [
        ("g", 0.0269, 0.0115, 0.075),
        ("portion", 6.66, 2.85, 18.6),
    ]


def test_measure_macros_are_null_unless_the_measure_gives_its_own_energy_back(tools: FitatuTools, fake: FakeFitatuServer) -> None:
    details = recorded("product_details")  # 48 kcal per 100 g
    details["measures"] = [
        {"id": 7, "name": "glass", "energyPerUnit": 120, "weightPerUnit": 260, "capacityPerUnit": 250},  # per 100 ml
        {"id": 8, "name": "odd", "energyPerUnit": 10, "weightPerUnit": 35},  # 35 g would be 16.8 kcal
        {"id": 9, "name": "no weight", "energyPerUnit": 1},
    ]
    fake.routes[("GET", f"/products/{PRODUCT_ID}")] = details
    measures = data(tools, "get_food_measures", food_id=PRODUCT_ID, food_type="PRODUCT")["measures"]
    assert [(m["protein_g"], m["fat_g"], m["carbohydrate_g"]) for m in measures] == [(27.25, 0.5, 1.75), (None,) * 3, (None,) * 3]


def test_get_food_measures_refuses_a_recipe_sent_as_a_product(tools: FitatuTools, fake: FakeFitatuServer) -> None:
    def server(method: str, path: str, **kwargs: Any) -> Any:
        if path == f"/products/{RECIPE_ID}":  # what Fitatu answers for a recipe id
            raise FitatuNotFoundError(f"GET {path} -> 404", status_code=404)
        return fake.request(method, path, **kwargs)

    tools.client.request = server  # type: ignore[method-assign]
    assert error_type(tools, "get_food_measures", {"food_id": RECIPE_ID, "food_type": "PRODUCT"}) == "not_found"


def test_weight_water_and_activity_reads(tools: FitatuTools, fake: FakeFitatuServer) -> None:
    assert data(tools, "get_weight_history", from_date=WEIGHT_DAY, to_date=WEIGHT_DAY) == [
        {"date": WEIGHT_DAY, "value": 70, "difference": None, "direction": 0}
    ]
    assert data(tools, "get_water", date=DAY) == {"date": DAY, "water_ml": 750, "target_ml": 2000, "glass_ml": 250}
    fake.routes[("GET", f"/water/{RECORDED_USER_ID}/{DAY}")] = {"water": {"waterConsumption": 750}}
    assert data(tools, "get_water", date=DAY) == {"date": DAY, "water_ml": 750, "target_ml": None, "glass_ml": None}
    fake.routes[("GET", f"/water/{RECORDED_USER_ID}/{DAY}")] = {"water": {"waterConsumption": 0}, "settings": {"waterUnitCapacity": 0}}
    assert data(tools, "get_water", date=DAY)["glass_ml"] is None
    running = data(tools, "search_activity", phrase="bieganie")[0]
    assert running == {"activity_id": 535, "name": "Bieganie", "energy_kcal": 340, "energy_for": "5 km w 00:30:00"}


def test_get_body_measurements_lists_only_the_parts_with_values(tools: FitatuTools, fake: FakeFitatuServer) -> None:
    sizes = data(tools, "get_body_measurements", from_date=WEIGHT_DAY, to_date=WEIGHT_DAY)
    assert sizes == {
        "from_date": WEIGHT_DAY,
        "to_date": WEIGHT_DAY,
        "measurements": [{"body_part": "waist", "start_value": 80, "end_value": 80, "difference": 0}],
    }
    assert fake.requests[-1]["params"] == {"fromDate": WEIGHT_DAY, "toDate": WEIGHT_DAY}
    data(tools, "get_body_measurements")
    assert fake.requests[-1]["params"] is None
    fake.routes[("GET", f"/users/{RECORDED_USER_ID}/measurements/summary/size")] = {
        "fatPercentage": {"startValue": 20.5, "endValue": 19.8, "difference": -0.7}
    }
    assert data(tools, "get_body_measurements")["measurements"][0]["body_part"] == "fat_percentage"


def test_get_measurement_history_reads_one_part(tools: FitatuTools, fake: FakeFitatuServer) -> None:
    assert data(tools, "get_measurement_history", body_part="waist", from_date=WEIGHT_DAY, to_date=WEIGHT_DAY) == [{"date": WEIGHT_DAY, "value": 80}]
    assert fake.requests[-1]["params"] == {"fromDate": WEIGHT_DAY, "toDate": WEIGHT_DAY, "page": 1, "limit": 41}
    fake.routes[("GET", f"/users/{RECORDED_USER_ID}/measurements/size/fatPercentage")] = [{"date": WEIGHT_DAY, "value": 20}]
    assert data(tools, "get_measurement_history", body_part="fat_percentage") == [{"date": WEIGHT_DAY, "value": 20}]
    assert fake.requests[-1]["path"] == f"/users/{RECORDED_USER_ID}/measurements/size/fatPercentage"


def test_get_profile_reads_the_settings_and_leaves_out_what_identifies(tools: FitatuTools, fake: FakeFitatuServer) -> None:
    profile = data(tools, "get_profile")
    assert profile == {
        "height_cm": 180,
        "weight_kg": 80,
        "start_weight_kg": 85,
        "target_weight_kg": 75,
        "bmi": 24.7,
        "goal": "maintain",
        "pace_kg_per_week": 0,
        "pal": 1.4,
        "sex": "male",
        "age": 36,
        "premium": {"active": False, "end_date": None},
    }
    # GET /users/{id} carries the e-mail, username and ids; the birth date and user id are dropped
    assert ("GET", f"/users/{RECORDED_USER_ID}") not in [(r["method"], r["path"]) for r in fake.requests]
    assert "1990-01-01" not in json.dumps(profile) and RECORDED_USER_ID not in json.dumps(profile)
    settings = recorded("settings_new")
    settings["userDietSettings"]["weightChangeDirection"] = -1
    settings["userSettings"] |= {"sex": 2, "heightCm": None}
    fake.routes[("GET", f"/users/{RECORDED_USER_ID}/settings-new/{TODAY}")] = settings
    assert {key: data(tools, "get_profile")[key] for key in ("goal", "sex", "bmi")} == {"goal": "lose", "sex": "female", "bmi": None}
    settings["userSettings"]["sex"] = 0  # not known
    assert data(tools, "get_profile")["sex"] is None


# -- writes -------------------------------------------------------------------------------


def test_add_food_logs_a_product_in_grams(tools: FitatuTools, fake: FakeFitatuServer) -> None:
    added = data(tools, "add_food", **{**ARGUMENTS["add_food"], "eaten": True})
    assert added["meal"] == "dinner" and added["ok"] is True
    assert (added["measure_id"], added["measure_name"], added["measure_quantity"]) == (1, "g", 70)
    meal, row = rows(fake)[added["row_id"]]
    assert (meal, row["productId"], row["measureId"], row["measureQuantity"], row["eaten"]) == ("dinner", PRODUCT_ID, 1, 70, True)


def test_add_food_by_measure_and_a_recipe_in_portions(tools: FitatuTools, fake: FakeFitatuServer) -> None:
    portions = data(tools, "add_food", date=DAY, meal="breakfast", food_id=PRODUCT_ID, food_type="PRODUCT", quantity=2, measure_id=39)
    recipe = data(tools, "add_food", date=DAY, meal="lunch", food_id=RECIPE_ID, food_type="RECIPE", quantity=1.5)
    assert (portions["measure_id"], portions["measure_name"], portions["measure_quantity"]) == (39, "portion", 2)
    assert (recipe["measure_id"], recipe["measure_name"], recipe["measure_quantity"]) == (39, None, 1.5)
    day = rows(fake)
    assert (day[portions["row_id"]][1]["measureId"], day[portions["row_id"]][1]["measureQuantity"]) == (39, 2)
    assert {key: day[recipe["row_id"]][1][key] for key in ("foodType", "recipeId", "measureId", "measureQuantity")} == {
        "foodType": "RECIPE",
        "recipeId": RECIPE_ID,
        "measureId": 39,
        "measureQuantity": 1.5,
    }


def test_grams_of_a_food_without_a_gram_measure_say_which_measure_they_were_stored_in(
    tools: FitatuTools, fake: FakeFitatuServer
) -> None:
    portion_only = recorded("product_details")
    portion_only["measures"] = [m for m in portion_only["measures"] if m["name"] == "portion"]  # 35 g
    fake.routes[("GET", f"/products/{PRODUCT_ID}")] = portion_only
    added = data(tools, "add_food", **{**ARGUMENTS["add_food"], "quantity": 100})
    # what update_item's quantity counts: 100 there would be 100 portions, not 100 g
    assert (added["measure_id"], added["measure_name"], added["measure_quantity"]) == (39, "portion", 2.86)
    assert rows(fake)[added["row_id"]][1]["measureId"] == 39


@pytest.mark.parametrize(("food_id", "food_type", "path"), [(PRODUCT_ID, "PRODUCT", "products"), (RECIPE_ID, "RECIPE", "recipes")])
def test_add_food_refuses_a_measure_the_food_does_not_have(
    tools: FitatuTools, fake: FakeFitatuServer, food_id: int, food_type: str, path: str
) -> None:
    error = tools.call("add_food", {**ARGUMENTS["add_food"], "food_id": food_id, "food_type": food_type, "unit": None, "measure_id": 46})
    assert error["error"]["error_type"] == "validation" and error["error"]["hint"] is None
    assert "no measure_id 46" in error["error"]["message"] and "39 portion" in error["error"]["message"]
    assert [(r["method"], r["path"]) for r in fake.requests] == [("GET", f"/{path}/{food_id}")]  # nothing written


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


def test_add_foods_logs_every_item_in_one_sync(tools: FitatuTools, fake: FakeFitatuServer) -> None:
    added = data(tools, "add_foods", **ARGUMENTS["add_foods"])
    product, recipe = added["rows"]
    assert (added["meal"], added["ok"]) == ("dinner", True)
    assert {key: value for key, value in product.items() if key != "row_id"} == {
        "food_id": PRODUCT_ID, "measure_id": 1, "measure_name": "g", "measure_quantity": 70
    }
    assert {key: value for key, value in recipe.items() if key != "row_id"} == {
        "food_id": RECIPE_ID, "measure_id": 39, "measure_name": None, "measure_quantity": 1.5
    }
    [payload] = fake.sync_payloads
    assert [row["planDayDietItemId"] for row in payload[DAY]["dietPlan"]["dinner"]["items"]] == [product["row_id"], recipe["row_id"]]
    day = rows(fake)
    assert [(day[row["row_id"]][0], day[row["row_id"]][1]["eaten"]) for row in added["rows"]] == [("dinner", True)] * 2  # eaten by default


def test_add_foods_writes_nothing_when_an_item_is_wrong(tools: FitatuTools, fake: FakeFitatuServer) -> None:
    good, recipe = ARGUMENTS["add_foods"]["items"]
    error = tools.call("add_foods", {**ARGUMENTS["add_foods"], "items": [good, {**recipe, "measure_id": 46}]})["error"]
    assert error["error_type"] == "validation" and error["message"].startswith("items[1]: RECIPE 590200 has no measure_id 46")
    assert error["hint"] is None  # refused before anything was sent: no row_id to repeat it with
    assert fake.sync_payloads == [] and {r["method"] for r in fake.requests} == {"GET"}

    def server(method: str, path: str, **kwargs: Any) -> Any:
        if path == f"/products/{RECIPE_ID}":  # a recipe's food_id sent as a PRODUCT
            raise FitatuNotFoundError(f"GET {path} -> 404", status_code=404)
        return fake.request(method, path, **kwargs)

    tools.client.request = server  # type: ignore[method-assign]
    error = tools.call("add_foods", {**ARGUMENTS["add_foods"], "items": [good, {**good, "food_id": RECIPE_ID}]})["error"]
    assert error["error_type"] == "not_found" and error["message"].startswith(f"items[1]: GET /products/{RECIPE_ID} -> 404")
    fake.requests.clear()
    for item, message in (({**good, "quantity": "70"}, "items[0]: quantity must be a JSON number"),
                          ({**good, "unit": None}, "items[0]: a PRODUCT needs unit")):
        assert tools.call("add_foods", {**ARGUMENTS["add_foods"], "items": [item]})["error"]["message"].startswith(message)
    assert fake.requests == []


def test_a_failed_add_foods_repeated_with_its_row_id_logs_each_food_once(
    tools: FitatuTools, fake: FakeFitatuServer, monkeypatch: pytest.MonkeyPatch
) -> None:
    def saved_then_timed_out(payload: dict[str, Any]) -> Any:
        FakeFitatuServer._sync(fake, payload)
        raise FitatuConnectionError("read timeout")

    monkeypatch.setattr(fake, "_sync", saved_then_timed_out)
    error = tools.call("add_foods", ARGUMENTS["add_foods"])["error"]
    logged = [rid for rid, (meal, _) in rows(fake).items() if meal == "dinner"]
    assert (error["error_type"], error["retryable"], len(logged)) == ("network", False, 2) and logged[0] in error["hint"]
    monkeypatch.undo()
    again = data(tools, "add_foods", **ARGUMENTS["add_foods"], row_id=logged[0])
    assert [row["row_id"] for row in again["rows"]] == logged
    assert [rid for rid, (meal, _) in rows(fake).items() if meal == "dinner"] == logged


def test_add_custom_item_needs_only_the_kcal(tools: FitatuTools, fake: FakeFitatuServer) -> None:
    added = data(tools, "add_custom_item", date=DAY, meal="supper", name="Kanapka", energy_kcal=350)
    row = rows(fake)[added["row_id"]][1]
    assert (row["energy"], row["protein"], row["fat"], row["carbohydrate"]) == (350, 0, 0, 0)


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


@pytest.mark.parametrize("name", ["add_food", "add_foods", "add_custom_item", "copy_meal", "add_activity"])
def test_every_add_that_may_have_landed_gives_its_retry_row_id(
    tools: FitatuTools, fake: FakeFitatuServer, monkeypatch: pytest.MonkeyPatch, name: str
) -> None:
    def saved_then_timed_out(payload: dict[str, Any]) -> Any:
        FakeFitatuServer._sync(fake, payload)
        raise FitatuConnectionError("read timeout")

    monkeypatch.setattr(fake, "_sync", saved_then_timed_out)
    error = tools.call(name, ARGUMENTS[name])["error"]
    row_id = error["retry_row_id"]  # its own field, so code need not parse the hint
    assert row_id in error["hint"] and error["retryable"] is False
    # a row_id the caller gave comes back too; the same call is then safe to repeat as it is
    again = tools.call(name, {**ARGUMENTS[name], "row_id": row_id})["error"]
    assert (again["retry_row_id"], again["retryable"]) == (row_id, True)
    monkeypatch.undo()
    assert data(tools, name, **ARGUMENTS[name], row_id=row_id)["ok"]
    # refused before anything was sent: nothing to repeat ("" too, which must not get a fresh id)
    for wrong in ("not-a-uuid", ""):
        error = tools.call(name, {**ARGUMENTS[name], "row_id": wrong})["error"]
        assert error["error_type"] == "validation" and "retry_row_id" not in error


def test_update_item_changes_quantity_and_eaten(tools: FitatuTools, fake: FakeFitatuServer) -> None:
    updated = data(tools, "update_item", date=DAY, row_id=ROWS["lunch"], quantity=2, eaten=True)
    previous = {"quantity": 1, "eaten": False}
    assert updated == {"date": DAY, "meal": "lunch", "row_id": ROWS["lunch"], "previous": previous, "ok": True}
    row = rows(fake)[ROWS["lunch"]][1]
    assert (row["measureQuantity"], row["eaten"]) == (2, True)
    # undo: previous goes back into update_item as it is
    assert data(tools, "update_item", date=DAY, row_id=ROWS["lunch"], **updated["previous"])["previous"] == {"quantity": 2, "eaten": True}
    row = rows(fake)[ROWS["lunch"]][1]
    assert (row["measureQuantity"], row["eaten"]) == (1, False)


def test_move_item_to_another_meal_and_day(tools: FitatuTools, fake: FakeFitatuServer) -> None:
    moved = data(tools, "move_item", date=DAY, row_id=ROWS["snack"], to_meal="dinner", to_date="2027-01-04")
    assert (moved["date"], moved["meal"], moved["ok"]) == ("2027-01-04", "dinner", True)
    assert ROWS["snack"] not in rows(fake)
    assert rows(fake, "2027-01-04")[moved["row_id"]][0] == "dinner"


def test_copy_meal_copies_a_meal_to_another_day(tools: FitatuTools, fake: FakeFitatuServer) -> None:
    data(tools, "update_item", date=DAY, row_id=ROWS["breakfast"], eaten=True)
    copied = data(tools, "copy_meal", from_date=DAY, from_meal="breakfast", to_date=NEXT_DAY, to_meal="supper")
    (row_id,) = copied["row_ids"]
    assert copied == {"from_date": DAY, "from_meal": "breakfast", "to_date": NEXT_DAY, "to_meal": "supper",
                      "row_ids": [row_id], "skipped": [], "ok": True}
    meal, row = rows(fake, NEXT_DAY)[row_id]
    assert (meal, row["productId"], row["measureId"], row["measureQuantity"], row["eaten"]) == ("supper", PRODUCT_ID, 39, 1, True)
    # planned for tomorrow: eaten false for every copy; and only the rows named
    planned = data(tools, "copy_meal", from_date=DAY, from_meal="snack", to_date=NEXT_DAY, row_ids=[ROWS["snack"]], eaten=False)
    meal, row = rows(fake, NEXT_DAY)[planned["row_ids"][0]]
    assert (meal, row["name"], row["energy"], row["eaten"]) == ("snack", "fitatu-api fixture", 250, False)


def test_copy_meal_lists_the_rows_it_cannot_copy() -> None:
    seed = json.loads(json.dumps(SEED))
    lunch = seed["dietPlan"]["lunch"]["items"]
    lunch.append({**lunch[0], "planDayDietItemId": "ai-row", "foodType": "RECIPE_AI", "name": "Zupa z AI"})
    server = FakeFitatuServer(seed)
    client = FitatuApiClient(FitatuAuthContext(bearer_token="t", fitatu_user_id=RECORDED_USER_ID))
    client.request = server.request  # type: ignore[method-assign]
    copied = data(FitatuTools(client), "copy_meal", from_date=DAY, from_meal="lunch", to_date=DAY, to_meal="dinner")
    assert copied["skipped"] == [{"row_id": "ai-row", "name": "Zupa z AI", "food_type": "RECIPE_AI"}]
    assert copied["ok"] and [rows(server)[row_id][0] for row_id in copied["row_ids"]] == ["dinner"]


def test_a_failed_copy_repeated_with_its_row_id_writes_nothing_new(
    tools: FitatuTools, fake: FakeFitatuServer, monkeypatch: pytest.MonkeyPatch
) -> None:
    def saved_then_timed_out(payload: dict[str, Any]) -> Any:
        FakeFitatuServer._sync(fake, payload)
        raise FitatuConnectionError("read timeout")

    monkeypatch.setattr(fake, "_sync", saved_then_timed_out)
    error = tools.call("copy_meal", ARGUMENTS["copy_meal"])["error"]
    (row_id,) = [rid for rid, (meal, _) in rows(fake, NEXT_DAY).items() if meal == "dinner"]
    assert (error["error_type"], error["retryable"]) == ("network", False) and row_id in error["hint"]
    monkeypatch.undo()
    again = data(tools, "copy_meal", **ARGUMENTS["copy_meal"], row_id=row_id)
    assert again["ok"] and again["row_ids"] == [row_id]
    assert [rid for rid, (meal, _) in rows(fake, NEXT_DAY).items() if meal == "dinner"] == [row_id]


LUNCH_ROW = {
    "meal": "lunch",
    "meal_name": "Lunch",
    "row_id": ROWS["lunch"],
    "name": "Gorąca czekolada",
    "food_type": "RECIPE",
    "food_id": RECIPE_ID,
    "measure_id": 39,
    "measure_name": "portion",
    "measure_quantity": 1,
    "energy": 125.91,
    "protein": 6.66,
    "fat": 2.85,
    "carbohydrate": 18.6,
    "eaten": False,
}


def test_preview_removal_lists_what_remove_items_would_delete_and_deletes_nothing(tools: FitatuTools, fake: FakeFitatuServer) -> None:
    preview = data(tools, "preview_removal", date=DAY, row_ids=[ROWS["lunch"], "gone"])
    assert preview == {"date": DAY, "ok": False, "would_remove": [LUNCH_ROW], "missing": ["gone"]}
    assert fake.sync_payloads == [] and ROWS["lunch"] in rows(fake)
    assert data(tools, "remove_items", date=DAY, row_ids=[ROWS["lunch"]])["removed"] == preview["would_remove"]


def test_remove_items_reports_removed_and_missing_row_ids(tools: FitatuTools, fake: FakeFitatuServer) -> None:
    preview = data(tools, "remove_items", date=DAY, row_ids=[ROWS["lunch"], "gone"], dry_run=True)
    assert preview == {"date": DAY, "ok": False, "dry_run": True, "removed": [LUNCH_ROW], "failed": [], "missing": ["gone"]}
    assert fake.sync_payloads == []

    removed = data(tools, "remove_items", date=DAY, row_ids=[ROWS["lunch"], ROWS["snack"]])
    assert removed["ok"] and removed["failed"] == []
    assert {row["row_id"]: (row["name"], row["food_id"]) for row in removed["removed"]} == {
        ROWS["lunch"]: ("Gorąca czekolada", RECIPE_ID),
        ROWS["snack"]: ("fitatu-api fixture", None),  # a custom row has no food_id: add_custom_item logs it again
    }
    assert set(rows(fake)) == {ROWS["breakfast"]}
    again = data(tools, "remove_items", date=DAY, row_ids=[ROWS["lunch"]])
    assert (again["ok"], again["removed"], again["missing"]) == (False, [], [ROWS["lunch"]])


def test_a_removed_row_is_added_again_from_what_remove_items_returned(tools: FitatuTools, fake: FakeFitatuServer) -> None:
    (row,) = data(tools, "remove_items", date=DAY, row_ids=[ROWS["breakfast"]])["removed"]
    keys = ("meal", "food_id", "food_type", "measure_id", "eaten")
    again = data(tools, "add_food", date=DAY, quantity=row["measure_quantity"], **{key: row[key] for key in keys})
    stored = rows(fake)[again["row_id"]]
    assert (stored[0], stored[1]["productId"], stored[1]["measureId"], stored[1]["measureQuantity"]) == ("breakfast", PRODUCT_ID, 39, 1)


def test_a_removed_custom_row_is_added_again_with_its_nutrition(tools: FitatuTools, fake: FakeFitatuServer) -> None:
    (row,) = data(tools, "remove_items", date=DAY, row_ids=[ROWS["snack"]])["removed"]
    assert row["food_id"] is None  # add_food cannot log it; add_custom_item can, from what the row carries
    again = data(tools, "add_custom_item", date=DAY, meal=row["meal"], name=row["name"], energy_kcal=row["energy"],
                 protein_g=row["protein"], fat_g=row["fat"], carbohydrate_g=row["carbohydrate"], eaten=row["eaten"])
    (stored,) = (item for meal in data(tools, "get_day", date=DAY)["meals"] for item in meal["items"] if item["row_id"] == again["row_id"])
    assert (stored["name"], stored["energy"], stored["protein"], stored["fat"], stored["carbohydrate"]) == (
        "fitatu-api fixture", 250, 10, 8, 30
    )


def test_remove_items_lists_a_row_the_sync_left_as_failed(
    tools: FitatuTools, fake: FakeFitatuServer, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(fake, "_sync", lambda payload: recorded("sync_update_product"))  # accepted, nothing deleted
    result = data(tools, "remove_items", date=DAY, row_ids=[ROWS["lunch"]])
    assert (result["ok"], result["removed"], result["failed"]) == (False, [], [LUNCH_ROW])


def test_log_weight_and_set_water_send_absolute_values(tools: FitatuTools, fake: FakeFitatuServer) -> None:
    assert data(tools, "log_weight", date=WEIGHT_DAY, weight_kg=70) == {"date": WEIGHT_DAY, "weight_kg": 70, "ok": True}
    assert fake.requests[-1]["method"] == "PUT"
    assert {key: fake.requests[-1]["json_data"][key] for key in ("weight", "weightUnit")} == {"weight": "70.0", "weightUnit": "KG"}
    assert data(tools, "set_water", date=DAY, water_ml=1500) == {"date": DAY, "water_ml": 1500, "ok": True}
    assert fake.requests[-1]["json_data"] == {"waterConsumption": 1500}


def test_set_targets_sends_the_whole_diet_with_the_goal_and_its_grams(tools: FitatuTools, fake: FakeFitatuServer) -> None:
    # The recorded settings: automatic, 2000 kcal, 20/30/50 with 100/67/250 g, no glass size (0).
    result = data(tools, "set_targets", **ARGUMENTS["set_targets"])
    assert [(r["method"], r["path"]) for r in fake.requests] == [
        ("GET", f"/users/{RECORDED_USER_ID}/settings-new/{TODAY}"),
        ("PATCH", f"/users/{RECORDED_USER_ID}/settings-new"),
    ]
    # Fitatu refuses a part of userDietSettings (checked live): the whole one goes back with the changes.
    diet = {**recorded("settings_new")["userDietSettings"], **MANUAL_1800}
    assert fake.requests[-1]["json_data"] == {"userId": 1234567, "updatedAt": None, "userDietSettings": diet}
    assert (result["energy"], result["protein"], result["automatic"], result["glass_ml"]) == (1800, {"grams": 180, "percent": 40}, False, None)
    assert (result["date"], result["previous"], result["ok"]) == (TODAY, {"automatic": True}, True)


def test_set_targets_keeps_the_split_or_takes_20_30_50_when_there_is_none(tools: FitatuTools, fake: FakeFitatuServer) -> None:
    expected = {"manualEnergyTarget": True, "energy": 1800, "proteinPercentage": 20, "proteinWeight": 90, "fatPercentage": 30,
                "fatWeight": 60, "carbohydratePercentage": 50, "carbohydrateWeight": 225}
    data(tools, "set_targets", energy_kcal=1800)
    assert fake.requests[-1]["json_data"]["userDietSettings"] == {**recorded("settings_new")["userDietSettings"], **expected}
    # A free account's automatic goal has no split (checked live), and a manual goal needs one.
    none = dict.fromkeys(f"{macro}{part}" for macro in ("protein", "fat", "carbohydrate") for part in ("Percentage", "Weight"))
    fake.routes[("GET", f"/users/{RECORDED_USER_ID}/settings-new/{TODAY}")] = settings_after(none)
    data(tools, "set_targets", energy_kcal=1800)
    assert fake.requests[-1]["json_data"]["userDietSettings"] == {**settings_after(none)["userDietSettings"], **expected}


def test_set_targets_percentages_alone_keep_the_energy(tools: FitatuTools, fake: FakeFitatuServer) -> None:
    data(tools, "set_targets", protein_pct=40, fat_pct=30, carbohydrate_pct=30)
    diet = fake.requests[-1]["json_data"]["userDietSettings"]
    assert (diet["manualEnergyTarget"], diet["energy"]) == (True, 2000)
    assert (diet["proteinWeight"], diet["fatWeight"], diet["carbohydrateWeight"]) == (200, 67, 150)  # 66.7 g of fat rounds to 67


def test_set_targets_back_to_automatic(tools: FitatuTools, fake: FakeFitatuServer) -> None:
    manual = settings_after(MANUAL_1800)
    fake.routes[("GET", f"/users/{RECORDED_USER_ID}/settings-new/{TODAY}")] = manual
    fake.routes[("PATCH", f"/users/{RECORDED_USER_ID}/settings-new")] = recorded("settings_new")
    result = data(tools, "set_targets", automatic=True)
    # Only the mode changes: Fitatu works the energy out again.
    assert fake.requests[-1]["json_data"]["userDietSettings"] == {**manual["userDietSettings"], "manualEnergyTarget": False}
    assert (result["automatic"], result["energy"], result["ok"]) == (True, 2000, True)
    assert result["previous"] == {"energy_kcal": 1800, "protein_pct": 40, "fat_pct": 30, "carbohydrate_pct": 30}
    data(tools, "set_targets", **result["previous"])  # the undo SYSTEM_PROMPT describes
    assert {key: fake.requests[-1]["json_data"]["userDietSettings"][key] for key in MANUAL_1800} == MANUAL_1800


def test_set_targets_water_glass_only(tools: FitatuTools, fake: FakeFitatuServer) -> None:
    fake.routes[("PATCH", f"/users/{RECORDED_USER_ID}/settings-new")] = settings_after(glass=300)
    result = data(tools, "set_targets", water_glass_ml=300)
    assert fake.requests[-1]["json_data"] == {"userId": 1234567, "updatedAt": None, "waterSettings": {"unitCapacity": 300}}
    assert (result["glass_ml"], result["automatic"], result["ok"]) == (300, True, True)
    # previous holds only what the call changed; the recorded glass size is none (0), which cannot be sent back.
    assert result["previous"] == {}
    fake.routes[("GET", f"/users/{RECORDED_USER_ID}/settings-new/{TODAY}")] = settings_after(glass=250)
    assert data(tools, "set_targets", water_glass_ml=300)["previous"] == {"water_glass_ml": 250}
    assert data(tools, "set_targets", energy_kcal=1800)["previous"] == {"automatic": True}


def test_set_targets_is_not_ok_when_the_reply_does_not_show_it(tools: FitatuTools, fake: FakeFitatuServer) -> None:
    fake.routes[("PATCH", f"/users/{RECORDED_USER_ID}/settings-new")] = recorded("settings_new")
    assert data(tools, "set_targets", energy_kcal=1800)["ok"] is False
    # An empty reply: the settings are read again.
    fake.routes[("PATCH", f"/users/{RECORDED_USER_ID}/settings-new")] = None
    fake.requests.clear()
    assert data(tools, "set_targets", water_glass_ml=300)["ok"] is False
    assert [r["method"] for r in fake.requests] == ["GET", "PATCH", "GET"]


def test_set_note_replaces_the_note_keeps_its_tags_and_sends_nothing_else(tools: FitatuTools, fake: FakeFitatuServer) -> None:
    fake.day_notes[DAY] = {"note": "Stara notatka", "tagsIds": [3]}
    before = rows(fake)
    assert data(tools, "set_note", date=DAY, note="Nowa notatka") == {"date": DAY, "note": "Nowa notatka", "previous": "Stara notatka", "ok": True}
    assert fake.sync_payloads[-1] == {DAY: {"note": "Nowa notatka", "tagsIds": [3]}}  # the rows are not sent, and the day keeps them
    assert rows(fake) == before
    day = data(tools, "get_day", date=DAY)
    assert (day["note"], day["tags_ids"]) == ("Nowa notatka", [3])
    # "" deletes it, as the app does with null; the previous note is what puts it back
    assert data(tools, "set_note", date=DAY, note="") == {"date": DAY, "note": None, "previous": "Nowa notatka", "ok": True}
    assert fake.sync_payloads[-1][DAY]["note"] is None and data(tools, "get_day", date=DAY)["note"] is None
    assert error_type(tools, "set_note", {"date": DAY}) == "validation"  # an omitted note never deletes one
    # A day without a note: previous is '', so the undo SYSTEM_PROMPT describes deletes the new one again.
    written = data(tools, "set_note", date=DAY, note="Ból głowy")
    assert written["previous"] == "" and data(tools, "set_note", date=DAY, note=written["previous"])["ok"] is True
    assert data(tools, "get_day", date=DAY)["note"] is None


def test_log_measurements_replaces_only_the_values_given(tools: FitatuTools, fake: FakeFitatuServer) -> None:
    fake.routes[("GET", f"/users/{RECORDED_USER_ID}/measurements/{WEIGHT_DAY}")] = recorded("measurement_day")  # 70 kg, waist 80
    logged = data(tools, "log_measurements", date=WEIGHT_DAY, hips=95.5, fat_percentage=18)
    assert logged == {"date": WEIGHT_DAY, "hips": 95.5, "fat_percentage": 18, "size_unit": "CM", "ok": True}
    put = fake.requests[-1]
    assert (put["method"], put["path"]) == ("PUT", f"/users/{RECORDED_USER_ID}/measurements/{WEIGHT_DAY}")
    assert put["json_data"] == {
        "date": WEIGHT_DAY, "weight": "70.0", "waist": "80.0", "hips": "95.5", "fatPercentage": "18.0", "weightUnit": "KG", "sizeUnit": "CM"
    }


def test_log_measurements_takes_the_users_size_unit(tools: FitatuTools, fake: FakeFitatuServer) -> None:
    fake.routes[("GET", f"/users/{RECORDED_USER_ID}")] = {**recorded("user_info"), "sizeUnit": "IN"}
    assert data(tools, "log_measurements", date=WEIGHT_DAY, waist=31.5)["size_unit"] == "IN"
    assert fake.requests[-1]["json_data"] == {"date": WEIGHT_DAY, "waist": "31.5", "fatPercentage": "0.0", "weightUnit": "KG", "sizeUnit": "IN"}


def test_set_favorite_marks_and_unmarks_a_food(tools: FitatuTools, fake: FakeFitatuServer) -> None:
    for favorite in (True, False):
        assert data(tools, "set_favorite", food_id=PRODUCT_ID, food_type="PRODUCT", favorite=favorite) == {
            "food_id": PRODUCT_ID,
            "food_type": "PRODUCT",
            "favorite": favorite,
            "ok": True,
        }
        request = fake.requests[-1]
        assert (request["method"], request["json_data"]) == ("PUT", {"markAsFavorite": favorite})
        assert request["path"] == f"/users/{RECORDED_USER_ID}/favorite/food/PRODUCT/{PRODUCT_ID}"


def test_get_my_foods_searches_the_users_own_foods(tools: FitatuTools, fake: FakeFitatuServer) -> None:
    # the same hits as search_food, from the user's own foods (the fake answers with the recorded catalog ones)
    assert data(tools, "get_my_foods", phrase="owsianka")[0] == data(tools, "search_food", phrase="owsianka")[0]
    request = fake.requests[-2]
    assert request["path"] == f"/search/food/user/{RECORDED_USER_ID}" and request["params"]["phrase"] == "owsianka"
    assert request["params"]["date"] == tools.get_today()["date"] and request["params"]["limit"] == 50
    assert len(data(tools, "get_my_foods")) == 10 and fake.requests[-1]["params"]["phrase"] == ""
    # The route answers every own food whatever the phrase (checked live 2026-10-15): the words are matched here.
    assert [hit["name"] for hit in data(tools, "get_my_foods", phrase="CLASSIC owsianka")] == ["Owsianka classic", "K Classic Owsianka"]
    assert [hit["name"] for hit in data(tools, "get_my_foods", phrase="classic", limit=1)] == ["Owsianka classic"]


def test_create_recipe_checks_every_ingredient_and_keeps_the_recipe_private(tools: FitatuTools, fake: FakeFitatuServer) -> None:
    created = data(tools, "create_recipe", **ARGUMENTS["create_recipe"])
    post = next(r for r in fake.requests if r["method"] == "POST")
    assert post["path"] == "/recipes" and post["json_data"]["shared"] is False and post["json_data"]["serving"] == "2"
    assert post["json_data"]["items"] == [{"type": "PRODUCT", "itemId": PRODUCT_ID, "measureId": 1, "measureQuantity": 70}]
    # one serving as add_food logs the recipe: its portion measure (the recorded recipe's 248 g)
    assert created == {
        "recipe_id": MY_RECIPE_ID,
        "name": "Owsianka z jajkiem",
        "servings": 2,
        "per_serving": {"measure_id": 39, "name": "portion", "weight_g": 248, "capacity_ml": None, "energy_kcal": 125.91,
                        "protein_g": 6.66, "fat_g": 2.85, "carbohydrate_g": 18.6},
        "ok": True,
    }
    fake.requests.clear()
    wrong = {**ARGUMENTS["create_recipe"]["ingredients"][0], "unit": None, "measure_id": 46}
    error = tools.call("create_recipe", {**ARGUMENTS["create_recipe"], "ingredients": [wrong]})["error"]
    assert error["error_type"] == "validation" and error["message"].startswith(f"ingredients[0]: PRODUCT {PRODUCT_ID} has no measure_id 46")
    assert {r["method"] for r in fake.requests} == {"GET"}  # nothing created


def test_a_created_recipe_that_cannot_be_read_back_is_still_reported_created(tools: FitatuTools, fake: FakeFitatuServer) -> None:
    def server(method: str, path: str, **kwargs: Any) -> Any:
        if path == f"/recipes/{MY_RECIPE_ID}":
            raise FitatuConnectionError("read timeout")
        return fake.request(method, path, **kwargs)

    tools.client.request = server  # type: ignore[method-assign]
    created = data(tools, "create_recipe", **ARGUMENTS["create_recipe"])
    assert (created["recipe_id"], created["per_serving"], created["ok"]) == (MY_RECIPE_ID, None, True)


@pytest.mark.parametrize(("tool", "post"), [("create_recipe", "/recipes"), ("create_food", "/products")])
@pytest.mark.parametrize(
    ("failure", "retryable"),
    [
        (FitatuConnectionError("read timeout"), False),
        (FitatuApiError("POST -> 502", status_code=502), False),
        (FitatuRateLimitError("POST -> 429", status_code=429, retry_after=5), True),  # refused: nothing created
    ],
    ids=["network", "server", "rate_limit"],
)
def test_a_create_that_may_have_made_the_food_is_not_retryable(
    tools: FitatuTools, fake: FakeFitatuServer, failure: FitatuApiError, retryable: bool, tool: str, post: str
) -> None:
    # The POST is sent once: after a timeout or a 5xx the food may exist, and repeating it would make a second one.
    def server(method: str, path: str, **kwargs: Any) -> Any:
        if (method, path) == ("POST", post):
            raise failure
        return fake.request(method, path, **kwargs)

    tools.client.request = server  # type: ignore[method-assign]
    error = tools.call(tool, ARGUMENTS[tool])["error"]
    assert error["retryable"] is retryable and ("get_my_foods" in (error["hint"] or "")) is not retryable


def test_delete_recipe_deletes_only_the_users_own_private_recipes(tools: FitatuTools, fake: FakeFitatuServer) -> None:
    assert data(tools, "delete_recipe", recipe_id=MY_RECIPE_ID) == {
        "recipe_id": MY_RECIPE_ID, "name": "Owsianka z jajkiem", "already_deleted": False, "ok": True
    }
    assert [(r["method"], r["path"]) for r in fake.requests] == [("GET", f"/recipes/{MY_RECIPE_ID}"), ("DELETE", f"/recipes/{MY_RECIPE_ID}")]
    # Deletes are soft: the recipe is still there, marked deleted, and deleting it again is done.
    fake.routes[("GET", f"/recipes/{MY_RECIPE_ID}")]["deleted"] = True
    fake.requests.clear()
    assert data(tools, "delete_recipe", recipe_id=MY_RECIPE_ID)["already_deleted"] is True
    assert [r["method"] for r in fake.requests] == ["GET"]
    # The recorded catalog recipe says private but is shared and someone else's; a shared one of the user's is refused too.
    fake.routes[("GET", f"/recipes/{MY_RECIPE_ID}")].update(deleted=False, shared=True)
    for recipe_id in (RECIPE_ID, MY_RECIPE_ID):
        error = tools.call("delete_recipe", {"recipe_id": recipe_id})["error"]
        assert error["error_type"] == "validation" and "not a private recipe of this user" in error["message"]
    assert "DELETE" not in {r["method"] for r in fake.requests}


def test_update_recipe_sends_the_whole_recipe_with_only_the_changes(tools: FitatuTools, fake: FakeFitatuServer) -> None:
    stored = recorded("recipe_details")  # what the fake serves for MY_RECIPE_ID, with the user's flags
    kept = [
        {"type": "PRODUCT", "itemId": item["itemId"], "measureId": item["measureId"], "measureQuantity": float(item["measureQuantity"])}
        for item in stored["items"]
    ]
    updated = data(tools, "update_recipe", recipe_id=MY_RECIPE_ID, name="Owsianka z kakao", servings=2)
    put = next(r for r in fake.requests if r["method"] == "PUT")
    assert put["path"] == f"/recipes/{MY_RECIPE_ID}" and put["json_data"] == {
        "name": "Owsianka z kakao",
        "categories": None,  # as the app sends them
        "cookingTime": stored["cookingTime"],
        "items": kept,
        "mealSchema": stored["mealSchema"],
        "preparationTime": None,
        "recipeDescription": stored["recipeDescription"],
        "serving": "2",
        "shared": False,
        "tags": [],
    }
    previous = {
        "name": "Owsianka z jajkiem",
        "servings": 1,
        "description": stored["recipeDescription"],
        "ingredients": [{"food_id": i["itemId"], "food_type": "PRODUCT", "quantity": i["measureQuantity"], "measure_id": i["measureId"]} for i in kept],
    }
    assert (updated["name"], updated["servings"], updated["previous"], updated["ok"]) == ("Owsianka z kakao", 2, previous, True)
    assert updated["per_serving"]["measure_id"] == 39


def test_update_recipe_checks_new_ingredients_and_refuses_other_recipes(tools: FitatuTools, fake: FakeFitatuServer) -> None:
    egg = {"food_id": PRODUCT_ID, "food_type": "PRODUCT", "quantity": 70, "unit": "g"}
    data(tools, "update_recipe", recipe_id=MY_RECIPE_ID, ingredients=[egg])
    assert [item["itemId"] for item in next(r for r in fake.requests if r["method"] == "PUT")["json_data"]["items"]] == [PRODUCT_ID]
    fake.requests.clear()
    error = tools.call("update_recipe", {"recipe_id": MY_RECIPE_ID, "ingredients": [{**egg, "unit": None, "measure_id": 999}]})["error"]
    assert error["error_type"] == "validation" and error["message"].startswith("ingredients[0]:")
    assert error_type(tools, "update_recipe", {"recipe_id": MY_RECIPE_ID}) == "validation"  # nothing to change
    assert "not a private recipe of this user" in tools.call("update_recipe", {"recipe_id": RECIPE_ID, "name": "x"})["error"]["message"]
    fake.routes[("GET", f"/recipes/{MY_RECIPE_ID}")]["deleted"] = True
    assert error_type(tools, "update_recipe", {"recipe_id": MY_RECIPE_ID, "name": "x"}) == "not_found"
    assert "PUT" not in {r["method"] for r in fake.requests}


def test_create_food_sends_the_label_per_100_g_with_a_gram_base(tools: FitatuTools, fake: FakeFitatuServer) -> None:
    created = data(tools, "create_food", **ARGUMENTS["create_food"])
    post = next(r for r in fake.requests if r["method"] == "POST")
    assert (post["path"], post["json_data"]) == ("/products", {
        "name": "Baton owsiany", "energy": 420, "protein": 9, "fat": 18, "carbohydrate": 55,  # no brand key without one
        "measures": [GRAM_BASE, {"measureKey": "BAR", "measureUnit": "g", "weight": 45}],
    })
    # its measures read back, for add_food
    measures = data(tools, "get_food_measures", food_id=MY_PRODUCT_ID, food_type="PRODUCT")["measures"]
    assert created == {"food_id": MY_PRODUCT_ID, "name": "Baton owsiany", "measures": measures, "ok": True}
    assert [m["measure_id"] for m in measures] == [54, 1]
    fake.requests.clear()
    data(tools, "create_food", **ARGUMENTS["create_food"], brand="Domowy", sugars_g=20, barcode="2900000000018", unit="ml")
    body = fake.requests[0]["json_data"]
    assert (body["brand"], body["sugars"], body["barcode"]) == ("Domowy", 20, "2900000000018")
    assert [(m["measureKey"], m["measureUnit"]) for m in body["measures"]] == [("MILLILITER", "ml"), ("BAR", "ml")]


def test_a_created_product_that_cannot_be_read_back_is_still_reported_created(tools: FitatuTools, fake: FakeFitatuServer) -> None:
    def server(method: str, path: str, **kwargs: Any) -> Any:
        if path == f"/products/{MY_PRODUCT_ID}":
            raise FitatuConnectionError("read timeout")
        return fake.request(method, path, **kwargs)

    tools.client.request = server  # type: ignore[method-assign]
    created = data(tools, "create_food", **ARGUMENTS["create_food"])
    assert (created["food_id"], created["measures"], created["ok"]) == (MY_PRODUCT_ID, None, True)


@pytest.mark.parametrize(
    ("change", "message"),
    [
        ({"protein_g": 30, "fat_g": 20, "carbohydrate_g": 51}, "add up to more than 100 g in 100 g"),
        ({"sugars_g": 56}, "sugars_g is part of carbohydrate_g"),
        ({"saturated_fat_g": 19}, "saturated_fat_g is part of fat_g"),
        ({"energy_kcal": 901}, "energy_kcal must be at most 900"),  # most likely kJ
        ({"brand": "x" * 256}, "brand must be at most 255 characters"),  # Fitatu answers it with a 500
        ({"name": " "}, "name must not be empty"),
        ({"servings": [{"name": "bar", "weight": 45}, {"name": "bar", "weight": 50}]}, "servings names bar twice"),
        ({"servings": [{"name": "bar", "weight": 0}]}, "servings[0]: weight must be more than 0"),
        ({"servings": [{"name": "tablespoon", "weight": 10}]}, "servings[0]: name must be one of"),
        ({"barcode": "12ab"}, "barcode must be the 8 to 14 digits"),
    ],
)
def test_create_food_refuses_labels_fitatu_would_take(tools: FitatuTools, fake: FakeFitatuServer, change: dict[str, Any], message: str) -> None:
    error = tools.call("create_food", {**ARGUMENTS["create_food"], **change})["error"]
    assert error["error_type"] == "validation" and message in error["message"]
    assert fake.requests == []


def test_update_food_sends_the_whole_product_with_only_the_changes(tools: FitatuTools, fake: FakeFitatuServer) -> None:
    stored = fake.routes[("GET", f"/products/{MY_PRODUCT_ID}")]
    stored["currentUserPrivateBarcode"] = "2900000000018"
    updated = data(tools, "update_food", food_id=MY_PRODUCT_ID, energy_kcal=410)
    put = next(r for r in fake.requests if r["method"] == "PUT")
    # Fitatu clears what a PUT leaves out (checked live): the rest goes back as read, cholesterol included.
    assert put["path"] == f"/products/{MY_PRODUCT_ID}" and put["json_data"] == {
        **{key: stored[key] for key in _PRODUCT_FORM},
        "energy": 410,
        "measures": [GRAM_BASE, {"measureKey": "BAR", "measureUnit": "g", "weight": 45}],
        "barcode": "2900000000018",
    }
    previous = {
        "name": "Baton owsiany", "energy_kcal": 420, "protein_g": 9, "fat_g": 18, "carbohydrate_g": 55, "sugars_g": 20, "fiber_g": 6,
        "salt_g": 0.2, "saturated_fat_g": 2, "servings": [{"name": "bar", "weight": 45}], "barcode": "2900000000018",
    }
    assert (updated["food_id"], updated["replaced_food_id"], updated["previous"], updated["ok"]) == (MY_PRODUCT_ID, None, previous, True)
    assert [m["measure_id"] for m in updated["measures"]] == [54, 1]
    fake.requests.clear()
    data(tools, "update_food", food_id=MY_PRODUCT_ID, servings=[])  # servings replaces the list
    assert next(r for r in fake.requests if r["method"] == "PUT")["json_data"]["measures"] == [GRAM_BASE]


def test_update_food_reports_a_replaced_product(tools: FitatuTools, fake: FakeFitatuServer) -> None:
    # e.g. a rename: Fitatu keeps the change as a new product and retires the old one (seen live)
    new_id = MY_PRODUCT_ID + 1
    fake.routes[("PUT", f"/products/{MY_PRODUCT_ID}")] = {"id": new_id, "name": "Baton owsiany", "newProductCreated": True}
    fake.routes[("GET", f"/products/{new_id}")] = {**fake.routes[("GET", f"/products/{MY_PRODUCT_ID}")], "id": new_id, "energy": 410}
    updated = data(tools, "update_food", food_id=MY_PRODUCT_ID, energy_kcal=410)
    assert (updated["food_id"], updated["replaced_food_id"]) == (new_id, MY_PRODUCT_ID)
    assert fake.requests[-1]["path"] == f"/products/{new_id}"  # the measures are the new product's


def test_update_and_delete_food_keep_a_drink_in_ml(tools: FitatuTools, fake: FakeFitatuServer) -> None:
    fake.routes[("GET", f"/products/{MY_PRODUCT_ID}")]["liquid"] = True
    data(tools, "update_food", food_id=MY_PRODUCT_ID, energy_kcal=410)
    measures = next(r for r in fake.requests if r["method"] == "PUT")["json_data"]["measures"]
    assert measures == [{"measureKey": "MILLILITER", "measureUnit": "ml", "weight": 100}, {"measureKey": "BAR", "measureUnit": "ml", "weight": 45}]
    assert data(tools, "delete_food", food_id=MY_PRODUCT_ID)["previous"]["unit"] == "ml"


def test_update_and_delete_food_refuse_other_products(tools: FitatuTools, fake: FakeFitatuServer) -> None:
    for name, arguments in (("update_food", {"food_id": PRODUCT_ID, "name": "x"}), ("delete_food", {"food_id": PRODUCT_ID})):
        error = tools.call(name, arguments)["error"]  # a catalog product: private false, userId 0
        assert error["error_type"] == "validation" and "not a product this user created" in error["message"], name
    stored = fake.routes[("GET", f"/products/{MY_PRODUCT_ID}")]
    stored["userId"] = int(RECORDED_USER_ID) + 1  # another user's private product
    assert error_type(tools, "delete_food", {"food_id": MY_PRODUCT_ID}) == "validation"
    assert error_type(tools, "update_food", {"food_id": MY_PRODUCT_ID}) == "validation"  # nothing to change
    stored |= {"userId": int(RECORDED_USER_ID), "deleted": True}
    assert error_type(tools, "update_food", {"food_id": MY_PRODUCT_ID, "name": "x"}) == "not_found"
    assert data(tools, "delete_food", food_id=MY_PRODUCT_ID)["already_deleted"] is True
    # a measure update_food cannot send back, which a PUT would drop
    stored |= {"deleted": False, "measures": [*stored["measures"], {"id": 52, "name": "tablespoon", "energyPerUnit": 42, "weightPerUnit": 10}]}
    error = tools.call("update_food", {"food_id": MY_PRODUCT_ID, "name": "x"})["error"]
    assert error["error_type"] == "validation" and "(tablespoon)" in error["message"]
    assert {r["method"] for r in fake.requests} == {"GET"}
    deleted = data(tools, "delete_food", food_id=MY_PRODUCT_ID)  # deleting it leaves the measure out of previous
    assert deleted["already_deleted"] is False and deleted["previous"]["servings"] == [{"name": "bar", "weight": 45}]


def test_delete_food_previous_makes_it_again(tools: FitatuTools, fake: FakeFitatuServer) -> None:
    deleted = data(tools, "delete_food", food_id=MY_PRODUCT_ID)
    assert [(r["method"], r["path"]) for r in fake.requests] == [("GET", f"/products/{MY_PRODUCT_ID}"), ("DELETE", f"/products/{MY_PRODUCT_ID}")]
    assert (deleted["food_id"], deleted["name"], deleted["already_deleted"], deleted["ok"]) == (MY_PRODUCT_ID, "Baton owsiany", False, True)
    fake.requests.clear()
    data(tools, "create_food", **deleted["previous"])
    assert fake.requests[0]["json_data"] == {
        "name": "Baton owsiany", "energy": 420, "protein": 9, "fat": 18, "carbohydrate": 55, "sugars": 20, "fiber": 6, "salt": 0.2,
        "saturatedFat": 2, "measures": [GRAM_BASE, {"measureKey": "BAR", "measureUnit": "g", "weight": 45}],
    }


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
        ("get_progress", {"from_date": NEXT_DAY, "to_date": DAY}, "validation"),
        ("get_report", {"from_date": NEXT_DAY, "to_date": DAY}, "validation"),
        ("get_top_foods", {"from_date": NEXT_DAY, "to_date": DAY}, "validation"),
        ("get_top_foods", {"from_date": DAY, "to_date": DAY, "nutrient": "kcal"}, "validation"),
        ("copy_meal", {**ARGUMENTS["copy_meal"], "to_meal": "Obiad"}, "validation"),
        ("copy_meal", {**ARGUMENTS["copy_meal"], "row_ids": []}, "validation"),
        ("copy_meal", {**ARGUMENTS["copy_meal"], "row_id": ROWS["lunch"].replace("-11f1-", "-41f1-")}, "validation"),  # a UUID v4
        ("search_food", {"phrase": "jajko", "limit": 0}, "validation"),
        ("search_food", {"phrase": "jajko", "limit": 51}, "validation"),
        ("get_weight_history", {"limit": 42}, "validation"),
        ("get_measurement_history", {"body_part": "knee"}, "validation"),
        ("get_measurement_history", {"body_part": "fatPercentage"}, "validation"),
        ("get_measurement_history", {"body_part": "waist", "limit": 42}, "validation"),
        ("search_food", {"phrase": "jajko", "locale": "pl"}, "validation"),
        ("get_progress", {"from_date": "2027-01-01", "to_date": "2027-03-04"}, "validation"),  # 63 days
        ("preview_removal", {"date": DAY, "row_ids": []}, "validation"),
        ("search_food", {"phrase": "jajko", "limit": True}, "validation"),
        ("get_food_measures", {"food_id": PRODUCT_ID, "food_type": "MEAL"}, "validation"),
        ("find_by_barcode", {"barcode": "590 1234 123457"}, "validation"),
        ("find_by_barcode", {"barcode": "1234567"}, "validation"),
        ("find_by_barcode", {"barcode": "\uff15" * 13}, "validation"),  # fullwidth digits
        ("create_recipe", {**ARGUMENTS["create_recipe"], "ingredients": []}, "validation"),
        # only products: a recipe id would name an unrelated product
        ("create_recipe", {**ARGUMENTS["create_recipe"], "ingredients": [{"food_id": RECIPE_ID, "food_type": "RECIPE", "quantity": 1}]}, "validation"),
        ("create_recipe", {**ARGUMENTS["create_recipe"], "servings": 0}, "validation"),
        ("delete_recipe", {"recipe_id": 0}, "validation"),
        ("add_food", {**ARGUMENTS["add_food"], "meal": "midnight_snack"}, "validation"),
        ("add_food", {**ARGUMENTS["add_food"], "quantity": "70"}, "validation"),
        ("add_food", {**ARGUMENTS["add_food"], "quantity": 0}, "validation"),
        ("add_food", {**ARGUMENTS["add_food"], "unit": None}, "validation"),
        ("add_food", {**ARGUMENTS["add_food"], "measure_id": 1}, "validation"),
        ("add_food", {**ARGUMENTS["add_food"], "food_id": RECIPE_ID, "food_type": "RECIPE"}, "validation"),
        ("add_foods", {**ARGUMENTS["add_foods"], "items": []}, "validation"),
        ("add_foods", {**ARGUMENTS["add_foods"], "items": ["jajko"]}, "validation"),
        ("add_foods", {**ARGUMENTS["add_foods"], "items": [{"food_id": PRODUCT_ID, "food_type": "PRODUCT"}]}, "validation"),
        ("add_foods", {**ARGUMENTS["add_foods"], "items": [{**ARGUMENTS["add_food"], "quantity": 1}]}, "validation"),  # date, meal
        ("update_item", {"date": DAY, "row_id": ROWS["breakfast"]}, "validation"),
        ("remove_items", {"date": DAY, "row_ids": ROWS["lunch"]}, "validation"),
        # a remove_items that reached the planner without row_ids would empty the whole day
        ("remove_items", {"date": DAY}, "validation"),
        ("remove_items", {"date": DAY, "row_ids": None}, "validation"),
        ("remove_items", {"date": DAY, "row_ids": []}, "validation"),
        ("remove_items", {"date": DAY, "row_ids": [None]}, "validation"),
        ("remove_items", {"date": DAY, "row_ids": [ROWS["lunch"], 123]}, "validation"),
        ("add_food", {"date": DAY, "meal": "lunch", "food_id": -5, "food_type": "PRODUCT", "quantity": 1, "measure_id": 39}, "validation"),
        ("add_food", {"date": DAY, "meal": "lunch", "food_id": 0, "food_type": "RECIPE", "quantity": 1}, "validation"),
        ("add_food", {"date": DAY, "meal": "lunch", "food_id": PRODUCT_ID, "food_type": "PRODUCT", "quantity": 1, "measure_id": -1}, "validation"),
        ("add_activity", {**ARGUMENTS["add_activity"], "activity_id": 0}, "validation"),
        ("add_activity", {**ARGUMENTS["add_activity"], "energy_kcal": 12.5}, "validation"),
        ("set_water", {"date": DAY, "water_ml": True}, "validation"),
        ("log_measurements", {"date": WEIGHT_DAY}, "validation"),
        ("log_measurements", {"date": WEIGHT_DAY, "waist": 0}, "validation"),
        ("log_measurements", {"date": WEIGHT_DAY, "weight": 70}, "validation"),
        ("set_targets", {}, "validation"),
        ("set_targets", {"automatic": False}, "validation"),
        ("set_targets", {"automatic": True, "energy_kcal": 1800}, "validation"),
        ("set_targets", {"protein_pct": 40, "fat_pct": 30}, "validation"),
        ("set_targets", {"protein_pct": 30, "fat_pct": 30, "carbohydrate_pct": 30}, "validation"),  # sums to 90
        ("set_targets", {"protein_pct": 40.5, "fat_pct": 29.5, "carbohydrate_pct": 30}, "validation"),  # Fitatu answers 500
        ("set_targets", {"protein_pct": 0, "fat_pct": 40, "carbohydrate_pct": 60}, "validation"),  # 0 g is refused
        ("set_targets", {"energy_kcal": 499}, "validation"),
        ("set_targets", {"energy_kcal": 8001}, "validation"),
        ("set_targets", {"energy_kcal": "1800"}, "validation"),
        ("set_targets", {"water_glass_ml": 2}, "validation"),
    ],
)
def test_bad_calls_are_validation_errors_and_send_nothing(
    tools: FitatuTools, fake: FakeFitatuServer, name: str, arguments: Any, expected: str
) -> None:
    assert error_type(tools, name, arguments) == expected
    assert fake.requests == []


def test_argument_errors_send_the_model_to_the_tool_that_has_the_answer() -> None:
    tools = _renamed_meals_tools(without="supper")
    add = {**ARGUMENTS["add_custom_item"], "meal": "Obiad"}
    assert "match the user's words against get_day's meal_name" in tools.call("add_custom_item", add)["error"]["message"]
    for date in ("today", "03.01.2027"):
        error = tools.call("get_day", {"date": date})["error"]
        assert error["error_type"] == "validation" and "call get_today" in error["message"], date
    assert "call get_today" in tools.call("move_item", {"date": DAY, "row_id": ROWS["snack"], "to_meal": "dinner", "to_date": "tomorrow"})["error"]["message"]
    # a meal key the user's plan does not have: the day's meals with the names the user knows
    missing = tools.call("add_custom_item", {**add, "meal": "supper"})["error"]
    assert missing["error_type"] == "not_found" and "dinner (Obiad)" in missing["message"] and "snack (Kolacja)" in missing["message"]


def test_remove_items_called_directly_never_takes_the_whole_day(tools: FitatuTools, fake: FakeFitatuServer) -> None:
    for row_ids in (None, []):
        with pytest.raises(FitatuValidationError, match="at least one row_id"):
            tools.remove_items(DAY, row_ids)  # type: ignore[arg-type]
    assert fake.requests == []


def test_whole_number_floats_count_as_integers(tools: FitatuTools, fake: FakeFitatuServer) -> None:
    # JSON Schema accepts 1500.0 for an integer, and json.loads gives a float for it.
    assert data(tools, "set_water", date=DAY, water_ml=1500.0)["water_ml"] == 1500
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


@pytest.mark.parametrize(
    ("subscriptions", "hint"),
    [
        ({"subscriptions": []}, "A premium feature: the account has no active premium plan"),
        ({"subscriptions": [{"startDate": "2020-01-01", "endDate": "2099-01-31"}]}, "Not a premium matter, as the account has premium"),
        ([], "Premium-only feature, used-up AI quota"),  # not an object, so the plan is unknown: describe_error's hint stays
    ],
)
def test_a_403_says_whether_the_account_has_premium(tools: FitatuTools, fake: FakeFitatuServer, subscriptions: Any, hint: str) -> None:
    fake.routes[("GET", f"/users/{RECORDED_USER_ID}/subscriptions")] = subscriptions
    forbidden = FitatuApiError("GET /search/catalog/recipes -> 403", status_code=403, body='{"message": "Forbidden"}')
    with patch.object(tools.client, "search_recipes", side_effect=forbidden):
        error = tools.call("search_recipes", {"phrase": "kurczak"})["error"]
    assert (error["error_type"], error["retryable"]) == ("forbidden", False) and error["hint"].startswith(hint)
    firewall = FitatuApiError("GET /search/catalog/recipes -> 403", status_code=403, body="<html>blocked</html>")
    fake.requests.clear()
    with patch.object(tools.client, "search_recipes", side_effect=firewall):
        assert tools.call("search_recipes", {})["error"]["hint"].startswith("A firewall") and fake.requests == []


def test_call_reports_an_unexpected_error_instead_of_raising(tools: FitatuTools, caplog: pytest.LogCaptureFixture) -> None:
    tools.client.request = lambda *args, **kwargs: 1 / 0  # type: ignore[method-assign]
    with caplog.at_level(logging.DEBUG, logger="fitatu_api"):
        assert error_type(tools, "get_day", {"date": DAY}) == "unexpected"
        assert error_type(tools, "get_day", {"date": "today"}) == "validation"
    # A bug is a warning with its traceback; a model's mistake only a debug line.
    bug, mistake = ((r.levelname, r.getMessage(), r.exc_info is not None) for r in caplog.records)
    assert bug == ("WARNING", "get_day failed (unexpected): division by zero", True)
    assert mistake[0] == "DEBUG" and mistake[1].startswith("get_day failed (validation)") and not mistake[2]


def test_a_session_without_user_id_is_an_auth_error() -> None:
    tools = FitatuTools(FitatuApiClient(FitatuAuthContext(bearer_token="t")))
    assert error_type(tools, "get_day", {"date": DAY}) == "auth"


def test_from_session_and_context_manager(tmp_path: Path) -> None:
    path = tmp_path / "session.json"
    path.write_text(json.dumps({"bearer_token": "t", "fitatu_user_id": RECORDED_USER_ID}), encoding="utf-8")
    with FitatuTools.from_session(path) as tools:
        assert tools.client.auth.fitatu_user_id == RECORDED_USER_ID
        assert tools.client.token_store is not None and tools.client.token_store.path == path
    with FitatuTools.from_session(path, tools=["get_today"]) as tools:
        assert tools.call("get_today")["ok"] and error_type(tools, "get_day", {"date": DAY}) == "validation"
