"""Unit tests for the measurement, summary, foods and activity helpers."""

from __future__ import annotations

import warnings
from collections.abc import Callable
from datetime import date
from typing import Any
from unittest.mock import Mock, patch

import pytest

from fitatu_api import (
    ActivitiesModule,
    DietPlanModule,
    FitatuApiClient,
    FitatuApiError,
    FitatuAuthContext,
    FitatuPlannerSyncError,
    FitatuValidationError,
    FoodsModule,
    MeasurementsModule,
    PlannerModule,
    SummaryModule,
    UserSettingsModule,
    WaterModule,
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

    def test_set_day_refuses_to_relabel_stored_values_with_another_unit(self) -> None:
        stored = {"neck": 40.0, "waist": 85.0, "weight": 72.4, "weightUnit": "KG", "sizeUnit": "CM"}
        client = Mock()
        client.request.side_effect = lambda method, path, **kw: stored if method == "GET" else {"ok": True}
        module = MeasurementsModule(client)

        with pytest.raises(FitatuValidationError, match="neck"):
            module.set_day(USER, DAY, waist=32, size_unit="IN")
        module.set_day(USER, DAY, waist=32, neck=15.7, size_unit="IN")  # all kept sizes given in IN
        assert client.request.call_args.kwargs["json_data"]["sizeUnit"] == "IN"

    def test_set_day_upper_cases_units_and_rejects_unknown_ones(self) -> None:
        client = Mock()
        module = MeasurementsModule(client)

        module.set_day(USER, DAY, weight=160, waist=33, weight_unit="lb", size_unit=" in ", merge=False)

        payload = client.request.call_args.kwargs["json_data"]
        assert (payload["weightUnit"], payload["sizeUnit"]) == ("LB", "IN")
        client.request.reset_mock()
        with pytest.raises(FitatuValidationError, match="weight_unit"):
            module.set_day(USER, DAY, weight=70, weight_unit="stone")
        with pytest.raises(FitatuValidationError, match="size_unit"):
            module.set_day(USER, DAY, waist=80, size_unit="mm")
        client.request.assert_not_called()

    @pytest.mark.parametrize("reply", [None, "", "<html>busy</html>", []])
    def test_set_day_does_not_replace_a_record_it_could_not_read(self, reply: Any) -> None:
        client = Mock()
        client.request.return_value = reply
        with pytest.raises(FitatuApiError, match="unexpected response shape"):
            MeasurementsModule(client).set_day(USER, DAY, weight=71)
        assert [c.args[0] for c in client.request.call_args_list] == ["GET"]

    def test_set_day_none_keeps_the_stored_value(self) -> None:
        stored = {"weight": 70, "waist": 80, "neck": 38, "weightUnit": "KG", "sizeUnit": "CM"}
        client = Mock()
        client.request.side_effect = lambda method, path, **kw: stored if method == "GET" else {}

        MeasurementsModule(client).set_day(
            USER, DAY, weight=None, fat_percentage=None, waist=None, neck=None, chest=72
        )

        payload = client.request.call_args.kwargs["json_data"]
        assert {k: payload[k] for k in ("weight", "waist", "neck", "chest")} == {
            "weight": "70.0",
            "waist": "80.0",
            "neck": "38.0",
            "chest": "72.0",
        }

    @pytest.mark.parametrize(
        ("kwargs", "name"),
        [
            ({"weight": "72,4"}, "weight"),
            ({"weight": -70}, "weight"),
            ({"weight": float("nan")}, "weight"),
            ({"weight": True}, "weight"),
            ({"fat_percentage": float("inf")}, "fat_percentage"),
            ({"weight": 70, "waist": "eighty"}, "waist"),
        ],
    )
    def test_set_day_rejects_values_that_are_not_non_negative_numbers(self, kwargs: dict[str, Any], name: str) -> None:
        client = Mock()
        with pytest.raises(FitatuValidationError, match=name):
            MeasurementsModule(client).set_day(USER, DAY, **kwargs)
        client.request.assert_not_called()

    def test_set_day_accepts_numeric_strings(self) -> None:
        client = Mock()
        MeasurementsModule(client).set_day(USER, DAY, weight=" 72.4", waist="81", merge=False)  # type: ignore[arg-type]
        payload = client.request.call_args.kwargs["json_data"]
        assert (payload["weight"], payload["waist"]) == ("72.4", "81.0")

    def test_set_day_without_merge_skips_the_read(self) -> None:
        client = Mock()
        MeasurementsModule(client).set_day(USER, DAY, weight=70, merge=False)
        assert client.request.call_count == 1
        assert client.request.call_args.args[0] == "PUT"

    def test_full_replacement_writes_are_retried_and_empty_replies_are_dicts(self) -> None:
        client = Mock()
        client.request.return_value = None
        module = MeasurementsModule(client)

        assert module.set_day(USER, DAY, weight=70, merge=False) == {}
        module.delete_day(USER, DAY)

        assert [c.kwargs["idempotent"] for c in client.request.call_args_list] == [True, True]

    def test_set_day_rejects_unknown_body_parts_and_empty_input(self) -> None:
        module = MeasurementsModule(Mock())
        with pytest.raises(FitatuValidationError, match="unknown body parts"):
            module.set_day(USER, DAY, weight=70, knee=40)
        client = Mock()
        client.request.side_effect = _not_found
        with pytest.raises(FitatuValidationError, match="provide weight"):
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

    def test_other_read_routes(self) -> None:
        client = Mock()
        client.request.return_value = {"weight": 72.0}
        module = MeasurementsModule(client)

        assert module.get_day(USER, DAY) == {"weight": 72.0}
        client.request.return_value = [{"date": "2026-09-01", "value": 80.0}]
        assert module.get_body_part_history(USER, "waist", from_date=date(2026, 9, 1)) == [
            {"date": "2026-09-01", "value": 80.0}
        ]
        client.request.return_value = {"waist": {"difference": -2}}
        module.get_size_summary(USER, from_date=date(2026, 9, 1), to_date=DAY)
        module.delete_day(USER, DAY)

        calls = [(c.args[0], c.args[1]) for c in client.request.call_args_list]
        assert calls == [
            ("GET", f"/users/{USER}/measurements/{DAY.isoformat()}"),
            ("GET", f"/users/{USER}/measurements/size/waist"),
            ("GET", f"/users/{USER}/measurements/summary/size"),
            ("DELETE", f"/users/{USER}/measurements/{DAY.isoformat()}"),
        ]
        assert client.request.call_args_list[1].kwargs["params"] == {"fromDate": "2026-09-01", "limit": 41, "page": 1}

    def test_body_part_history_validates_body_part(self) -> None:
        with pytest.raises(FitatuValidationError):
            MeasurementsModule(Mock()).get_body_part_history(USER, "knee")

    def test_reversed_range_is_rejected_before_any_request(self) -> None:
        client = Mock()
        with pytest.raises(FitatuValidationError, match="after"):
            MeasurementsModule(client).get_weight_chart(USER, from_date=DAY, to_date=date(2026, 9, 1))
        with pytest.raises(FitatuValidationError, match="after"):
            SummaryModule(client).get_nutrition(USER, "2026-10-01", "2026-09-01")
        client.request.assert_not_called()


class TestWaterModule:
    def test_set_day_is_retried(self) -> None:
        client = Mock()
        WaterModule(client).set_day(USER, DAY, 1500)
        client.request.assert_called_once_with(
            "PUT", f"/water/{USER}/{DAY.isoformat()}", json_data={"waterConsumption": 1500}, idempotent=True
        )

    def test_add_intake_adds_to_the_stored_amount(self) -> None:
        client = Mock()
        client.request.side_effect = [{"water": {"waterConsumption": 750}}, {}]
        WaterModule(client).add_intake(USER, DAY, 250)
        assert client.request.call_args.kwargs["json_data"] == {"waterConsumption": 1000}

    def test_add_intake_on_an_empty_day(self) -> None:
        # request() returns None when the API answers with an empty body.
        client = Mock()
        client.request.side_effect = [None, {}]
        WaterModule(client).add_intake(USER, DAY, 250)
        assert client.request.call_args.kwargs["json_data"] == {"waterConsumption": 250}

    def test_get_day_rejects_a_non_object_reply(self) -> None:
        client = Mock()
        client.request.return_value = ["unexpected"]
        with pytest.raises(FitatuApiError, match="unexpected response shape"):
            WaterModule(client).get_day(USER, DAY)


class TestReplyShapes:
    @pytest.mark.parametrize(
        "call",
        [
            lambda c: UserSettingsModule(c).get_profile(USER),
            lambda c: UserSettingsModule(c).get_new(USER, DAY),
            lambda c: MeasurementsModule(c).get_weight_chart(USER),
            lambda c: MeasurementsModule(c).get_size_summary(USER),
            lambda c: SummaryModule(c).get_nutrition(USER, DAY, DAY),
            lambda c: SummaryModule(c).get_nutrient_by_day(USER, "energy", DAY, DAY),
            lambda c: SummaryModule(c).get_top_products(USER, "energy", DAY, DAY),
            lambda c: SummaryModule(c).get_activities(USER, DAY, DAY),
        ],
    )
    def test_reads_that_must_be_objects_raise_on_anything_else(self, call: Callable[[Mock], object]) -> None:
        client = Mock()
        client.request.return_value = "<html>maintenance</html>"
        with pytest.raises(FitatuApiError, match="unexpected response shape") as caught:
            call(client)
        assert not isinstance(caught.value, FitatuValidationError)

    def test_writes_with_an_empty_reply_return_an_empty_dict(self) -> None:
        client = Mock()
        client.request.return_value = None
        module = UserSettingsModule(client)
        assert module.update_profile(USER, {"name": "A"}) == {}
        assert module.update_new(USER, {}) == {}
        assert module.update_water_settings(USER, unit_capacity=250) == {}
        assert MeasurementsModule(client).delete_day(USER, DAY) == {}
        assert WaterModule(client).set_day(USER, DAY, 500) == {}
        assert WaterModule(client).add_intake(USER, DAY, 250) == {}
        assert FoodsModule(client).set_favorite(USER, 42) == {}

    def test_a_retried_delete_that_already_went_through_is_not_a_404(self) -> None:
        client = FitatuApiClient(auth=FitatuAuthContext(bearer_token="t"), retry_base_delay_seconds=0.0)
        gateway_timeout = Mock(status_code=504, text="", headers={})
        already_gone = Mock(status_code=404, text='{"message": "Measurement not found"}', headers={})
        with patch("fitatu_api.client.requests.Session.request", side_effect=[gateway_timeout, already_gone]) as sent:
            assert client.measurements.delete_day(USER, DAY) == {}
        assert sent.call_count == 2


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

    def test_export_refused_for_want_of_premium_says_so(self) -> None:
        client = Mock()
        client.request.side_effect = FitatuApiError("GET -> 403", status_code=403, body='{"message": "Forbidden"}')
        with pytest.raises(FitatuApiError, match="the diary export needs Fitatu Premium") as refused:
            SummaryModule(client).export_diary(USER, DAY, DAY)
        assert (refused.value.status_code, refused.value.body) == (403, '{"message": "Forbidden"}')
        client.request.side_effect = firewall = FitatuApiError("GET -> 403", status_code=403, body="<html>blocked</html>")
        with pytest.raises(FitatuApiError) as blocked:
            SummaryModule(client).export_diary(USER, DAY, DAY)
        assert blocked.value is firewall

    def test_export_rejects_unknown_format(self) -> None:
        with pytest.raises(FitatuValidationError, match="fmt"):
            SummaryModule(Mock()).export_diary(USER, DAY, DAY, fmt="xlsx")


class TestFoodsModule:
    def test_recent_and_favourites_routes(self) -> None:
        client = Mock()
        client.request.return_value = [{"foodId": 1}]
        module = FoodsModule(client)

        assert module.get_recent(USER, " Second Breakfast") == [{"foodId": 1}]
        module.get_recent_own(USER, limit=10)
        module.get_favorites(USER, phrase="kefir")

        calls = client.request.call_args_list
        assert calls[0].args[1] == f"/search/food/{USER}/last-used"
        assert calls[0].kwargs["params"] == {"meal": "second_breakfast"}
        assert calls[1].kwargs["params"] == {"page": 1, "limit": 10}
        assert calls[2].args[1] == f"/users/{USER}/favorite/foods"
        assert calls[2].kwargs["params"]["phrase"] == "kefir"

    def test_set_favorite(self) -> None:
        client = Mock()
        FoodsModule(client).set_favorite(USER, 42, favorite=False, food_type="recipe")
        client.request.assert_called_once_with(
            "PUT", f"/users/{USER}/favorite/food/RECIPE/42", json_data={"markAsFavorite": False}, idempotent=True
        )
        for food_type in ("ACTIVITY", None):
            with pytest.raises(FitatuValidationError, match="food_type"):
                FoodsModule(client).set_favorite(USER, 42, food_type=food_type)  # type: ignore[arg-type]
        with pytest.raises(FitatuValidationError, match="food_id"):
            FoodsModule(client).set_favorite(USER, "42/../1")


class TestPremium:
    TODAY = date.today()

    @staticmethod
    def plan(start: str, end: str | None) -> dict[str, Any]:
        # the live reply's fields, with neutral values
        return {
            "id": 1, "startDate": start, "endDate": end, "planName": "Premium", "planGroupName": "premium", "planGroupType": "PREMIUM",
            "provider": "STORE", "providerId": "x", "duration": "P1Y", "amount": "1.00", "currency": "PLN", "description": None,
            "lastOriginalTransactionId": "0000",
        }

    @pytest.mark.parametrize(
        ("dates", "expected"),
        [
            ([], {"active": False, "end_date": None}),
            ([("2020-01-01 00:00:00", "2020-12-31 23:59:59")], {"active": False, "end_date": None}),  # over
            ([("2099-01-01", "2099-12-31")], {"active": False, "end_date": None}),  # not started
            ([("2020-01-01 00:00:00", "2098-01-31T10:00:00+00:00"), ("2020-01-01", "2099-01-31")], {"active": True, "end_date": "2099-01-31"}),
            ([("2020-01-01", None)], {"active": True, "end_date": None}),  # no end
        ],
    )
    def test_only_whether_a_plan_runs_today_and_until_when(self, dates: list[tuple[str, str | None]], expected: dict[str, Any]) -> None:
        client = Mock()
        client.request.return_value = {"subscriptions": [self.plan(start, end) for start, end in dates]}
        assert UserSettingsModule(client).get_premium(USER) == expected  # nothing else: no prices or transaction ids
        client.request.assert_called_once_with("GET", f"/users/{USER}/subscriptions")


class TestActivitiesAndRevisions:
    def test_activity_search_quotes_the_phrase(self) -> None:
        client = Mock()
        client.request.return_value = []
        ActivitiesModule(client).search("jazda na rowerze", locale="pl_PL")
        client.request.assert_called_once_with(
            "GET", "/search/activity/jazda%20na%20rowerze", params={"page": 1, "limit": 20, "locale": "pl_PL"}
        )

    def test_activity_search_defaults_to_the_session_search_locale(self) -> None:
        client = Mock()
        client.auth.app_search_locale = "de_DE"
        client.request.return_value = []
        ActivitiesModule(client).search("laufen")
        assert client.request.call_args.kwargs["params"]["locale"] == "de_DE"

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


CALLS_WITH_A_USER_ID: list[Callable[[Mock, Any], object]] = [
    lambda c, uid: UserSettingsModule(c).get_profile(uid),
    lambda c, uid: UserSettingsModule(c).update_profile(uid, {}),
    lambda c, uid: UserSettingsModule(c).get(uid),
    lambda c, uid: UserSettingsModule(c).get_for_day(uid, DAY),
    lambda c, uid: UserSettingsModule(c).get_new(uid),
    lambda c, uid: UserSettingsModule(c).update_new(uid, {}),
    lambda c, uid: UserSettingsModule(c).update_water_settings(uid, unit_capacity=250),
    lambda c, uid: DietPlanModule(c).get_settings(uid),
    lambda c, uid: DietPlanModule(c).get_default_meal_schema(uid),
    lambda c, uid: DietPlanModule(c).get_meal_schema(uid),
    lambda c, uid: WaterModule(c).get_day(uid, DAY),
    lambda c, uid: WaterModule(c).set_day(uid, DAY, 500),
    lambda c, uid: WaterModule(c).add_intake(uid, DAY, 250),
    lambda c, uid: ActivitiesModule(c).get_recent(uid),
    lambda c, uid: MeasurementsModule(c).get_day(uid, DAY),
    lambda c, uid: MeasurementsModule(c).set_day(uid, DAY, weight=70),
    lambda c, uid: MeasurementsModule(c).delete_day(uid, DAY),
    lambda c, uid: MeasurementsModule(c).get_weight_history(uid),
    lambda c, uid: MeasurementsModule(c).get_body_part_history(uid, "waist"),
    lambda c, uid: MeasurementsModule(c).get_weight_chart(uid),
    lambda c, uid: MeasurementsModule(c).get_size_summary(uid),
    lambda c, uid: SummaryModule(c).get_nutrition(uid, DAY, DAY),
    lambda c, uid: SummaryModule(c).get_nutrient_by_day(uid, "energy", DAY, DAY),
    lambda c, uid: SummaryModule(c).get_top_products(uid, "energy", DAY, DAY),
    lambda c, uid: SummaryModule(c).get_activities(uid, DAY, DAY),
    lambda c, uid: SummaryModule(c).export_diary(uid, DAY, DAY),
    lambda c, uid: FoodsModule(c).get_recent(uid, "breakfast"),
    lambda c, uid: FoodsModule(c).get_recent_own(uid),
    lambda c, uid: FoodsModule(c).get_favorites(uid),
    lambda c, uid: FoodsModule(c).set_favorite(uid, 42),
]


class TestInputValidation:
    @pytest.mark.filterwarnings("ignore::DeprecationWarning")
    @pytest.mark.parametrize("call", CALLS_WITH_A_USER_ID)
    @pytest.mark.parametrize("user_id", ["123/../456", "user-123", None])
    def test_user_id_must_be_numeric_before_any_request(self, call: Callable[[Mock, Any], object], user_id: Any) -> None:
        client = Mock()
        with pytest.raises(FitatuValidationError, match="user_id"):
            call(client, user_id)
        client.request.assert_not_called()

    def test_dates_accept_iso_strings(self) -> None:
        client = Mock()
        client.request.return_value = {}

        WaterModule(client).get_day(USER, "2026-10-01")
        MeasurementsModule(client).delete_day(USER, "2026-10-01")
        UserSettingsModule(client).get_new(USER, "2026-10-01")
        SummaryModule(client).get_nutrition(USER, "2026-09-24", "2026-10-01")

        calls = client.request.call_args_list
        assert [c.args[1] for c in calls[:3]] == [
            f"/water/{USER}/2026-10-01",
            f"/users/{USER}/measurements/2026-10-01",
            f"/users/{USER}/settings-new/2026-10-01",
        ]
        assert calls[3].kwargs["params"] == {"fromDate": "2026-09-24", "toDate": "2026-10-01"}

    def test_a_malformed_date_is_a_validation_error(self) -> None:
        client = Mock()
        with pytest.raises(FitatuValidationError, match="from_date"):
            SummaryModule(client).get_nutrition(USER, "24.09.2026", DAY)
        with pytest.raises(FitatuValidationError, match="day"):
            WaterModule(client).set_day(USER, "tomorrow", 500)
        client.request.assert_not_called()

    @pytest.mark.parametrize("nutrient", ["../energy", "a/b", "..", ".", "", "energy\n", None])
    def test_nutrient_must_be_a_nutrient_name(self, nutrient: Any) -> None:
        client = Mock()
        module = SummaryModule(client)
        with pytest.raises(FitatuValidationError, match="nutrient"):
            module.get_nutrient_by_day(USER, nutrient, DAY, DAY)
        with pytest.raises(FitatuValidationError, match="nutrient"):
            module.get_top_products(USER, nutrient, DAY, DAY)
        client.request.assert_not_called()

    @pytest.mark.parametrize(
        "call",
        [
            lambda c: FoodsModule(c).get_favorites(USER, limit=0),
            lambda c: FoodsModule(c).get_recent_own(USER, page=0),
            lambda c: FoodsModule(c).get_recent(USER, None),
            lambda c: FoodsModule(c).set_favorite(USER, 42, favorite="false"),
            lambda c: MeasurementsModule(c).get_weight_history(USER, limit=-1),
            lambda c: MeasurementsModule(c).get_body_part_history(USER, "waist", page=True),
            lambda c: ActivitiesModule(c).get_recent(USER, page=0),
            lambda c: ActivitiesModule(c).search("bieganie", limit=0),
            lambda c: SummaryModule(c).get_top_products(USER, "energy", DAY, DAY, limit=0),
            lambda c: UserSettingsModule(c).update_water_settings(USER, unit_capacity=-250),
            lambda c: PlannerModule(c).add_product_to_day_meal(USER, DAY, meal_type="lunch", product_id="abc", measure_id=1),
            lambda c: PlannerModule(c).add_product_to_day_meal(USER, DAY, meal_type="lunch", product_id=1, measure_id=None),
            lambda c: PlannerModule(c).add_product_to_day_meal(USER, DAY, meal_type="lunch", product_id=1, measure_id=1, eaten="no"),
            lambda c: PlannerModule(c).add_recipe_to_day_meal(USER, DAY, meal_type="lunch", recipe_id="owsianka"),
            lambda c: PlannerModule(c).add_custom_item_to_day_meal(
                USER, DAY, meal_type="lunch", name="X", calories=1, protein_g=0, fat_g=0, carbs_g=0, eaten=1
            ),
            lambda c: PlannerModule(c).update_day_item(USER, DAY, meal_type="lunch", item_id="x", eaten="yes"),
            lambda c: PlannerModule(c).sync_days(USER, {DAY: {"dietPlan": {}}}),
        ],
    )
    def test_argument_mistakes_send_nothing(self, call: Callable[[Mock], object]) -> None:
        client = Mock()
        with pytest.raises(FitatuValidationError):
            call(client)
        client.request.assert_not_called()

    @pytest.mark.parametrize("phrase", ["", "  ", ".", " .. ", None])
    def test_activity_search_needs_a_phrase_that_stays_one_segment(self, phrase: Any) -> None:
        client = Mock()
        with pytest.raises(FitatuValidationError, match="phrase"):
            ActivitiesModule(client).search(phrase)
        client.request.assert_not_called()


class TestLegacySettingsReads:
    def test_they_warn_when_called_not_when_the_client_is_built(self) -> None:
        with warnings.catch_warnings():
            warnings.simplefilter("error", DeprecationWarning)
            FitatuApiClient(auth=FitatuAuthContext(bearer_token="t")).close()

        client = Mock()
        client.request.return_value = {}
        for call in (
            lambda: UserSettingsModule(client).get(USER),
            lambda: UserSettingsModule(client).get_for_day(USER, DAY),
            lambda: DietPlanModule(client).get_settings(USER),
            lambda: DietPlanModule(client).get_default_meal_schema(USER),
            lambda: DietPlanModule(client).get_meal_schema(USER),
        ):
            with pytest.warns(DeprecationWarning, match=r"user_settings\.get_new") as caught:
                call()
            assert caught[0].filename == __file__  # points at the caller, not the library
        assert client.request.call_count == 5


class TestErrors:
    def test_planner_sync_error_survives_pickle_and_copy(self) -> None:
        import copy
        import pickle

        error = FitatuPlannerSyncError("rejected", [{"date": "2026-10-01", "errorMessage": "bad uuid"}])
        for clone in (pickle.loads(pickle.dumps(error)), copy.deepcopy(error)):
            assert clone.results == error.results
            assert clone.status_code == 202
            assert str(clone) == "rejected"


class TestRawRequests:
    def _client(self) -> FitatuApiClient:
        return FitatuApiClient(auth=FitatuAuthContext(bearer_token="t"), retry_base_delay_seconds=0.0)

    def test_raw_returns_bytes(self) -> None:
        response = Mock(status_code=200, content=b"%PDF-1.7", text="ignored", headers={})
        with patch("fitatu_api.client.requests.Session.request", return_value=response):
            assert self._client().request("GET", "/export", raw=True) == b"%PDF-1.7"

    def test_raw_returns_none_for_no_content(self) -> None:
        response = Mock(status_code=204, content=b"", text="", headers={})
        with patch("fitatu_api.client.requests.Session.request", return_value=response):
            assert self._client().request("GET", "/export", raw=True) is None
