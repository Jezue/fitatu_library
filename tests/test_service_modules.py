"""Unit tests for the measurement, summary, foods and activity helpers."""

from __future__ import annotations

from datetime import date
from pathlib import Path
from unittest.mock import Mock, patch

import pytest

from fitatu_api import (
    ActivitiesModule,
    FitatuApiClient,
    FitatuApiError,
    FitatuAuthContext,
    FitatuLibrary,
    FoodsModule,
    MeasurementsModule,
    PlannerModule,
    SummaryModule,
)

USER = "123"
DAY = date(2026, 10, 1)


def _not_found(*_: object, **__: object) -> None:
    raise FitatuApiError("missing", status_code=404)


class TestMeasurementsModule:
    def test_get_day_returns_none_when_nothing_is_stored(self) -> None:
        client = Mock()
        client.request.side_effect = _not_found
        assert MeasurementsModule(client).get_day(USER, DAY) is None

    def test_get_day_reraises_other_errors(self) -> None:
        client = Mock()
        client.request.side_effect = FitatuApiError("boom", status_code=500)
        with pytest.raises(FitatuApiError):
            MeasurementsModule(client).get_day(USER, DAY)

    def test_set_day_sends_numbers_as_strings_like_the_app(self) -> None:
        client = Mock()
        client.request.side_effect = lambda method, path, **kw: _not_found() if method == "GET" else {"ok": True}

        MeasurementsModule(client).set_day(USER, DAY, weight=72.4, waist=81)

        method, path = client.request.call_args.args
        assert (method, path) == ("PUT", f"/users/{USER}/measurements/{DAY.isoformat()}")
        assert client.request.call_args.kwargs["json_data"] == {
            "weight": "72.4",
            "waist": "81.0",
            "fatPercentage": "0.0",
            "date": DAY.isoformat(),
            "weightUnit": "KG",
            "sizeUnit": "CM",
        }

    def test_set_day_merges_stored_values_and_keeps_their_units(self) -> None:
        stored = {"weight": 160.0, "waist": 33.0, "chest": None, "fatPercentage": 21.5, "weightUnit": "LB", "sizeUnit": "IN"}
        client = Mock()
        client.request.side_effect = lambda method, path, **kw: stored if method == "GET" else {"ok": True}

        MeasurementsModule(client).set_day(USER, DAY, weight=158)

        payload = client.request.call_args.kwargs["json_data"]
        assert payload["weight"] == "158.0"
        assert payload["waist"] == "33.0"
        assert payload["fatPercentage"] == "21.5"
        assert payload["weightUnit"] == "LB"
        assert payload["sizeUnit"] == "IN"
        assert "chest" not in payload

    def test_set_day_without_merge_skips_the_read(self) -> None:
        client = Mock()
        MeasurementsModule(client).set_day(USER, DAY, weight=70, merge=False)
        assert client.request.call_count == 1
        assert client.request.call_args.args[0] == "PUT"

    def test_set_day_rejects_unknown_body_parts_and_empty_input(self) -> None:
        module = MeasurementsModule(Mock())
        with pytest.raises(ValueError, match="unknown body parts"):
            module.set_day(USER, DAY, weight=70, knee=40)
        client = Mock()
        client.request.side_effect = _not_found
        with pytest.raises(ValueError, match="provide weight"):
            MeasurementsModule(client).set_day(USER, DAY)

    def test_weight_history_query(self) -> None:
        client = Mock()
        client.request.return_value = [{"date": "2026-09-01", "value": 72.0}, "junk"]

        rows = MeasurementsModule(client).get_weight_history(USER, from_date=date(2026, 9, 1), to_date=DAY)

        assert rows == [{"date": "2026-09-01", "value": 72.0}]
        client.request.assert_called_once_with(
            "GET",
            f"/users/{USER}/measurements/summary/weight",
            params={"fromDate": "2026-09-01", "toDate": DAY.isoformat(), "limit": 41, "page": 1},
        )

    def test_body_part_history_validates_body_part(self) -> None:
        with pytest.raises(ValueError):
            MeasurementsModule(Mock()).get_body_part_history(USER, "knee")

    def test_reversed_range_is_rejected_before_any_request(self) -> None:
        client = Mock()
        with pytest.raises(ValueError, match="after"):
            MeasurementsModule(client).get_weight_chart(USER, from_date=DAY, to_date=date(2026, 9, 1))
        client.request.assert_not_called()


class TestSummaryModule:
    def test_nutrition_and_activity_routes(self) -> None:
        client = Mock()
        client.request.return_value = {"energy": {"current": 1}}
        module = SummaryModule(client)
        start = date(2026, 9, 24)

        module.get_nutrition(USER, start, DAY)
        module.get_nutrient_by_day(USER, "protein", start, DAY)
        module.get_top_products(USER, "protein", start, DAY, limit=5)
        module.get_activities(USER, start, DAY)

        paths = [call.args[1] for call in client.request.call_args_list]
        assert paths == [
            f"/v2/diet-plan/{USER}/summary/custom",
            f"/v2/diet-plan/{USER}/summary/protein/custom",
            f"/diet-plan/{USER}/summary-product/protein/custom",
            f"/activity-plan/{USER}/summary/custom",
        ]
        assert client.request.call_args_list[2].kwargs["params"]["limit"] == 5

    def test_export_returns_bytes_or_none(self) -> None:
        client = Mock()
        client.request.return_value = b"Date,Meal\n"
        module = SummaryModule(client)

        assert module.export_diary(USER, date(2026, 9, 24), DAY, fmt="pdf") == b"Date,Meal\n"
        client.request.assert_called_once_with(
            "GET",
            f"/diet-plan/{USER}/export",
            params={"format": "pdf", "fromDate": "2026-09-24", "toDate": DAY.isoformat()},
            raw=True,
        )
        client.request.return_value = None
        assert module.export_diary(USER, date(2026, 9, 24), DAY) is None

    def test_export_rejects_unknown_format(self) -> None:
        with pytest.raises(ValueError, match="fmt"):
            SummaryModule(Mock()).export_diary(USER, DAY, DAY, fmt="xlsx")


class TestFoodsModule:
    def test_recent_and_favourites_routes(self) -> None:
        client = Mock()
        client.request.return_value = [{"foodId": 1}]
        module = FoodsModule(client)

        assert module.get_recent(USER, "breakfast") == [{"foodId": 1}]
        module.get_recent_own(USER, limit=10)
        module.get_favorites(USER, phrase="kefir")

        calls = client.request.call_args_list
        assert calls[0].args[1] == f"/search/food/{USER}/last-used"
        assert calls[0].kwargs["params"] == {"meal": "breakfast"}
        assert calls[1].kwargs["params"] == {"page": 1, "limit": 10}
        assert calls[2].args[1] == f"/users/{USER}/favorite/foods"
        assert calls[2].kwargs["params"]["phrase"] == "kefir"

    def test_set_favorite(self) -> None:
        client = Mock()
        FoodsModule(client).set_favorite(USER, 42, favorite=False, food_type="recipe")
        client.request.assert_called_once_with(
            "PUT", f"/users/{USER}/favorite/food/RECIPE/42", json_data={"markAsFavorite": False}
        )
        with pytest.raises(ValueError):
            FoodsModule(client).set_favorite(USER, 42, food_type="ACTIVITY")


class TestActivitiesAndRevisions:
    def test_activity_search_quotes_the_phrase(self) -> None:
        client = Mock()
        client.request.return_value = []
        ActivitiesModule(client).search("jazda na rowerze", locale="pl_PL")
        client.request.assert_called_once_with(
            "GET", "/search/activity/jazda%20na%20rowerze", params={"page": 1, "limit": 20, "locale": "pl_PL"}
        )

    def test_recent_activities(self) -> None:
        client = Mock()
        client.request.return_value = [{"activityId": 7}]
        assert ActivitiesModule(client).get_recent(USER) == [{"activityId": 7}]
        assert client.request.call_args.args[1] == f"/search/activity/{USER}/last-used"

    def test_day_revisions(self) -> None:
        client = Mock()
        client.request.return_value = ["b", "a"]
        assert PlannerModule(client).get_day_revisions(USER, DAY) == ["b", "a"]
        client.request.assert_called_once_with("GET", f"/diet-plan/{USER}/days/{DAY.isoformat()}/revisions")


class TestRawRequests:
    def _client(self) -> FitatuApiClient:
        return FitatuApiClient(auth=FitatuAuthContext(bearer_token="t"), retry_base_delay_seconds=0.0)

    def test_raw_returns_bytes(self) -> None:
        response = Mock(status_code=200, content=b"%PDF-1.7", text="ignored", headers={})
        with patch("fitatu_api.client.requests.request", return_value=response):
            assert self._client().request("GET", "/export", raw=True) == b"%PDF-1.7"

    def test_raw_returns_none_for_no_content(self) -> None:
        response = Mock(status_code=204, content=b"", text="", headers={})
        with patch("fitatu_api.client.requests.request", return_value=response):
            assert self._client().request("GET", "/export", raw=True) is None


class TestFacadeHelpers:
    def _library(self, client: Mock) -> FitatuLibrary:
        library = FitatuLibrary(session_data={"fitatu_user_id": USER})
        library._planner_result = Mock(return_value=(client, USER, None))  # type: ignore[method-assign]
        return library

    def test_daily_targets_flatten_settings(self) -> None:
        client = Mock()
        client.user_settings.get_new.return_value = {
            "date": "2026-10-01",
            "userDietSettings": {"energy": 2400, "proteinWeight": 150, "proteinPercentage": 25},
            "calculatedValues": {"BMR": 1900, "TMR": 2700},
        }

        result = self._library(client).get_daily_targets_via_api(target_date=DAY)

        assert result["status"] == "ok"
        assert result["result"]["energy"] == 2400
        assert result["result"]["protein"] == {"grams": 150, "percent": 25}
        assert result["result"]["bmr"] == 1900
        client.user_settings.get_new.assert_called_once_with(USER, DAY)

    def test_export_writes_file(self, tmp_path: Path) -> None:
        client = Mock()
        client.summary.export_diary.return_value = b"a,b\n"
        target = tmp_path / "diary.csv"

        result = self._library(client).export_diary_via_api(from_date=DAY, to_date=DAY, path=target)

        assert result == {"status": "ok", "result": {"format": "csv", "size_bytes": 4, "path": str(target)}}
        assert target.read_bytes() == b"a,b\n"

    def test_export_reports_empty_range(self) -> None:
        client = Mock()
        client.summary.export_diary.return_value = None
        assert self._library(client).export_diary_via_api(from_date=DAY, to_date=DAY)["status"] == "empty"

    def test_log_weight_wraps_api_errors(self) -> None:
        client = Mock()
        client.measurements.set_day.side_effect = FitatuApiError("nope", status_code=500)
        result = self._library(client).log_weight_via_api(weight=70.5, target_date=DAY)
        assert result["status"] == "error"
        assert result["operation"] == "log_weight_via_api"
