"""Service module routes (moved from test_client.py)."""

from __future__ import annotations

import unittest
from datetime import date
from unittest.mock import Mock

from fitatu_api import (
    ActivitiesModule,
    DietPlanModule,
    ResourcesModule,
    UserSettingsModule,
    WaterModule,
)

TEST_DAY = date(2026, 4, 19)



class ServiceModuleTests(unittest.TestCase):
    def test_resources_module_calls_expected_route(self) -> None:
        client = Mock()
        client.request.return_value = {"tags": []}
        module = ResourcesModule(client)

        result = module.get_food_tags_recipe()

        self.assertEqual(result, {"tags": []})
        client.request.assert_called_once_with("GET", "/resources/food-tags/recipe")

    def test_user_settings_module_get_uses_optional_date_param(self) -> None:
        client = Mock()
        client.request.return_value = {"ok": True}
        module = UserSettingsModule(client)

        with self.assertWarns(DeprecationWarning):
            result = module.get("123", day=TEST_DAY)

        self.assertEqual(result["ok"], True)
        client.request.assert_called_once_with(
            "GET",
            "/users/123/settings",
            params={"date": TEST_DAY.isoformat()},
        )

    def test_user_settings_module_write_helpers_call_expected_routes(self) -> None:
        client = Mock()
        client.request.return_value = {"ok": True}
        module = UserSettingsModule(client)

        module.update_profile("123", {"name": "Adam"})
        module.update_new("123", {"dietType": "balanced"})
        module.update_water_settings("123", unit_capacity=300)

        self.assertEqual(client.request.call_args_list[0].args, ("PATCH", "/users/123"))
        self.assertEqual(client.request.call_args_list[1].args, ("PATCH", "/users/123/settings-new"))
        self.assertEqual(client.request.call_args_list[2].args, ("PATCH", "/users/123/settings-new"))

    def test_diet_plan_module_get_default_meal_schema(self) -> None:
        client = Mock()
        client.request.return_value = {"schema": []}
        module = DietPlanModule(client)

        with self.assertWarns(DeprecationWarning):
            result = module.get_default_meal_schema("123")

        self.assertEqual(result["schema"], [])
        client.request.assert_called_once_with(
            "GET",
            "/diet-plan/123/settings/preferences/meal-schema/default",
        )

    def test_diet_plan_module_get_settings_and_meal_schema(self) -> None:
        client = Mock()
        client.request.side_effect = [{"kcal": 2100}, {"schemas": []}]
        module = DietPlanModule(client)

        with self.assertWarns(DeprecationWarning):
            settings = module.get_settings("123")
        with self.assertWarns(DeprecationWarning):
            schema = module.get_meal_schema("123")

        self.assertEqual(settings["kcal"], 2100)
        self.assertEqual(schema["schemas"], [])
        self.assertEqual(client.request.call_args_list[0].args, ("GET", "/diet-plan/123/settings"))
        self.assertEqual(
            client.request.call_args_list[1].args,
            ("GET", "/diet-plan/123/settings/preferences/meal-schema"),
        )

    def test_water_module_get_day_calls_expected_route(self) -> None:
        client = Mock()
        client.request.return_value = {"water": 123}
        module = WaterModule(client)

        result = module.get_day("123", TEST_DAY)

        self.assertEqual(result["water"], 123)
        client.request.assert_called_once_with("GET", f"/water/123/{TEST_DAY.isoformat()}")

    def test_activities_module_get_catalog_calls_expected_route(self) -> None:
        client = Mock()
        client.auth.app_search_locale = "de_DE"
        client.request.return_value = [{"id": 1}]
        module = ActivitiesModule(client)

        result = module.get_catalog()

        self.assertEqual(result[0]["id"], 1)
        client.request.assert_called_once_with("GET", "/activities", params={"locale": "de_DE"})

    def test_activities_module_get_catalog_sends_only_given_params(self) -> None:
        client = Mock()
        ActivitiesModule(client).get_catalog(locale="pl_PL", bmr=1800.0, sex=1)

        client.request.assert_called_once_with(
            "GET", "/activities", params={"locale": "pl_PL", "bmr": 1800.0, "sex": 1}
        )

    def test_user_settings_get_new_requests_the_dated_route(self) -> None:
        client = Mock()
        client.request.return_value = {"userId": 1}

        UserSettingsModule(client).get_new("123", TEST_DAY)

        client.request.assert_called_once_with("GET", f"/users/123/settings-new/{TEST_DAY.isoformat()}")
