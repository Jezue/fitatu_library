"""Non-planner service modules exposed by the Fitatu client."""

from __future__ import annotations

import logging
from datetime import date
from typing import Any, cast
from urllib.parse import quote

from ._validation import validate_non_negative_int, validate_user_id
from .exceptions import FitatuApiError

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

    def search(
        self,
        phrase: str,
        *,
        page: int = 1,
        limit: int = 20,
        locale: str | None = None,
        bmr: float | None = None,
        sex: int | None = None,
    ) -> list[dict[str, Any]]:
        """Search activities by name (``bieganie``, ``rower``...)."""
        params = {"page": page, "limit": limit, "locale": locale, "bmr": bmr, "sex": sex}
        data = self._client.request(
            "GET",
            f"/search/activity/{quote(phrase, safe='')}",
            params={key: value for key, value in params.items() if value is not None},
        )
        return [row for row in data if isinstance(row, dict)] if isinstance(data, list) else []

    def get_recent(self, user_id: str, *, page: int = 1, limit: int = 20) -> list[dict[str, Any]]:
        """Return the user's recently logged activities."""
        validate_user_id(user_id)
        data = self._client.request(
            "GET",
            f"/search/activity/{user_id}/last-used",
            params={"page": page, "limit": limit},
        )
        return [row for row in data if isinstance(row, dict)] if isinstance(data, list) else []


def _date_range_params(from_date: date | None, to_date: date | None) -> dict[str, str]:
    """Build ``fromDate``/``toDate`` query params, rejecting reversed ranges up front."""
    if from_date is not None and to_date is not None and from_date > to_date:
        raise ValueError(f"from_date {from_date} is after to_date {to_date}")
    params: dict[str, str] = {}
    if from_date is not None:
        params["fromDate"] = from_date.isoformat()
    if to_date is not None:
        params["toDate"] = to_date.isoformat()
    return params


class MeasurementsModule:
    """Body weight and body-size measurements (the "Measurements" tab in the app)."""

    BODY_PARTS = ("neck", "chest", "waist", "stomach", "hips", "thigh", "calf", "biceps")

    def __init__(self, client: FitatuApiClient) -> None:
        self._client = client

    def get_day(self, user_id: str, day: date) -> dict[str, Any] | None:
        """Return the measurement saved for ``day``, or ``None`` when there is none."""
        validate_user_id(user_id)
        try:
            data = self._client.request("GET", f"/users/{user_id}/measurements/{day.isoformat()}")
        except FitatuApiError as exc:
            if exc.status_code == 404:
                return None
            raise
        return cast(dict[str, Any], data) if isinstance(data, dict) else None

    def set_day(
        self,
        user_id: str,
        day: date,
        *,
        weight: float | None = None,
        fat_percentage: float | None = None,
        weight_unit: str | None = None,
        size_unit: str | None = None,
        merge: bool = True,
        **body_parts: float,
    ) -> dict[str, Any]:
        """Save weight and/or body sizes for ``day``.

        Body parts are passed as keyword arguments named after ``BODY_PARTS``, e.g.
        ``set_day(uid, day, weight=72.4, waist=81)``. The endpoint replaces the whole
        record, so with ``merge=True`` (default) values already stored for that day are
        read first and kept unless overridden.

        Units: ``KG``/``LB``/``ST_LB`` for weight and ``CM``/``IN``/``FT_IN`` for sizes.
        When omitted they follow the stored record (so merged values keep their unit),
        falling back to ``KG`` and ``CM``.
        """
        validate_user_id(user_id)
        unknown = set(body_parts) - set(self.BODY_PARTS)
        if unknown:
            raise ValueError(f"unknown body parts: {sorted(unknown)}; expected any of {self.BODY_PARTS}")

        values: dict[str, Any] = {}
        existing = (self.get_day(user_id, day) or {}) if merge else {}
        if existing:
            values.update({key: existing.get(key) for key in ("weight", "fatPercentage", *self.BODY_PARTS)})
        if weight is not None:
            values["weight"] = weight
        if fat_percentage is not None:
            values["fatPercentage"] = fat_percentage
        values.update(body_parts)
        if not any(value is not None for value in values.values()):
            raise ValueError("provide weight, fat_percentage or at least one body part")

        # The app serialises every number as a string (double.toString()); match it.
        payload: dict[str, Any] = {
            key: str(float(value)) for key, value in values.items() if value is not None
        }
        payload.update(
            {
                "date": day.isoformat(),
                "weightUnit": weight_unit or existing.get("weightUnit") or "KG",
                "sizeUnit": size_unit or existing.get("sizeUnit") or "CM",
            }
        )
        payload.setdefault("fatPercentage", "0.0")
        data = self._client.request(
            "PUT",
            f"/users/{user_id}/measurements/{day.isoformat()}",
            json_data=payload,
        )
        return cast(dict[str, Any], data) if isinstance(data, dict) else {"raw": data}

    def delete_day(self, user_id: str, day: date) -> Any:
        """Delete the whole measurement record for ``day``."""
        validate_user_id(user_id)
        return self._client.request("DELETE", f"/users/{user_id}/measurements/{day.isoformat()}")

    def get_weight_history(
        self,
        user_id: str,
        *,
        from_date: date | None = None,
        to_date: date | None = None,
        page: int = 1,
        limit: int = 41,
    ) -> list[dict[str, Any]]:
        """Return weight entries as ``[{date, value, difference, direction}, ...]``.

        ``direction`` follows the goal type: 1 = gain, -1 = reduction, 0 = maintenance.
        """
        validate_user_id(user_id)
        params: dict[str, Any] = {**_date_range_params(from_date, to_date), "limit": limit, "page": page}
        data = self._client.request("GET", f"/users/{user_id}/measurements/summary/weight", params=params)
        return [row for row in data if isinstance(row, dict)] if isinstance(data, list) else []

    def get_body_part_history(
        self,
        user_id: str,
        body_part: str,
        *,
        from_date: date | None = None,
        to_date: date | None = None,
        page: int = 1,
        limit: int = 41,
    ) -> list[dict[str, Any]]:
        """Return ``[{date, value}, ...]`` for one of ``BODY_PARTS``."""
        validate_user_id(user_id)
        if body_part not in self.BODY_PARTS:
            raise ValueError(f"body_part must be one of {self.BODY_PARTS}, got {body_part!r}")
        params: dict[str, Any] = {**_date_range_params(from_date, to_date), "limit": limit, "page": page}
        data = self._client.request("GET", f"/users/{user_id}/measurements/size/{body_part}", params=params)
        return [row for row in data if isinstance(row, dict)] if isinstance(data, list) else []

    def get_weight_chart(
        self,
        user_id: str,
        *,
        from_date: date | None = None,
        to_date: date | None = None,
    ) -> dict[str, Any]:
        """Return the weight chart: ``{weights: {date: kg}, targets: {date: kg}, weightUnit}``."""
        validate_user_id(user_id)
        data = self._client.request(
            "GET",
            f"/users/{user_id}/measurements/chart/weight",
            params=_date_range_params(from_date, to_date) or None,
        )
        return cast(dict[str, Any], data) if isinstance(data, dict) else {"raw": data}

    def get_size_summary(
        self,
        user_id: str,
        *,
        from_date: date | None = None,
        to_date: date | None = None,
    ) -> dict[str, Any]:
        """Return the change of every body size (and fat %) across the range."""
        validate_user_id(user_id)
        data = self._client.request(
            "GET",
            f"/users/{user_id}/measurements/summary/size",
            params=_date_range_params(from_date, to_date) or None,
        )
        return cast(dict[str, Any], data) if isinstance(data, dict) else {"raw": data}


class SummaryModule:
    """Server-side aggregates over a date range, plus the diary export.

    These are what the app's "Summary" tab uses, so there is no need to fetch and add
    up every planner day client-side.
    """

    EXPORT_FORMATS = ("csv", "xls", "pdf", "txt")

    def __init__(self, client: FitatuApiClient) -> None:
        self._client = client

    def get_nutrition(self, user_id: str, from_date: date, to_date: date) -> dict[str, Any]:
        """Return range totals per nutrient, e.g. ``{"energy": {"current", "min", "max", ...}, ...}``."""
        validate_user_id(user_id)
        data = self._client.request(
            "GET",
            f"/v2/diet-plan/{user_id}/summary/custom",
            params=_date_range_params(from_date, to_date),
        )
        return cast(dict[str, Any], data) if isinstance(data, dict) else {"raw": data}

    def get_nutrient_by_day(self, user_id: str, nutrient: str, from_date: date, to_date: date) -> dict[str, Any]:
        """Return one nutrient day by day (``{targets, measures}``).

        ``nutrient`` uses the API's camelCase names: ``energy``, ``protein``, ``fat``,
        ``carbohydrate``, ``fiber``, ``sugars``, ``saturatedFat``, ``salt``, ``vitaminC``...
        """
        validate_user_id(user_id)
        data = self._client.request(
            "GET",
            f"/v2/diet-plan/{user_id}/summary/{nutrient}/custom",
            params=_date_range_params(from_date, to_date),
        )
        return cast(dict[str, Any], data) if isinstance(data, dict) else {"raw": data}

    def get_top_products(
        self,
        user_id: str,
        nutrient: str,
        from_date: date,
        to_date: date,
        *,
        page: int = 1,
        limit: int = 20,
    ) -> dict[str, Any]:
        """Return the products that contributed most of ``nutrient`` (``{list, count}``)."""
        validate_user_id(user_id)
        params: dict[str, Any] = {**_date_range_params(from_date, to_date), "page": page, "limit": limit}
        data = self._client.request("GET", f"/diet-plan/{user_id}/summary-product/{nutrient}/custom", params=params)
        return cast(dict[str, Any], data) if isinstance(data, dict) else {"raw": data}

    def get_activities(self, user_id: str, from_date: date, to_date: date) -> dict[str, Any]:
        """Return burned energy and activity totals for the range."""
        validate_user_id(user_id)
        data = self._client.request(
            "GET",
            f"/activity-plan/{user_id}/summary/custom",
            params=_date_range_params(from_date, to_date),
        )
        return cast(dict[str, Any], data) if isinstance(data, dict) else {"raw": data}

    def export_diary(self, user_id: str, from_date: date, to_date: date, *, fmt: str = "csv") -> bytes | None:
        """Download the food diary for a date range as ``csv``, ``xls``, ``pdf`` or ``txt``.

        Returns the file content, or ``None`` when the range has no entries (the API
        answers 204 in that case).
        """
        validate_user_id(user_id)
        if fmt not in self.EXPORT_FORMATS:
            raise ValueError(f"fmt must be one of {self.EXPORT_FORMATS}, got {fmt!r}")
        params = {"format": fmt, **_date_range_params(from_date, to_date)}
        data = self._client.request("GET", f"/diet-plan/{user_id}/export", params=params, raw=True)
        return data if isinstance(data, bytes) else None


class FoodsModule:
    """Favourite and recently used foods."""

    def __init__(self, client: FitatuApiClient) -> None:
        self._client = client

    def get_recent(self, user_id: str, meal: str) -> list[dict[str, Any]]:
        """Return foods recently logged in ``meal`` (``breakfast``, ``lunch``, ``snack``...)."""
        validate_user_id(user_id)
        data = self._client.request("GET", f"/search/food/{user_id}/last-used", params={"meal": meal})
        return [row for row in data if isinstance(row, dict)] if isinstance(data, list) else []

    def get_recent_own(self, user_id: str, *, page: int = 1, limit: int = 50) -> list[dict[str, Any]]:
        """Return the user's own products/recipes, most recently used first."""
        validate_user_id(user_id)
        data = self._client.request(
            "GET",
            f"/search/food/user/{user_id}/last-used",
            params={"page": page, "limit": limit},
        )
        return [row for row in data if isinstance(row, dict)] if isinstance(data, list) else []

    def get_favorites(
        self,
        user_id: str,
        *,
        phrase: str | None = None,
        page: int = 1,
        limit: int = 50,
    ) -> list[dict[str, Any]]:
        """Return favourite products and recipes, optionally filtered by ``phrase``."""
        validate_user_id(user_id)
        params: dict[str, Any] = {"page": page, "limit": limit}
        if phrase:
            params["phrase"] = phrase
        data = self._client.request("GET", f"/users/{user_id}/favorite/foods", params=params)
        return [row for row in data if isinstance(row, dict)] if isinstance(data, list) else []

    def set_favorite(
        self,
        user_id: str,
        food_id: int | str,
        *,
        favorite: bool = True,
        food_type: str = "PRODUCT",
    ) -> Any:
        """Mark (or unmark) a product or recipe as favourite.

        The API processes this asynchronously (202). Two opposite calls sent back to
        back for the same food can be applied in either order, so give it a moment
        before flipping the same item again.
        """
        validate_user_id(user_id)
        kind = food_type.upper()
        if kind not in {"PRODUCT", "RECIPE"}:
            raise ValueError(f"food_type must be PRODUCT or RECIPE, got {food_type!r}")
        return self._client.request(
            "PUT",
            f"/users/{user_id}/favorite/food/{kind}/{food_id}",
            json_data={"markAsFavorite": favorite},
        )
