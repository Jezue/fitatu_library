"""FitatuTools.demo() and fitatu-mcp --demo: every tool works on the made-up diary, and nothing leaves the process."""

from __future__ import annotations

import datetime as dt
import json
import sys
import uuid
from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest

from fitatu_api import FitatuTools
from fitatu_api.agent import TOOLS

DATA = Path(__file__).resolve().parents[1] / "src" / "fitatu_api" / "demo_data.json"


def demo_arguments(tools: FitatuTools) -> dict[str, dict[str, Any]]:
    """Arguments that work on a fresh demo, for every tool."""
    today = dt.date.fromisoformat(tools.get_today()["date"])
    day, yesterday, month_ago = today.isoformat(), (today - dt.timedelta(days=1)).isoformat(), (today - dt.timedelta(days=30)).isoformat()
    row = tools.get_day(day)["meals"][0]["items"][0]["row_id"]
    activity = tools.get_day(yesterday)["activities"][0]["row_id"]
    return {
        "get_today": {},
        "get_day": {"date": day},
        "get_targets": {"date": day},
        "get_profile": {},
        "get_range_summary": {"from_date": yesterday, "to_date": day},
        "get_progress": {"from_date": yesterday, "to_date": day},
        "get_top_foods": {"from_date": yesterday, "to_date": day, "nutrient": "protein"},
        "get_report": {"from_date": month_ago, "to_date": day},
        "get_shopping_list": {"from_date": day, "to_date": day},
        "search_food": {"phrase": "jajka"},
        "search_recipes": {"max_energy_kcal": 400, "meal": "breakfast", "max_cooking_time": 10},
        "get_recipe": {"recipe_id": 3000001},
        "find_by_barcode": {"barcode": "5900000000017"},
        "get_food_measures": {"food_id": 3000001, "food_type": "RECIPE"},
        "get_recent_foods": {"meal": "breakfast"},
        "get_favorite_foods": {},
        "get_my_foods": {},
        "get_weight_history": {"from_date": month_ago, "to_date": day},
        "get_body_measurements": {"from_date": month_ago, "to_date": day},
        "get_measurement_history": {"body_part": "waist"},
        "get_water": {"date": day},
        "search_activity": {"phrase": "bieganie"},
        "get_fasting": {"from_date": yesterday, "to_date": day},
        "get_habits": {},
        "get_good_days": {},
        "preview_removal": {"date": day, "row_ids": [row]},
        "add_food": {"date": day, "meal": "snack", "food_id": 2000004, "food_type": "PRODUCT", "quantity": 1, "measure_id": 46, "eaten": True},
        "add_foods": {
            "date": day,
            "meal": "second_breakfast",
            "items": [{"food_id": 2000008, "food_type": "PRODUCT", "quantity": 150, "unit": "g"}, {"food_id": 3000001, "food_type": "RECIPE", "quantity": 1}],
        },
        "add_custom_item": {"date": day, "meal": "supper", "name": "Baton", "energy_kcal": 200},
        "update_item": {"date": day, "row_id": row, "quantity": 3},
        "move_item": {"date": day, "row_id": row, "to_meal": "lunch"},
        "copy_meal": {"from_date": yesterday, "from_meal": "breakfast", "to_date": day},
        "remove_items": {"date": day, "row_ids": [row]},
        "log_weight": {"date": day, "weight_kg": 80.2},
        "log_measurements": {"date": day, "waist": 89.5},
        "set_water": {"date": day, "water_ml": 1000},
        "set_targets": {"energy_kcal": 1800, "protein_pct": 30, "fat_pct": 30, "carbohydrate_pct": 40, "water_glass_ml": 300},
        "set_note": {"date": day, "note": "Trening rano"},
        "add_activity": {"date": day, "activity_id": 4000003, "minutes": 45, "energy_kcal": 110},
        "remove_activity": {"date": yesterday, "row_id": activity},
        "set_favorite": {"food_id": 2000004, "food_type": "PRODUCT"},
        "create_recipe": {"name": "Jajecznica", "ingredients": [{"food_id": 2000001, "food_type": "PRODUCT", "quantity": 3, "measure_id": 46}]},
        "update_recipe": {"recipe_id": 3000003, "name": "Koktajl", "servings": 2},
        "delete_recipe": {"recipe_id": 3000003},
        "create_food": {"name": "Baton", "energy_kcal": 420, "protein_g": 9, "fat_g": 18, "carbohydrate_g": 55, "servings": [{"name": "bar", "weight": 45}]},
        "update_food": {"food_id": 2000015, "energy_kcal": 410},
        "delete_food": {"food_id": 2000015},
    }


def test_every_tool_works_on_the_demo_and_nothing_is_sent() -> None:
    names = set()
    with patch("fitatu_api.client.requests.Session.request") as http:
        for name in demo_arguments(FitatuTools.demo()):
            tools = FitatuTools.demo()  # a fresh diary for each tool, and its own row ids
            result = tools.call(name, demo_arguments(tools)[name])
            assert result["ok"] and (not isinstance(result["data"], dict) or result["data"].get("ok", True)), (name, result)
            assert result["data"], name
            names.add(name)
    http.assert_not_called()
    assert names == {tool["name"] for tool in TOOLS}


def test_writes_stay_in_one_demo_and_the_numbers_follow_the_catalog() -> None:
    tools, other = FitatuTools.demo(), FitatuTools.demo()
    day = tools.get_today()["date"]
    before = tools.get_day(day)["totals"]["energy"]
    added = tools.call("add_food", {"date": day, "meal": "snack", "food_id": 2000004, "food_type": "PRODUCT", "quantity": 1, "measure_id": 46})
    assert added["ok"] and added["data"]["measure_name"] == "piece"
    assert tools.get_day(day)["totals"]["energy"] == round(before + 97 * 120 / 100, 2)  # one 120 g banana
    assert other.get_day(day)["totals"]["energy"] == before
    snack = next(meal for meal in tools.get_day(day)["meals"] if meal["meal_key"] == "snack")
    assert snack["meal_name"] == "Kolacja"  # renamed slots, as users have them
    assert tools.call("get_progress", {"from_date": day, "to_date": day})["data"]["days"][0]["energy"]["logged"] == tools.get_day(day)["totals"]["energy"]


def test_goals_set_in_the_demo_show_in_its_targets_progress_and_water() -> None:
    tools = FitatuTools.demo()
    day = tools.get_today()["date"]
    assert tools.set_targets(energy_kcal=1800, protein_pct=30, fat_pct=30, carbohydrate_pct=40, water_glass_ml=300)["ok"]
    targets = tools.get_targets(day)
    assert targets["energy"] == 1800 and targets["protein"] == {"grams": 135, "percent": 30}
    assert tools.get_progress(day, day)["days"][0]["energy"]["target"] == 1800
    assert tools.get_water(day)["glass_ml"] == 300
    back = tools.set_targets(automatic=True)
    assert back["ok"] and back["automatic"] and back["energy"] == 2000


def test_the_demo_profile_follows_a_weigh_in_for_today_and_the_sizes_come_newest_first() -> None:
    tools = FitatuTools.demo()
    assert [entry["value"] for entry in tools.get_measurement_history("waist")] == [90, 92]
    profile = tools.get_profile()
    assert profile["weight_kg"] == 80.5 and profile["goal"] == "lose" and profile["premium"]["active"]
    tools.log_weight(tools.get_today()["date"], 79.9)
    assert tools.get_profile()["weight_kg"] == 79.9


def test_the_demo_recipe_search_filters_and_get_recipe_tells_the_users_own() -> None:
    tools = FitatuTools.demo()
    assert [hit["name"] for hit in tools.search_recipes(diets=["high_protein"], meal="dinner")] == ["Spaghetti bolognese"]
    assert tools.search_recipes(diets=["vegan"]) == [] and tools.search_recipes(max_cooking_time=10, min_protein_g=20) == []
    assert tools.get_recipe(3000003)["own"] is True and tools.get_recipe(3000001)["own"] is False


def test_a_product_created_in_the_demo_logs_by_its_serving_and_is_one_of_the_users_foods() -> None:
    tools = FitatuTools.demo()
    day = tools.get_today()["date"]
    created = tools.create_food("Baton proteinowy", 380, 30, 12, 35, servings=[{"name": "bar", "weight": 50}], barcode="2900000000018")
    assert [measure["measure_id"] for measure in created["measures"]] == [1, 54]
    added = tools.add_food(day, "snack", created["food_id"], "PRODUCT", 1, measure_id=54)
    assert added["measure_name"] == "bar" and created["food_id"] in {hit["food_id"] for hit in tools.get_my_foods("proteinowy")}
    assert tools.find_by_barcode("2900000000018")["food_id"] == created["food_id"]
    assert tools.delete_food(created["food_id"])["previous"]["servings"] == [{"name": "bar", "weight": 50}]
    assert tools.get_my_foods("proteinowy") == []


def test_the_demo_refuses_what_fitatu_would_and_names_what_it_does_not_serve() -> None:
    tools = FitatuTools.demo()
    assert tools.call("delete_recipe", {"recipe_id": 3000001})["error"]["error_type"] == "validation"  # a catalog recipe
    assert tools.call("delete_food", {"food_id": 2000004})["error"]["error_type"] == "validation"  # a catalog product
    assert tools.call("get_food_measures", {"food_id": 1, "food_type": "PRODUCT"})["error"]["error_type"] == "not_found"
    error = tools.call("add_food", {"date": "2027-01-03", "meal": "snack", "food_id": 1, "food_type": "RECIPE", "quantity": 1})["error"]
    assert error["error_type"] == "sync_rejected"
    with pytest.raises(Exception, match="the demo does not serve"):
        tools.client.request("GET", "/users/1000001/measurements/chart/weight")


def test_the_demo_data_is_small_and_made_up() -> None:
    assert DATA.stat().st_size < 8000
    data = json.loads(DATA.read_text(encoding="utf-8"))
    assert data["user_id"] == "1000001" and all(len(code) == 13 for code in (p.get("barcode", "0" * 13) for p in data["products"].values()))
    # The row ids reach the model provider: like the planner's, they carry no MAC address.
    tools = FitatuTools.demo()
    day = tools.get_day(tools.get_today()["date"])
    ids = [uuid.UUID(row["row_id"]) for meal in day["meals"] for row in meal["items"]]
    assert ids and uuid.getnode() not in {row_id.node for row_id in ids}


def test_fitatu_mcp_demo_serves_the_made_up_diary_over_stdio(tmp_path: Path) -> None:
    pytest.importorskip("mcp")
    import anyio
    from mcp import Client, StdioServerParameters

    from fitatu_api.mcp_server import DEMO_NOTE

    env = {"FITATU_SESSION": str(tmp_path / "missing.json"), "PYTHONPATH": str(Path(__file__).resolve().parents[1] / "src")}
    server = StdioServerParameters(command=sys.executable, args=["-m", "fitatu_api.mcp_server", "--demo"], env=env)

    async def run() -> tuple[Any, Any]:
        async with Client(server) as session:
            today = (await session.call_tool("get_today", {})).structured_content["data"]["date"]
            return session.instructions, (await session.call_tool("get_day", {"date": today})).structured_content

    instructions, day = anyio.run(run)
    assert instructions.endswith(DEMO_NOTE)
    assert day["ok"] and day["data"]["meals"][0]["meal_name"] == "Śniadanie"


def test_every_demo_result_fits_its_output_schema() -> None:
    pytest.importorskip("mcp")
    import anyio
    from mcp import Client

    from fitatu_api.mcp_server import build_server

    async def run() -> None:
        for name in demo_arguments(FitatuTools.demo()):
            tools = FitatuTools.demo()
            # the SDK client raises when a result does not fit the tool's outputSchema
            async with Client(build_server(lambda: tools, demo=True)) as session:  # noqa: B023 - used before the next loop
                assert not (await session.call_tool(name, demo_arguments(tools)[name])).is_error, name

    anyio.run(run)
