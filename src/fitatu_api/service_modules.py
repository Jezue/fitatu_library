"""Non-planner service modules exposed by the Fitatu client."""

from __future__ import annotations

import math
import warnings
from datetime import date
from typing import Any
from urllib.parse import quote

from ._shapes import as_dict_list, read_reply, write_reply
from ._validation import (
    as_date,
    validate_bool,
    validate_non_negative_int,
    validate_numeric_id,
    validate_positive_int,
)
from .exceptions import FitatuApiError, FitatuValidationError
from .planner import PlannerModule

if False:  # pragma: no cover
    from .client import FitatuApiClient


def _page_params(page: int, limit: int) -> dict[str, int]:
    """``page`` and ``limit`` query params; both must be positive integers."""
    validate_positive_int(page, "page")
    validate_positive_int(limit, "limit")
    return {"page": page, "limit": limit}


def _warn_legacy_settings_read(name: str) -> None:
    """Emit a DeprecationWarning for a read that ``settings-new`` replaces."""
    warnings.warn(
        f"{name}() reads a legacy settings route. Use client.user_settings.get_new(user_id, day), "
        "where the app reads goals, the meal schema and water settings. "
        "It will be removed in 1.0.",
        DeprecationWarning,
        stacklevel=3,
    )


class ResourcesModule:
    """Read-only helpers for Fitatu static resources."""

    def __init__(self, client: FitatuApiClient) -> None:
        self._client = client

    def get_food_tags_recipe(self) -> Any:
        """Fetch recipe tag resources."""
        return self._client.request("GET", "/resources/food-tags/recipe")


class UserSettingsModule:
    """Helpers for user profile and settings endpoints."""

    def __init__(self, client: FitatuApiClient) -> None:
        self._client = client

    def get_profile(self, user_id: str) -> dict[str, Any]:
        """Fetch the user profile."""
        uid = validate_numeric_id(user_id, "user_id")
        data = self._client.request("GET", f"/users/{uid}")
        return read_reply(data, f"GET /users/{uid}")

    def update_profile(self, user_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        """Patch the user profile."""
        uid = validate_numeric_id(user_id, "user_id")
        data = self._client.request("PATCH", f"/users/{uid}", json_data=payload)
        return write_reply(data)

    def get_for_day(self, user_id: str, day: date | str) -> dict[str, Any]:
        """Fetch settings resolved for a specific day.

        Deprecated: use :meth:`get_new`.
        """
        _warn_legacy_settings_read("user_settings.get_for_day")
        uid = validate_numeric_id(user_id, "user_id")
        path = f"/users/{uid}/settings/{as_date(day).isoformat()}"
        return read_reply(self._client.request("GET", path), f"GET {path}")

    def get(self, user_id: str, *, day: date | str | None = None) -> dict[str, Any]:
        """Fetch current user settings.

        Deprecated: use :meth:`get_new`.
        """
        _warn_legacy_settings_read("user_settings.get")
        uid = validate_numeric_id(user_id, "user_id")
        data = self._client.request(
            "GET",
            f"/users/{uid}/settings",
            params={"date": as_date(day).isoformat()} if day is not None else None,
        )
        return read_reply(data, f"GET /users/{uid}/settings")

    def get_new(self, user_id: str, day: date | str | None = None) -> dict[str, Any]:
        """Fetch the `settings-new` payload resolved for ``day`` (defaults to today).

        This is where the mobile app reads goals from: energy and macro targets
        (``userDietSettings``), BMR/TMR (``calculatedValues``), the weekly meal schema,
        water and planner settings. The GET route needs the date segment; the bare
        ``/settings-new`` path only accepts PATCH, which is why it answered 405.
        """
        uid = validate_numeric_id(user_id, "user_id")
        target_day = as_date(day) if day is not None else date.today()
        path = f"/users/{uid}/settings-new/{target_day.isoformat()}"
        return read_reply(self._client.request("GET", path), f"GET {path}")

    def update_new(self, user_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        """Patch the `settings-new` payload."""
        uid = validate_numeric_id(user_id, "user_id")
        data = self._client.request("PATCH", f"/users/{uid}/settings-new", json_data=payload)
        return write_reply(data)

    def get_premium(self, user_id: str) -> dict[str, Any]:
        """Whether a premium plan runs today and until when: ``{"active": bool, "end_date": str | None}``.

        Nothing else leaves this method: ``GET /users/{id}/subscriptions`` also carries prices,
        currencies and store transaction ids (checked live 2026-10-14). ``end_date`` is the latest
        end (``YYYY-MM-DD``) of the plans running today; None when none runs or one has no end.
        """
        uid = validate_numeric_id(user_id, "user_id")
        path = f"/users/{uid}/subscriptions"
        reply = read_reply(self._client.request("GET", path), f"GET {path}")
        today = date.today().isoformat()
        # shortcut: the dates' format was not recorded; their first 10 characters are taken as YYYY-MM-DD.
        # Pin it once a reply with a plan is recorded.
        ends = [
            str(plan.get("endDate") or "9999-12-31")[:10]
            for plan in as_dict_list(reply.get("subscriptions"))
            if str(plan.get("startDate") or "")[:10] <= today <= str(plan.get("endDate") or "9999-12-31")[:10]
        ]
        return {"active": bool(ends), "end_date": None if not ends or max(ends) == "9999-12-31" else max(ends)}

    def update_water_settings(self, user_id: str, *, unit_capacity: int) -> dict[str, Any]:
        """Update water settings inside the `settings-new` payload."""
        uid = validate_numeric_id(user_id, "user_id")
        validate_positive_int(unit_capacity, "unit_capacity")
        data = self._client.request(
            "PATCH",
            f"/users/{uid}/settings-new",
            json_data={"userId": uid, "waterSettings": {"unitCapacity": unit_capacity}},
        )
        return write_reply(data)


class DietPlanModule:
    """Helpers for diet plan settings endpoints.

    Deprecated: every method here has a replacement in ``user_settings.get_new``.
    """

    def __init__(self, client: FitatuApiClient) -> None:
        self._client = client

    def get_settings(self, user_id: str) -> dict[str, Any]:
        """Fetch diet plan settings. Deprecated: use ``user_settings.get_new``."""
        _warn_legacy_settings_read("diet_plan.get_settings")
        uid = validate_numeric_id(user_id, "user_id")
        data = self._client.request("GET", f"/diet-plan/{uid}/settings")
        return read_reply(data, f"GET /diet-plan/{uid}/settings")

    def get_default_meal_schema(self, user_id: str) -> Any:
        """Fetch the default meal schema. Deprecated: use ``user_settings.get_new``."""
        _warn_legacy_settings_read("diet_plan.get_default_meal_schema")
        uid = validate_numeric_id(user_id, "user_id")
        return self._client.request("GET", f"/diet-plan/{uid}/settings/preferences/meal-schema/default")

    def get_meal_schema(self, user_id: str) -> Any:
        """Fetch available meal schema preferences. Deprecated: use ``user_settings.get_new``."""
        _warn_legacy_settings_read("diet_plan.get_meal_schema")
        uid = validate_numeric_id(user_id, "user_id")
        return self._client.request("GET", f"/diet-plan/{uid}/settings/preferences/meal-schema")


class WaterModule:
    """Water tracking helpers."""

    def __init__(self, client: FitatuApiClient) -> None:
        self._client = client

    def get_day(self, user_id: str, day: date | str) -> dict[str, Any] | None:
        """Fetch water data for a given day, or ``None`` when the API sends an empty body."""
        uid = validate_numeric_id(user_id, "user_id")
        path = f"/water/{uid}/{as_date(day).isoformat()}"
        data = self._client.request("GET", path)
        return None if data is None else read_reply(data, f"GET {path}")

    def set_day(self, user_id: str, day: date | str, water_consumption_ml: int) -> dict[str, Any]:
        """Set absolute water consumption (ml) for a given day; safe to repeat."""
        uid = validate_numeric_id(user_id, "user_id")
        target_day = as_date(day)
        validate_non_negative_int(water_consumption_ml, "water_consumption_ml")
        data = self._client.request(
            "PUT",
            f"/water/{uid}/{target_day.isoformat()}",
            json_data={"waterConsumption": water_consumption_ml},
            idempotent=True,
        )
        return write_reply(data)

    def add_intake(self, user_id: str, day: date | str, amount_ml: int) -> dict[str, Any]:
        """Add water intake (ml) on top of the current consumption for a given day.

        This is a read-modify-write (GET the day, PUT the sum), not an atomic increment:
        a change made between the two calls is lost, and repeating the call after a
        timeout can count the amount twice. To retry safely, read the day and call
        :meth:`set_day` with the absolute total.
        """
        validate_non_negative_int(amount_ml, "amount_ml")
        current = self.get_day(user_id, day) or {}
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

        ``locale`` defaults to the session's search locale, as for food search.
        Pass ``bmr`` and ``sex`` (the profile's numeric value) to get ``energy``
        values computed for the user instead of the generic defaults.
        """
        locale = locale or self._client.auth.app_search_locale
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
        """Search activities by name (``bieganie``, ``rower``...).

        ``locale`` defaults to the session's search locale, as for food search.
        """
        # requests resolves "." and ".." path segments, so those would reach another route.
        if not isinstance(phrase, str) or not phrase.strip().strip("."):
            raise FitatuValidationError(f"phrase must be a non-empty search text, got {phrase!r}")
        locale = locale or self._client.auth.app_search_locale
        params = {**_page_params(page, limit), "locale": locale, "bmr": bmr, "sex": sex}
        data = self._client.request(
            "GET",
            f"/search/activity/{quote(phrase, safe='')}",
            params={key: value for key, value in params.items() if value is not None},
        )
        return as_dict_list(data)

    def get_recent(self, user_id: str, *, page: int = 1, limit: int = 20) -> list[dict[str, Any]]:
        """Return the user's recently logged activities."""
        uid = validate_numeric_id(user_id, "user_id")
        data = self._client.request(
            "GET",
            f"/search/activity/{uid}/last-used",
            params=_page_params(page, limit),
        )
        return as_dict_list(data)


def _date_range_params(from_date: date | str | None, to_date: date | str | None) -> dict[str, str]:
    """Build ``fromDate``/``toDate`` query params, rejecting reversed ranges up front."""
    start = as_date(from_date, "from_date") if from_date is not None else None
    end = as_date(to_date, "to_date") if to_date is not None else None
    if start is not None and end is not None and start > end:
        raise FitatuValidationError(f"from_date {start} is after to_date {end}")
    params: dict[str, str] = {}
    if start is not None:
        params["fromDate"] = start.isoformat()
    if end is not None:
        params["toDate"] = end.isoformat()
    return params


def _unit(value: str | None, name: str, allowed: tuple[str, ...]) -> str | None:
    """Return *value* upper-cased when it is one of *allowed*; ``None`` stays ``None``."""
    if value is None:
        return None
    unit = str(value).strip().upper()
    if unit not in allowed:
        raise FitatuValidationError(f"{name} must be one of {allowed}, got {value!r}")
    return unit


def _check_measurement(value: Any, name: str) -> None:
    """Raise FitatuValidationError unless *value* is ``None`` or a finite number >= 0.

    Numeric strings pass, since tool calls often send them; a decimal comma does not.
    """
    if value is None:
        return
    try:
        number = float(value)
    except (TypeError, ValueError):
        number = math.nan
    if isinstance(value, bool) or not math.isfinite(number) or number < 0:
        raise FitatuValidationError(f"{name} must be a non-negative number, got {value!r}")


class MeasurementsModule:
    """Body weight and body-size measurements (the "Measurements" tab in the app)."""

    BODY_PARTS = ("neck", "chest", "waist", "stomach", "hips", "thigh", "calf", "biceps")
    WEIGHT_UNITS = ("KG", "LB", "ST_LB")
    SIZE_UNITS = ("CM", "IN", "FT_IN")

    def __init__(self, client: FitatuApiClient) -> None:
        self._client = client

    def get_day(self, user_id: str, day: date | str) -> dict[str, Any] | None:
        """Return the measurement saved for ``day``, or ``None`` when there is none (404)."""
        uid = validate_numeric_id(user_id, "user_id")
        path = f"/users/{uid}/measurements/{as_date(day).isoformat()}"
        try:
            data = self._client.request("GET", path)
        except FitatuApiError as exc:
            if exc.status_code == 404:
                return None
            raise
        # Only a 404 means "no record": set_day merges against this before a full-replacement PUT.
        return read_reply(data, f"GET {path}")

    def set_day(
        self,
        user_id: str,
        day: date | str,
        *,
        weight: float | None = None,
        fat_percentage: float | None = None,
        weight_unit: str | None = None,
        size_unit: str | None = None,
        merge: bool = True,
        **body_parts: float | None,
    ) -> dict[str, Any]:
        """Save weight and/or body sizes for ``day``.

        Body parts are passed as keyword arguments named after ``BODY_PARTS``, e.g.
        ``set_day(uid, day, weight=72.4, waist=81)``. The endpoint replaces the whole
        record, so with ``merge=True`` (default) values already stored for that day are
        read first and kept unless overridden. ``None`` means "not given" for every value,
        never "erase it"; to drop a stored value, rewrite the day with ``merge=False``.

        Units: ``KG``/``LB``/``ST_LB`` for weight and ``CM``/``IN``/``FT_IN`` for sizes,
        in any case. When omitted they follow the stored record (so merged values keep
        their unit), falling back to ``KG`` and ``CM``.
        """
        uid = validate_numeric_id(user_id, "user_id")
        target_day = as_date(day)
        weight_unit = _unit(weight_unit, "weight_unit", self.WEIGHT_UNITS)
        size_unit = _unit(size_unit, "size_unit", self.SIZE_UNITS)
        unknown = set(body_parts) - set(self.BODY_PARTS)
        if unknown:
            raise FitatuValidationError(
                f"unknown body parts: {sorted(unknown)}; expected any of {self.BODY_PARTS}"
            )
        body_parts = {part: size for part, size in body_parts.items() if size is not None}
        for name, value in (("weight", weight), ("fat_percentage", fat_percentage), *body_parts.items()):
            _check_measurement(value, name)

        values: dict[str, Any] = {}
        existing = (self.get_day(uid, target_day) or {}) if merge else {}
        if existing:
            values.update({key: existing.get(key) for key in ("weight", "fatPercentage", *self.BODY_PARTS)})
        if weight is not None:
            values["weight"] = weight
        if fat_percentage is not None:
            values["fatPercentage"] = fat_percentage
        values.update(body_parts)
        # Units label the whole record. Switching unit while keeping stored values would
        # silently reinterpret them (40 cm -> 40 in), so make the caller decide.
        for unit, unit_key, keys in (
            (weight_unit, "weightUnit", ("weight",)),
            (size_unit, "sizeUnit", self.BODY_PARTS),
        ):
            stored_unit = existing.get(unit_key)
            kept = [k for k in keys if existing.get(k) is not None and k not in body_parts and not (k == "weight" and weight is not None)]
            if unit and stored_unit and unit != stored_unit and kept:
                raise FitatuValidationError(
                    f"{unit_key} {unit} differs from the stored {stored_unit}; pass {kept} in {unit} too, "
                    "or use merge=False"
                )
        if not any(value is not None for value in values.values()):
            raise FitatuValidationError("provide weight, fat_percentage or at least one body part")

        # The app serialises every number as a string (double.toString()); match it.
        payload: dict[str, Any] = {
            key: str(float(value)) for key, value in values.items() if value is not None
        }
        payload.update(
            {
                "date": target_day.isoformat(),
                "weightUnit": weight_unit or existing.get("weightUnit") or "KG",
                "sizeUnit": size_unit or existing.get("sizeUnit") or "CM",
            }
        )
        payload.setdefault("fatPercentage", "0.0")
        data = self._client.request(
            "PUT",
            f"/users/{uid}/measurements/{target_day.isoformat()}",
            json_data=payload,
            idempotent=True,
        )
        return write_reply(data)

    def delete_day(self, user_id: str, day: date | str) -> dict[str, Any]:
        """Delete the whole measurement record for ``day``; safe to repeat.

        A day with nothing stored counts as deleted (``{}``), so a retry of a delete that
        went through before a timeout or a 5xx does not come back as a 404.
        """
        uid = validate_numeric_id(user_id, "user_id")
        try:
            data = self._client.request(
                "DELETE", f"/users/{uid}/measurements/{as_date(day).isoformat()}", idempotent=True
            )
        except FitatuApiError as exc:
            if exc.status_code == 404:
                return {}
            raise
        return write_reply(data)

    def get_weight_history(
        self,
        user_id: str,
        *,
        from_date: date | str | None = None,
        to_date: date | str | None = None,
        page: int = 1,
        limit: int = 41,
    ) -> list[dict[str, Any]]:
        """Return one page of weight entries as ``[{date, value, difference, direction}, ...]``.

        Newest first. Without ``from_date`` and ``to_date`` the page holds every entry up to
        ``limit``, however old: there is no default period (checked live 2026-10-14).
        ``direction`` follows the goal type: 1 = gain, -1 = reduction, 0 = maintenance.
        The endpoint is paged (the app asks for 41 rows); keep increasing ``page`` until a
        page comes back shorter than ``limit``.
        """
        uid = validate_numeric_id(user_id, "user_id")
        params: dict[str, Any] = {**_date_range_params(from_date, to_date), **_page_params(page, limit)}
        data = self._client.request("GET", f"/users/{uid}/measurements/summary/weight", params=params)
        return as_dict_list(data)

    def get_body_part_history(
        self,
        user_id: str,
        body_part: str,
        *,
        from_date: date | str | None = None,
        to_date: date | str | None = None,
        page: int = 1,
        limit: int = 41,
    ) -> list[dict[str, Any]]:
        """Return ``[{date, value}, ...]`` for one of ``BODY_PARTS``."""
        uid = validate_numeric_id(user_id, "user_id")
        if body_part not in self.BODY_PARTS:
            raise FitatuValidationError(f"body_part must be one of {self.BODY_PARTS}, got {body_part!r}")
        params: dict[str, Any] = {**_date_range_params(from_date, to_date), **_page_params(page, limit)}
        data = self._client.request("GET", f"/users/{uid}/measurements/size/{body_part}", params=params)
        return as_dict_list(data)

    def get_weight_chart(
        self,
        user_id: str,
        *,
        from_date: date | str | None = None,
        to_date: date | str | None = None,
    ) -> dict[str, Any]:
        """Return the weight chart: ``{weights: {date: kg}, targets: {date: kg}, weightUnit}``."""
        uid = validate_numeric_id(user_id, "user_id")
        data = self._client.request(
            "GET",
            f"/users/{uid}/measurements/chart/weight",
            params=_date_range_params(from_date, to_date) or None,
        )
        return read_reply(data, f"GET /users/{uid}/measurements/chart/weight")

    def get_size_summary(
        self,
        user_id: str,
        *,
        from_date: date | str | None = None,
        to_date: date | str | None = None,
    ) -> dict[str, Any]:
        """Return the change of every body size (and fat %) across the range."""
        uid = validate_numeric_id(user_id, "user_id")
        data = self._client.request(
            "GET",
            f"/users/{uid}/measurements/summary/size",
            params=_date_range_params(from_date, to_date) or None,
        )
        return read_reply(data, f"GET /users/{uid}/measurements/summary/size")


def _nutrient(value: str) -> str:
    """Return *value* when it looks like an API nutrient name, else raise FitatuValidationError."""
    if not (isinstance(value, str) and value.isascii() and value.isalnum()):
        raise FitatuValidationError(f"nutrient must be a camelCase name such as 'protein', got {value!r}")
    return value


class SummaryModule:
    """Server-side aggregates over a date range, plus the diary export.

    These are what the app's "Summary" tab uses, so there is no need to fetch and add
    up every planner day client-side.
    """

    EXPORT_FORMATS = ("csv", "xls", "pdf", "txt")

    def __init__(self, client: FitatuApiClient) -> None:
        self._client = client

    def get_nutrition(self, user_id: str, from_date: date | str, to_date: date | str) -> dict[str, Any]:
        """Return the range summary per nutrient, e.g. ``{"energy": {"current", "eaten", "min", "max"}, ...}``.

        ``current`` is the total of every row over the whole range, not a daily average (checked
        live 2026-10-13; the recording covers one day); ``eaten`` counts only the rows ticked as
        eaten (null when none is), and ``min``/``max`` are the goals summed over the days
        (checked live 2026-10-14).
        """
        uid = validate_numeric_id(user_id, "user_id")
        path = f"/v2/diet-plan/{uid}/summary/custom"
        data = self._client.request("GET", path, params=_date_range_params(from_date, to_date))
        return read_reply(data, f"GET {path}")

    def get_nutrient_by_day(
        self, user_id: str, nutrient: str, from_date: date | str, to_date: date | str
    ) -> dict[str, Any]:
        """Return one nutrient day by day: ``{measures, targets}`` for energy (kcal per day),
        ``{measures, targetRanges}`` (``{min, max}`` per day) for protein, fat and carbohydrate.

        Every day of the range is a key of ``measures``; it counts every row, planned or eaten,
        and is null on a day with nothing logged (checked live 2026-10-14).

        ``nutrient`` uses the API's camelCase names: ``energy``, ``protein``, ``fat``,
        ``carbohydrate``, ``fiber``, ``sugars``, ``saturatedFat``, ``salt``, ``vitaminC``...
        """
        uid = validate_numeric_id(user_id, "user_id")
        path = f"/v2/diet-plan/{uid}/summary/{_nutrient(nutrient)}/custom"
        data = self._client.request("GET", path, params=_date_range_params(from_date, to_date))
        return read_reply(data, f"GET {path}")

    def get_top_products(
        self,
        user_id: str,
        nutrient: str,
        from_date: date | str,
        to_date: date | str,
        *,
        page: int = 1,
        limit: int = 20,
    ) -> dict[str, Any]:
        """Return the foods that contributed most of ``nutrient``: ``{list, count}``.

        ``list`` rows are ``{id, name, amount: {current, per100}}`` sorted by ``current``,
        highest first, with the numbers as strings; ``per100`` is null for a custom row, whose
        ``id`` is the row's (checked live 2026-10-14 for energy and protein).
        """
        uid = validate_numeric_id(user_id, "user_id")
        params: dict[str, Any] = {**_date_range_params(from_date, to_date), **_page_params(page, limit)}
        path = f"/diet-plan/{uid}/summary-product/{_nutrient(nutrient)}/custom"
        return read_reply(self._client.request("GET", path, params=params), f"GET {path}")

    def get_activities(self, user_id: str, from_date: date | str, to_date: date | str) -> dict[str, Any]:
        """Return burned energy and activity totals for the range."""
        uid = validate_numeric_id(user_id, "user_id")
        path = f"/activity-plan/{uid}/summary/custom"
        data = self._client.request("GET", path, params=_date_range_params(from_date, to_date))
        return read_reply(data, f"GET {path}")

    def export_diary(
        self, user_id: str, from_date: date | str, to_date: date | str, *, fmt: str = "csv"
    ) -> bytes | None:
        """Download the food diary for a date range as ``csv``, ``xls``, ``pdf`` or ``txt``.

        Returns the file content, or ``None`` when the range has no entries (the API
        answers 204 in that case).
        """
        uid = validate_numeric_id(user_id, "user_id")
        if fmt not in self.EXPORT_FORMATS:
            raise FitatuValidationError(f"fmt must be one of {self.EXPORT_FORMATS}, got {fmt!r}")
        params = {"format": fmt, **_date_range_params(from_date, to_date)}
        data = self._client.request("GET", f"/diet-plan/{uid}/export", params=params, raw=True)
        return data if isinstance(data, bytes) else None


class FastingModule:
    """Intermittent fasting, read only: the fasting schemes and the eating periods of a range."""

    def __init__(self, client: FitatuApiClient) -> None:
        self._client = client

    def get_types(self, user_id: str) -> list[dict[str, Any]]:
        """The fasting schemes, ``{id, symbol, currentlyInUse, rating, ratingCount, reviewsPerLocale}``:
        symbols such as ``16_08_hours``, ``5_2_days`` or ``custom`` (9 of them, checked live 2026-10-14)."""
        uid = validate_numeric_id(user_id, "user_id")
        return as_dict_list(self._client.request("GET", f"/intermittent-fasting/{uid}/types"))

    def get_summary(self, user_id: str, from_date: date | str, to_date: date | str) -> list[dict[str, Any]]:
        """Per fasting day of the range ``{date, eatingPeriods, expectedEatingPeriods}``, each period
        ``{date, from, to}`` with times of day; an empty list without fasting (checked live 2026-10-14)."""
        uid = validate_numeric_id(user_id, "user_id")
        dates = _date_range_params(as_date(from_date, "from_date"), as_date(to_date, "to_date"))
        params = {"from": dates["fromDate"], "to": dates["toDate"]}  # this route names them from and to
        return as_dict_list(self._client.request("GET", f"/intermittent-fasting/{uid}/summary", params=params))


class HabitsModule:
    """Habits, read only: the habit catalog, the user's habits and the app's good days."""

    def __init__(self, client: FitatuApiClient) -> None:
        self._client = client

    def get_catalog(self, user_id: str) -> list[dict[str, Any]]:
        """The habits the user can take up: ``{id, name, translationKey, length, premium}``, ``length`` in
        days and ``name`` in the account's language (22, checked live 2026-10-14)."""
        uid = validate_numeric_id(user_id, "user_id")
        return as_dict_list(self._client.request("GET", f"/habits/{uid}"))

    def get_user_habits(self, user_id: str) -> list[dict[str, Any]]:
        """The user's habits: ``{completed, enrolledDate, habit, habitItems}``, ``habit`` as in
        :meth:`get_catalog` and ``habitItems`` the checked days, ``{dayNumber, date}``."""
        uid = validate_numeric_id(user_id, "user_id")
        return as_dict_list(self._client.request("GET", f"/users/{uid}/habits"))

    def get_good_days(self, user_id: str) -> list[str]:
        """The dates the app's summary marks as good days, all of them (a list of date strings)."""
        uid = validate_numeric_id(user_id, "user_id")
        data = self._client.request("GET", f"/habits/{uid}/good-days")
        return [str(day) for day in data] if isinstance(data, list) else []


class FoodsModule:
    """Favourite and recently used foods."""

    def __init__(self, client: FitatuApiClient) -> None:
        self._client = client

    def get_recent(self, user_id: str, meal_type: str) -> list[dict[str, Any]]:
        """Return foods recently logged in a meal (``breakfast``, ``second breakfast``, ``lunch``...).

        The meal name is normalised like the planner's (:meth:`PlannerModule.normalize_meal_key`).
        """
        uid = validate_numeric_id(user_id, "user_id")
        meal = PlannerModule.normalize_meal_key(meal_type)
        data = self._client.request("GET", f"/search/food/{uid}/last-used", params={"meal": meal})
        return as_dict_list(data)

    def get_recent_own(self, user_id: str, *, page: int = 1, limit: int = 50) -> list[dict[str, Any]]:
        """Return the user's own products/recipes, most recently used first."""
        uid = validate_numeric_id(user_id, "user_id")
        data = self._client.request(
            "GET",
            f"/search/food/user/{uid}/last-used",
            params=_page_params(page, limit),
        )
        return as_dict_list(data)

    def get_favorites(
        self,
        user_id: str,
        *,
        phrase: str | None = None,
        page: int = 1,
        limit: int = 50,
    ) -> list[dict[str, Any]]:
        """Return favourite products and recipes, optionally filtered by ``phrase``."""
        uid = validate_numeric_id(user_id, "user_id")
        params: dict[str, Any] = _page_params(page, limit)
        if phrase:
            params["phrase"] = phrase
        data = self._client.request("GET", f"/users/{uid}/favorite/foods", params=params)
        return as_dict_list(data)

    def set_favorite(
        self,
        user_id: str,
        food_id: int | str,
        *,
        favorite: bool = True,
        food_type: str = "PRODUCT",
    ) -> dict[str, Any]:
        """Mark (or unmark) a product or recipe as favourite; safe to repeat.

        The API processes this asynchronously (202). Two opposite calls sent back to
        back for the same food can be applied in either order, so give it a moment
        before flipping the same item again.
        """
        uid = validate_numeric_id(user_id, "user_id")
        fid = validate_numeric_id(food_id, "food_id")
        validate_bool(favorite, "favorite")
        kind = str(food_type).upper()
        if kind not in {"PRODUCT", "RECIPE"}:
            raise FitatuValidationError(f"food_type must be PRODUCT or RECIPE, got {food_type!r}")
        data = self._client.request(
            "PUT",
            f"/users/{uid}/favorite/food/{kind}/{fid}",
            json_data={"markAsFavorite": favorite},
            idempotent=True,
        )
        return write_reply(data)
