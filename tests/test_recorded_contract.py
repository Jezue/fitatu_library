"""Tests against real API responses (tests/fixtures/api_recording.json).

Two jobs:

1. Replay the recorded day syncs through ``FakeFitatuServer`` and compare the fake's day
   with the day the real server returned after each step. If the fake drifts from the
   recording, every planner test built on it would be testing fiction.
2. Feed recorded responses to the library and check that the requests it builds match
   the ones that worked live.
"""

from __future__ import annotations

import importlib.util
from datetime import date
from pathlib import Path
from typing import Any
from unittest.mock import Mock, patch

import pytest
from fake_server import CALLS, RECORDED_USER_ID, FakeFitatuServer, recorded

from fitatu_api import (
    FitatuApiClient,
    FitatuApiError,
    FitatuAuthContext,
    FitatuLibrary,
    FitatuPlannerSyncError,
    FitatuValidationError,
)
from fitatu_api._summaries import day_summary
from fitatu_api.planner import PlannerModule
from fitatu_api.service_modules import ActivitiesModule, MeasurementsModule, SummaryModule

DAY = "2027-01-03"


def _rows(day: dict[str, Any]) -> dict[str, list[tuple[Any, ...]]]:
    """The parts of a day both sides must agree on: which rows and activities it has, and their values."""
    return {
        **{
            meal_key: sorted(
                (row["planDayDietItemId"], row["foodType"], row["eaten"], row["measureQuantity"])
                for row in bucket["items"]
            )
            for meal_key, bucket in day["dietPlan"].items()
        },
        "activityPlan": sorted(
            (row["uuid"], row["activityId"], row["energy"], [(m["measureId"], m["value"]) for m in row["simpleMeasures"]])
            for row in day["activityPlan"]
        ),
    }


REPLAY = [
    (["sync_add_product", "sync_add_recipe", "sync_add_custom"], "day_three_rows"),
    (["sync_update_product"], "day_after_update"),
    (["sync_row_without_eaten"], "day_after_row_without_eaten"),
    (["sync_omit_rows"], "day_after_omission"),
    (["sync_rejected"], "day_after_omission"),
    (["sync_delete_product"], "day_after_delete"),
    (["sync_cleanup"], "day_after_cleanup"),
    (["sync_add_search_recipe"], "day_after_search_recipe"),
    (["sync_add_activity"], "day_after_activity"),
    (["sync_delete_activity"], "day_after_activity_delete"),  # rejected: the read shape from the day GET
    (["sync_cleanup_recipe"], "day_after_cleanup_2"),
    (["sync_delete_activity_write_shape"], "day_after_activity_delete_write_shape"),
]


class TestFakeServerMatchesRecording:
    def test_replaying_recorded_syncs_gives_the_recorded_days(self) -> None:
        server = FakeFitatuServer(
            products={str(CALLS["product_details"]["body"]["id"]): recorded("product_details")},
            recipes={"590200": recorded("recipe_details")},
        )
        assert _rows(server.day_view(DAY)) == _rows(recorded("day_empty"))

        for sync_names, expected_day in REPLAY:
            for name in sync_names:
                request = CALLS[name]["request"]
                response = server.request("POST", request["path"], json_data=request["body"], params=request["query"])
                recorded_response = CALLS[name]["body"]
                assert [set(row) for row in response] == [set(row) for row in recorded_response], name
                assert [bool(row["errorMessage"]) for row in response] == [
                    bool(row["errorMessage"]) for row in recorded_response
                ], name
            assert _rows(server.day_view(DAY)) == _rows(recorded(expected_day)), expected_day

    def test_rejection_message_matches(self) -> None:
        server = FakeFitatuServer()
        request = CALLS["sync_rejected"]["request"]
        [row] = server.request("POST", request["path"], json_data=request["body"])
        [expected] = CALLS["sync_rejected"]["body"]
        assert row["errorMessage"] == expected["errorMessage"]
        assert CALLS["sync_rejected"]["status"] == 202

    def test_activity_rejection_message_matches(self) -> None:
        server = FakeFitatuServer()
        request = CALLS["sync_delete_activity"]["request"]
        [row] = server.request("POST", request["path"], json_data=request["body"])
        [expected] = CALLS["sync_delete_activity"]["body"]
        assert row["errorMessage"] == expected["errorMessage"]

    def test_day_get_keeps_every_recorded_top_level_key(self) -> None:
        assert set(FakeFitatuServer().day_view(DAY)) == set(recorded("day_empty"))

    def test_day_get_keeps_the_meal_names_and_times(self) -> None:
        assert FakeFitatuServer().day_view(DAY)["dietPlan"] == recorded("day_empty")["dietPlan"]


class TestPlannerWritesMatchWhatWorkedLive:
    def test_add_product_sends_only_the_new_row_with_the_recorded_keys(self) -> None:
        server = FakeFitatuServer()
        server.planner.add_product_to_day_meal(
            RECORDED_USER_ID, date.fromisoformat(DAY), meal_type="breakfast", product_id=15956231, measure_id=39
        )
        sent = server.sync_payloads[-1][DAY]["dietPlan"]
        recorded_row = CALLS["sync_add_product"]["request"]["body"][DAY]["dietPlan"]["breakfast"]["items"][0]
        assert list(sent) == ["breakfast"]
        [row] = sent["breakfast"]["items"]
        assert set(recorded_row) <= set(row)

    def test_add_recipe_needs_only_the_recipe_id(self) -> None:
        # Recorded: a recipe row with just recipeId came back with name and nutrition.
        recorded_lunch = recorded("day_three_rows")["dietPlan"]["lunch"]["items"][0]
        assert recorded_lunch["name"] and recorded_lunch["energy"]
        server = FakeFitatuServer()
        server.planner.add_recipe_to_day_meal(RECORDED_USER_ID, date.fromisoformat(DAY), meal_type="lunch", recipe_id=590200)
        [row] = server.sync_payloads[-1][DAY]["dietPlan"]["lunch"]["items"]
        assert row["recipeId"] == 590200
        assert "energy" not in row

    def test_a_recipe_search_hit_is_added_as_the_recorded_recipe_row(self) -> None:
        server = FakeFitatuServer()
        [hit] = server.search_food("owsianka", limit=1)
        assert (hit["type"], hit["foodId"]) == ("RECIPE", 60477557)

        server.planner.add_search_result_to_day_meal(RECORDED_USER_ID, DAY, meal_type="breakfast", phrase="owsianka")

        [row] = server.sync_payloads[-1][DAY]["dietPlan"]["breakfast"]["items"]
        [recorded_row] = CALLS["sync_add_search_recipe"]["request"]["body"][DAY]["dietPlan"]["breakfast"]["items"]
        assert set(recorded_row) <= set(row)
        assert {key: row[key] for key in recorded_row if key != "planDayDietItemId"} == {
            key: value for key, value in recorded_row.items() if key != "planDayDietItemId"
        }

    def test_a_recipe_search_hit_is_not_resolved_through_product_units(self) -> None:
        server = FakeFitatuServer()
        with pytest.raises(FitatuValidationError, match=r"recipe.*\(portion\)"):
            server.planner.add_search_result_to_day_meal(
                RECORDED_USER_ID, DAY, meal_type="breakfast", phrase="owsianka", measure_unit="g", measure_amount=200
            )
        assert server.sync_payloads == []

    def test_a_product_search_hit_is_still_added_by_product_id(self) -> None:
        server = FakeFitatuServer()
        hit = server.search_food("owsianka", limit=2)[1]
        assert hit["type"] == "PRODUCT"

        result = server.planner.add_search_result_to_day_meal(
            RECORDED_USER_ID, DAY, meal_type="breakfast", phrase="owsianka", index=1, measure_quantity=2
        )

        [row] = server.sync_payloads[-1][DAY]["dietPlan"]["breakfast"]["items"]
        assert (row["foodType"], row["productId"], row["measureId"], row["measureQuantity"]) == (
            "PRODUCT", hit["foodId"], hit["measure"]["measureId"], 2
        )
        assert "recipeId" not in row
        assert result["search_item"]["type"] == "PRODUCT"

    def test_add_activity_sends_the_recorded_write_shape(self) -> None:
        server = FakeFitatuServer()
        [recorded_row] = CALLS["sync_add_activity"]["request"]["body"][DAY]["activities"]

        result = server.planner.add_activity(RECORDED_USER_ID, DAY, activity_id=535, minutes=30, energy=123)

        sent = server.sync_payloads[-1][DAY]
        assert list(sent) == ["activities"]
        [row] = sent["activities"]
        assert list(row) == list(recorded_row)
        assert {**row, "uuid": recorded_row["uuid"]} == recorded_row
        assert result["ok"] is True
        [stored] = server.day_view(DAY)["activityPlan"]
        assert (stored["uuid"], stored["energy"]) == (result["row_id"], 123)

    def test_remove_activity_resends_it_in_the_write_shape_with_deleted_at(self) -> None:
        server = FakeFitatuServer(recorded("day_after_activity"))
        [recorded_row] = CALLS["sync_delete_activity_write_shape"]["request"]["body"][DAY]["activities"]

        result = server.planner.remove_activity(RECORDED_USER_ID, DAY, item_id=recorded_row["uuid"])

        [row] = server.sync_payloads[-1][DAY]["activities"]
        assert list(row) == list(recorded_row)
        assert row["deletedAt"]
        assert {**row, "deletedAt": recorded_row["deletedAt"]} == recorded_row
        assert result["ok"] is True
        assert server.day_view(DAY)["activityPlan"] == []

    def test_update_keeps_eaten_and_the_rest_of_the_row(self) -> None:
        server = FakeFitatuServer(recorded("day_after_update"))
        row_id = recorded("day_after_update")["dietPlan"]["breakfast"]["items"][0]["planDayDietItemId"]

        server.planner.update_day_item(
            RECORDED_USER_ID, date.fromisoformat(DAY), meal_type="breakfast", item_id=row_id, measure_quantity=3
        )

        [row] = server.day_view(DAY)["dietPlan"]["breakfast"]["items"]
        assert row["eaten"] is True
        assert row["measureQuantity"] == 3

    def test_remove_hides_the_row_and_leaves_other_meals_alone(self) -> None:
        server = FakeFitatuServer(recorded("day_three_rows"))
        row_id = recorded("day_three_rows")["dietPlan"]["breakfast"]["items"][0]["planDayDietItemId"]

        result = server.planner.remove_day_item(
            RECORDED_USER_ID, date.fromisoformat(DAY), meal_type="breakfast", item_id=row_id
        )

        assert result["ok"] is True
        day = server.day_view(DAY)
        assert day["dietPlan"]["breakfast"]["items"] == []
        assert len(day["dietPlan"]["lunch"]["items"]) == 1
        assert len(day["dietPlan"]["snack"]["items"]) == 1
        assert server.all_rows(DAY)[row_id]["deletedAt"]


class TestLibraryReadsRecordedResponses:
    def test_sync_days_raises_on_the_recorded_rejection(self) -> None:
        client = Mock()
        client.request.return_value = recorded("sync_rejected")
        with pytest.raises(FitatuPlannerSyncError, match="UUID version"):
            PlannerModule(client).sync_days(RECORDED_USER_ID, {DAY: {"dietPlan": {}}})
        client.request.return_value = recorded("sync_add_product")
        assert PlannerModule(client).sync_days(RECORDED_USER_ID, {DAY: {"dietPlan": {}}}) == recorded("sync_add_product")

    def test_day_summary_adds_up_recorded_rows(self) -> None:
        day = recorded("day_three_rows")
        summary = day_summary(day, date=DAY, user_id=RECORDED_USER_ID)
        expected = sum(row["energy"] for meal in day["dietPlan"].values() for row in meal["items"])
        assert summary["totals"]["energy"] == pytest.approx(expected)
        assert summary["eaten_totals"]["energy"] == 0  # the recorded rows are all planned, none eaten
        assert {item["food_type"] for meal in summary["meals"] for item in meal["items"]} == {"PRODUCT", "RECIPE", "CUSTOM_ITEM"}

    def test_day_summary_lists_recorded_activities(self) -> None:
        summary = day_summary(recorded("day_after_activity"), date=DAY)
        assert [(a["activity_id"], a["energy"]) for a in summary["activities"]] == [(535, 123)]

    def test_measurement_put_matches_the_recorded_request(self) -> None:
        client = Mock()
        client.request.return_value = recorded("measurement_put")
        MeasurementsModule(client).set_day(RECORDED_USER_ID, date(2020, 1, 1), weight=70, waist=80, merge=False)
        request = CALLS["measurement_put"]["request"]
        assert client.request.call_args.args == ("PUT", request["path"])
        assert client.request.call_args.kwargs["json_data"] == request["body"]

    def test_missing_measurement_is_none(self) -> None:
        client = Mock()
        client.request.side_effect = FitatuApiError("missing", status_code=404, body=str(recorded("measurement_missing")))
        assert MeasurementsModule(client).get_day(RECORDED_USER_ID, date(2020, 1, 1)) is None

    def test_weight_history_and_chart(self) -> None:
        client = Mock()
        client.request.return_value = recorded("measurement_weight_history")
        [entry] = MeasurementsModule(client).get_weight_history(RECORDED_USER_ID)
        assert set(entry) == {"date", "value", "difference", "direction"}
        client.request.return_value = recorded("measurement_weight_chart")
        assert set(MeasurementsModule(client).get_weight_chart(RECORDED_USER_ID)) == {"weights", "targets", "weightUnit"}

    def test_export_returns_the_csv_bytes(self) -> None:
        client = Mock()
        client.request.return_value = recorded("export_csv").encode()
        content = SummaryModule(client).export_diary(RECORDED_USER_ID, date.fromisoformat(DAY), date.fromisoformat(DAY))
        assert content is not None and content.startswith(b"Date,Meal")
        assert CALLS["export_empty_range"]["status"] == 204
        assert CALLS["export_reversed_range"]["status"] == 422

    def test_daily_targets_from_recorded_settings(self) -> None:
        client = Mock()
        client.auth.fitatu_user_id = RECORDED_USER_ID
        client.user_settings.get_new.return_value = recorded("settings_new")
        with patch("fitatu_api.facade.FitatuApiClient", return_value=client), pytest.warns(DeprecationWarning):
            result = FitatuLibrary({}).get_daily_targets_via_api()["result"]

        assert result["energy"] == 2000
        assert result["protein"] == {"grams": 100, "percent": 20}
        assert result["bmr"] == 1700

    def test_settings_new_needs_the_date(self) -> None:
        assert CALLS["settings_new_without_date"]["status"] == 405
        assert CALLS["settings_new"]["request"]["path"].endswith(f"/settings-new/{DAY}")

    def test_activity_requests_match_the_recorded_ones(self) -> None:
        client = Mock()
        client.auth = FitatuAuthContext()
        client.request.return_value = recorded("activity_search")
        module = ActivitiesModule(client)

        assert module.search("bieganie", limit=2) == recorded("activity_search")
        module.get_catalog(page=1, limit=2)

        for call, name in zip(client.request.call_args_list, ("activity_search", "activities_catalog"), strict=True):
            request = CALLS[name]["request"]
            assert call.args == ("GET", request["path"])
            assert call.kwargs["params"] == request["query"]

    def test_search_rows_carry_food_id(self) -> None:
        client = FitatuApiClient(auth=FitatuAuthContext(bearer_token="t"))
        client.request = Mock(return_value=recorded("search_food"))  # type: ignore[method-assign]
        rows = client.search_food("jajko kurze")
        assert rows and all("foodId" in row for row in rows)


def _load_recorder() -> Any:
    path = Path(__file__).parents[1] / "scripts" / "record_fixtures.py"
    if not path.exists():
        pytest.skip("scripts/ is not in the sdist")
    spec = importlib.util.spec_from_file_location("record_fixtures", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_scrubber_removes_personal_data() -> None:
    recorder = _load_recorder()
    raw = {
        "uid": "987654",
        "calls": {
            "day_empty": {"request": {"path": "/diet-and-activity-plan/987654/day/2027-01-03"}, "body": {
                "dietPlan": {"lunch": {"mealName": "My secret lunch", "mealTime": "12:34", "recommendedEnergy": 612, "items": []}}
            }},
            "export_csv": {"request": {}, "body": "Date,Meal\n2027-01-03,My secret lunch\n"},
            "search_food": {"request": {}, "body": [
                {"foodId": 1, "name": "Secret snack", "index": "LAST_USED", "userNumberOfUse": 15},
                {"foodId": 2, "name": "Plain catalog item", "index": "SEARCH", "userNumberOfUse": 3, "favorite": True},
            ]},
            "measurement_weight_history": {"request": {}, "body": [{"date": "2020-01-01", "value": 70, "difference": -23.7}]},
            "summary_nutrient_protein": {"request": {}, "body": {
                "measures": {"2027-01-03": 16.66}, "targetRanges": {"2027-01-03": {"min": 137.5, "max": 171.25}}
            }},
            "settings_new": {"request": {}, "body": {
                "userId": 987654,
                "nickname": "jezue-private",
                "userSettings": {"birthDate": "1985-05-05", "weightCurrent": 99, "heightUnit": 1},
                "userDietSettings": {"energy": 3100, "weightChangeDirection": -1, "nutritionSchema": [{"day": 1, "energy": 2950}]},
                "calculatedValues": {"BMR": 2100, "BMRConstant": {"formulaCoefficient": 15.057, "formulaElement": 692.2}},
                "weekMealSchema": {"dayMealSchemaCustomList": [{"meals": {"1": {"label": "breakfast", "mealName": "Night feast", "mealTime": "03:15"}}}]},
            }},
        },
    }
    scrubbed = recorder.scrub(raw)
    text = str(scrubbed)
    for secret in ("987654", "My secret lunch", "12:34", "1985-05-05", "3100", "612", "jezue-private",
                   "15.057", "692.2", "2950", "Night feast", "03:15", "Secret snack", "-23.7", "137.5", "171.25"):
        assert secret not in text, secret
    assert scrubbed["calls"]["search_food"]["body"] == [
        {"foodId": 2, "name": "Plain catalog item", "index": "SEARCH", "userNumberOfUse": 0, "favorite": False}
    ]
    assert scrubbed["calls"]["settings_new"]["body"]["userDietSettings"]["weightChangeDirection"] == 0
    assert scrubbed["calls"]["summary_nutrient_protein"]["body"] == {
        "measures": {"2027-01-03": 16.66}, "targetRanges": {"2027-01-03": {"min": 90, "max": 110}}
    }


def test_leak_guard_flags_values_the_scrubber_missed() -> None:
    recorder = _load_recorder()
    raw = {"uid": "987654", "calls": {"settings_new": {"request": {}, "body": {"userSettings": {"birthDate": "1985-05-05"}}}}}
    assert recorder.leaked_values(raw, '{"birthDate": "1985-05-05"}') == ["1985-05-05"]
    assert recorder.leaked_values(raw, '{"birthDate": "1990-01-01"}') == []
