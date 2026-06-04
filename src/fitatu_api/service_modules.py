"""Non-planner service modules exposed by the Fitatu client."""

from __future__ import annotations

import logging
from datetime import date
from typing import Any, cast

from ._validation import validate_non_negative_int, validate_user_id

logger = logging.getLogger(__name__)

if False:  # pragma: no cover
    from .client import FitatuApiClient


class ResourcesModule:
    """Read-only helpers for Fitatu static resources."""

    def __init__(self, client: FitatuApiClient) -> None:
        self._client = client

    def get_food_tags_recipe(self) -> Any:
        """Fetch recipe tag resources."""
        return self._client.request("GET", "/resources/food-tags/recipe")


class CmsModule:
    """Helpers for Fitatu CMS GraphQL endpoints."""

    def __init__(self, client: FitatuApiClient) -> None:
        self._client = client

    def graphql(
        self,
        query: str,
        *,
        variables: dict[str, Any] | None = None,
        operation_name: str | None = None,
    ) -> Any:
        """Execute a CMS GraphQL query."""
        payload: dict[str, Any] = {"query": query}
        if variables is not None:
            payload["variables"] = variables
        if operation_name:
            payload["operationName"] = operation_name
        return self._client.request(
            "POST",
            "https://www.fitatu.com/cms/api/graphql",
            json_data=payload,
        )


class AuthModule:
    """High-level auth helpers."""

    def __init__(self, client: FitatuApiClient) -> None:
        self._client = client

    def refresh(self) -> dict[str, Any]:
        """Refresh the access token using the refresh token."""
        return self._client.refresh_access_token()


class UserSettingsModule:
    """Helpers for user profile and settings endpoints."""

    def __init__(self, client: FitatuApiClient) -> None:
        self._client = client

    def get_profile(self, user_id: str) -> dict[str, Any]:
        """Fetch the user profile."""
        validate_user_id(user_id)
        data = self._client.request("GET", f"/users/{user_id}")
        return cast(dict[str, Any], data) if isinstance(data, dict) else {"raw": data}

    def update_profile(self, user_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        """Patch the user profile."""
        data = self._client.request("PATCH", f"/users/{user_id}", json_data=payload)
        return cast(dict[str, Any], data) if isinstance(data, dict) else {"raw": data}

    def get_for_day(self, user_id: str, day: date) -> dict[str, Any]:
        """Fetch settings resolved for a specific day."""
        data = self._client.request("GET", f"/users/{user_id}/settings/{day.isoformat()}")
        return cast(dict[str, Any], data) if isinstance(data, dict) else {"raw": data}

    def get(self, user_id: str, *, day: date | None = None) -> dict[str, Any]:
        """Fetch current user settings."""
        data = self._client.request(
            "GET",
            f"/users/{user_id}/settings",
            params={"date": day.isoformat()} if day is not None else None,
        )
        return cast(dict[str, Any], data) if isinstance(data, dict) else {"raw": data}

    def get_new(self, user_id: str, day: date | None = None) -> dict[str, Any]:
        """Fetch the `settings-new` payload resolved for ``day`` (defaults to today).

        This is where the mobile app reads goals from: energy and macro targets
        (``userDietSettings``), BMR/TMR (``calculatedValues``), the weekly meal schema,
        water and planner settings. The GET route needs the date segment; the bare
        ``/settings-new`` path only accepts PATCH, which is why it answered 405.
        """
        target_day = day or date.today()
        data = self._client.request("GET", f"/users/{user_id}/settings-new/{target_day.isoformat()}")
        return cast(dict[str, Any], data) if isinstance(data, dict) else {"raw": data}

    def update_new(self, user_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        """Patch the `settings-new` payload."""
        data = self._client.request("PATCH", f"/users/{user_id}/settings-new", json_data=payload)
        return cast(dict[str, Any], data) if isinstance(data, dict) else {"raw": data}

    def update_system_info(
        self,
        user_id: str,
        *,
        app_version: str,
        system_info: str = "FITATU-WEB",
    ) -> dict[str, Any]:
        """Update system info metadata on the user profile."""
        data = self._client.request(
            "PATCH",
            f"/users/{user_id}",
            json_data={"systemInfo": system_info, "appVersion": app_version},
        )
        return cast(dict[str, Any], data) if isinstance(data, dict) else {"raw": data}

    def update_water_settings(self, user_id: str, *, unit_capacity: int) -> dict[str, Any]:
        """Update water settings inside the `settings-new` payload."""
        data = self._client.request(
            "PATCH",
            f"/users/{user_id}/settings-new",
            json_data={"userId": str(user_id), "waterSettings": {"unitCapacity": unit_capacity}},
        )
        return cast(dict[str, Any], data) if isinstance(data, dict) else {"raw": data}

    def get_firebase_token(self, user_id: str) -> Any:
        """Fetch the user's Firebase token."""
        return self._client.request("GET", f"/users/{user_id}/firebaseToken")


class DietPlanModule:
    """Helpers for diet plan settings endpoints."""

    def __init__(self, client: FitatuApiClient) -> None:
        self._client = client

    def get_settings(self, user_id: str) -> dict[str, Any]:
        """Fetch diet plan settings."""
        data = self._client.request("GET", f"/diet-plan/{user_id}/settings")
        return cast(dict[str, Any], data) if isinstance(data, dict) else {"raw": data}

    def get_default_meal_schema(self, user_id: str) -> Any:
        """Fetch the default meal schema."""
        return self._client.request(
            "GET",
            f"/diet-plan/{user_id}/settings/preferences/meal-schema/default",
        )

    def get_meal_schema(self, user_id: str) -> Any:
        """Fetch available meal schema preferences."""
        return self._client.request("GET", f"/diet-plan/{user_id}/settings/preferences/meal-schema")


class WaterModule:
    """Water tracking helpers."""

    def __init__(self, client: FitatuApiClient) -> None:
        self._client = client

    def get_day(self, user_id: str, day: date) -> Any:
        """Fetch water data for a given day."""
        return self._client.request("GET", f"/water/{user_id}/{day.isoformat()}")

    def set_day(self, user_id: str, day: date, water_consumption_ml: int) -> Any:
        """Set absolute water consumption (ml) for a given day."""
        validate_user_id(user_id)
        validate_non_negative_int(water_consumption_ml, "water_consumption_ml")
        return self._client.request(
            "PUT",
            f"/water/{user_id}/{day.isoformat()}",
            json_data={"waterConsumption": water_consumption_ml},
        )

    def add_intake(self, user_id: str, day: date, amount_ml: int) -> Any:
        """Add water intake (ml) on top of the current consumption for a given day."""
        validate_non_negative_int(amount_ml, "amount_ml")
        current = self.get_day(user_id, day)
        current_ml = int((current.get("water") or {}).get("waterConsumption") or 0)
        return self.set_day(user_id, day, current_ml + amount_ml)


class ActivitiesModule:
    """Helpers for Fitatu activity catalog endpoints."""

    def __init__(self, client: FitatuApiClient) -> None:
        self._client = client

    def get_catalog(
        self,
        *,
        page: int | None = None,
        limit: int | None = None,
        locale: str | None = None,
        bmr: float | None = None,
        sex: int | None = None,
    ) -> Any:
        """Fetch the activity catalog, grouped by first letter.

        Pass ``bmr`` and ``sex`` (the profile's numeric value) to get ``energy``
        values computed for the user instead of the generic defaults.
        """
        params = {"page": page, "limit": limit, "locale": locale, "bmr": bmr, "sex": sex}
        return self._client.request(
            "GET",
            "/activities",
            params={key: value for key, value in params.items() if value is not None} or None,
        )
