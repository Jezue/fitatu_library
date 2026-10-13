"""Tools for AI agents: the Fitatu diary as JSON-in, JSON-out functions.

``TOOLS`` lists every tool with a description written for a language model, a JSON
Schema for its input and MCP-style annotations (``readOnlyHint``, ``destructiveHint``,
``idempotentHint``); :func:`anthropic_tools` and :func:`openai_tools` give the same list
in those providers' formats. :meth:`FitatuTools.call` runs a tool by name and never
raises::

    with FitatuTools.from_session() as tools:
        result = tools.call("get_day", {"date": "2026-10-13"})
        # {"ok": True, "data": {...}} or {"ok": False, "error": {"error_type": ..., ...}}

Tools always act for the session's own user. Session handling (tokens, login) and writes
to the shared food catalog are deliberately not tools.
"""

from __future__ import annotations

import datetime as dt
import json
from pathlib import Path
from typing import Any, NotRequired, TypedDict, cast

from ._shapes import as_dict, as_dict_list
from ._summaries import MACRO_FIELDS, daily_targets, day_summary
from ._validation import as_date
from .client import FitatuApiClient
from .exceptions import (
    FitatuAuthError,
    FitatuNotFoundError,
    FitatuValidationError,
    describe_error,
)
from .planner import PlannerModule, _new_row_id

MEALS = ("breakfast", "second_breakfast", "lunch", "dinner", "snack", "supper")
FOOD_TYPES = ("PRODUCT", "RECIPE")

_DATE = {"type": "string", "format": "date", "description": "Day as YYYY-MM-DD."}
_MEAL = {"type": "string", "enum": list(MEALS), "description": "Meal of the day."}
_FOOD_ID = {"type": "integer", "minimum": 1, "description": "food_id from search_food, get_recent_foods or get_favorite_foods."}
_FOOD_TYPE = {"type": "string", "enum": list(FOOD_TYPES), "description": "type of that search hit."}
_LIMIT = {"type": "integer", "minimum": 1, "default": 10, "description": "Maximum number of hits."}
_ROW_ID = {"type": "string", "description": "row_id of a food row on that day, from get_day (meals[].items[].row_id)."}

def _new_row_id_schema(to_change: str) -> dict[str, Any]:
    return {
        "type": "string",
        "description": "Optional UUID version 1 for the new row; omit it to get a new id. Repeating the add with "
        "the same row_id and values does not log a second row: when a failed add's hint names a row_id, repeat it "
        f"with that row_id. Never a row_id from get_day: {to_change}",
    }


_NEW_ROW_ID = _new_row_id_schema(
    "update_item changes a logged row's quantity and eaten; to change anything else, remove the row and add it again."
)
_NEW_ACTIVITY_ID = _new_row_id_schema("a logged activity cannot be changed; remove it with remove_activity and add it again.")
_EATEN = {"type": "boolean", "default": False, "description": "true when already eaten, false (default) when only planned."}
_NON_NEGATIVE = {"type": "number", "minimum": 0}


def _tool(
    name: str,
    description: str,
    properties: dict[str, Any],
    required: tuple[str, ...] = (),
    *,
    read_only: bool = False,
    destructive: bool = False,
    idempotent: bool = False,
) -> dict[str, Any]:
    return {
        "name": name,
        "description": description,
        "input_schema": {
            "type": "object",
            "properties": properties,
            "required": list(required),
            "additionalProperties": False,
        },
        "annotations": {
            "readOnlyHint": read_only,
            "destructiveHint": destructive,
            "idempotentHint": read_only or idempotent,
        },
    }


TOOLS: list[dict[str, Any]] = [
    _tool(
        "get_day",
        "Read one diary day: every logged food per meal (row_id, name, measure_name and "
        "measure_quantity, weight in g, energy in kcal, protein/fat/carbohydrate/fiber/sugars/salt "
        "in g, eaten), per-meal and day totals, and logged activities (row_id, name, energy in "
        "kcal burned). totals count every row, eaten_totals only rows marked eaten; burned energy "
        "is not subtracted. Read the day first to get the row_ids that update_item, move_item and "
        "remove_items take.",
        {"date": _DATE},
        ("date",),
        read_only=True,
    ),
    _tool(
        "get_targets",
        "Read the user's daily goals for a day: energy in kcal, protein/fat/carbohydrate as "
        "grams and percent of energy, and BMR/TMR in kcal. date is the day asked for; "
        "settings_since is when these goals took effect (an earlier day is normal).",
        {"date": _DATE},
        ("date",),
        read_only=True,
    ),
    _tool(
        "get_range_summary",
        "Read Fitatu's own summary of a date range, as the app's Summary tab shows it: nutrition "
        "(energy in kcal; protein, fat, carbohydrate, fiber, sugars, salt in g) and "
        "activity_energy (kcal burned). Nutrition is the total over the whole range, not a daily "
        "average: divide by the number of days for one.",
        {"from_date": _DATE, "to_date": _DATE},
        ("from_date", "to_date"),
        read_only=True,
    ),
    _tool(
        "search_food",
        "Search the Fitatu food catalog (Polish names work best). Each hit has food_id and type "
        "(PRODUCT or RECIPE) for add_food and get_food_measures, name, brand, and its default "
        "measure (measure_id, name, weight_g or capacity_ml of one measure) with energy_kcal "
        "for one such measure.",
        {"phrase": {"type": "string", "description": "What to look for, e.g. 'jajko' or 'owsianka'."}, "limit": _LIMIT},
        ("phrase",),
        read_only=True,
    ),
    _tool(
        "get_food_measures",
        "List the measures a food can be logged in: measure_id, name (e.g. 'g', 'portion', "
        "'piece'), weight_g or capacity_ml and energy_kcal of one measure. Pass measure_id and a "
        "quantity of that measure to add_food.",
        {"food_id": _FOOD_ID, "food_type": _FOOD_TYPE},
        ("food_id", "food_type"),
        read_only=True,
    ),
    _tool(
        "get_recent_foods",
        "List foods the user recently logged in a meal, most used first, in the same shape as "
        "search_food hits. Handy for 'my usual breakfast'.",
        {"meal": _MEAL, "limit": _LIMIT},
        ("meal",),
        read_only=True,
    ),
    _tool(
        "get_favorite_foods",
        "List the user's favourite products and recipes, in the same shape as search_food hits.",
        {"limit": _LIMIT},
        read_only=True,
    ),
    _tool(
        "get_weight_history",
        "List logged body weights as {date, value, difference, direction}: value in the user's "
        "weight unit (kg unless changed in the app), difference from the previous entry, "
        "direction the weight goal (1 gain, -1 lose, 0 keep). Pass from_date and to_date to "
        "choose the period.",
        {
            "from_date": _DATE,
            "to_date": _DATE,
            "limit": {**_LIMIT, "default": 41, "description": "Maximum number of entries."},
        },
        read_only=True,
    ),
    _tool(
        "get_water",
        "Read how much water the user drank on a day, in ml.",
        {"date": _DATE},
        ("date",),
        read_only=True,
    ),
    _tool(
        "search_activity",
        "Search the activity (workout) catalog, e.g. 'bieganie' (running) or 'rower' (cycling). "
        "Each hit has activity_id for add_activity, name, and energy_kcal: the catalog's estimate "
        "for the effort in energy_for (e.g. '5 km w 00:30:00') for an average person. Scale it to "
        "what the user did to get the energy_kcal that add_activity needs.",
        {"phrase": {"type": "string", "description": "Activity name, e.g. 'bieganie'."}, "limit": _LIMIT},
        ("phrase",),
        read_only=True,
    ),
    _tool(
        "add_food",
        "Log a catalog food in a meal; returns its row_id. food_id and food_type come from "
        "search_food, get_recent_foods or get_favorite_foods. For a PRODUCT give either unit 'g' "
        "or 'ml' with quantity in grams or millilitres, or measure_id (from the hit or "
        "get_food_measures) with quantity = how many of that measure. A RECIPE is logged in "
        "portions unless measure_id names another of its measures; unit is for products only. "
        "Each call adds a row unless row_id is reused. The row is not read back: ok says the sync "
        "accepted it, so read get_day to check what was logged.",
        {
            "date": _DATE,
            "meal": _MEAL,
            "food_id": _FOOD_ID,
            "food_type": _FOOD_TYPE,
            "quantity": {
                "type": "number",
                "exclusiveMinimum": 0,
                "description": "Grams or ml with unit, else how many of the measure.",
            },
            "measure_id": {"type": "integer", "minimum": 1, "description": "measure_id from search_food or get_food_measures."},
            "unit": {"type": "string", "enum": ["g", "ml"], "description": "Products only: quantity is in g or ml."},
            "eaten": _EATEN,
            "row_id": _NEW_ROW_ID,
        },
        ("date", "meal", "food_id", "food_type", "quantity"),
    ),
    _tool(
        "add_custom_item",
        "Log a food that is not in the catalog, by name and the nutrition of the whole amount "
        "eaten; returns its row_id. Each call adds a row unless row_id is reused.",
        {
            "date": _DATE,
            "meal": _MEAL,
            "name": {"type": "string", "description": "What it was, e.g. 'Kanapka z serem'."},
            "energy_kcal": {**_NON_NEGATIVE, "description": "Energy in kcal."},
            "protein_g": {**_NON_NEGATIVE, "description": "Protein in grams."},
            "fat_g": {**_NON_NEGATIVE, "description": "Fat in grams."},
            "carbohydrate_g": {**_NON_NEGATIVE, "description": "Carbohydrate in grams."},
            "eaten": _EATEN,
            "row_id": _NEW_ROW_ID,
        },
        ("date", "meal", "name", "energy_kcal", "protein_g", "fat_g", "carbohydrate_g"),
    ),
    _tool(
        "update_item",
        "Change a logged food row: its quantity (in the row's own measure, see measure_name and "
        "measure_quantity in get_day) and/or eaten. Nothing else can be changed: for another food, or "
        "a custom row's name or nutrition, remove the row and add it again. Activities cannot be "
        "updated: remove and add them again. ok says a re-read shows the change.",
        {
            "date": _DATE,
            "row_id": _ROW_ID,
            "quantity": {"type": "number", "exclusiveMinimum": 0, "description": "New measure_quantity."},
            "eaten": {"type": "boolean", "description": "true for eaten, false for only planned."},
        },
        ("date", "row_id"),
        destructive=True,
    ),
    _tool(
        "move_item",
        "Move a logged food row to another meal and/or day. The moved row gets a new row_id, "
        "returned here; the old one is gone. ok says a re-read confirms both.",
        {
            "date": _DATE,
            "row_id": _ROW_ID,
            "to_meal": {**_MEAL, "description": "Meal to move it to."},
            "to_date": {**_DATE, "description": "Day to move it to, as YYYY-MM-DD; default: the same day."},
        },
        ("date", "row_id", "to_meal"),
        destructive=True,
    ),
    _tool(
        "remove_items",
        "Delete logged food rows from a day by row_id (meals[].items[].row_id in get_day). Ids "
        "that are not food rows on the day (already deleted, or an activity's row_id) are listed "
        "in missing, so repeating the call is safe. With dry_run nothing is "
        "deleted and removed lists what would be. ok is false when a row was missing or is "
        "still there (failed).",
        {
            "date": _DATE,
            "row_ids": {"type": "array", "items": {"type": "string"}, "minItems": 1, "description": "row_ids from get_day."},
            "dry_run": {"type": "boolean", "default": False, "description": "Only report what would be deleted."},
        },
        ("date", "row_ids"),
        destructive=True,
        idempotent=True,
    ),
    _tool(
        "log_weight",
        "Save the user's body weight for a day in kg, replacing a weight already saved for that "
        "day; other body measurements of that day are kept.",
        {"date": _DATE, "weight_kg": {"type": "number", "exclusiveMinimum": 0, "description": "Body weight in kg."}},
        ("date", "weight_kg"),
        destructive=True,
        idempotent=True,
    ),
    _tool(
        "set_water",
        "Set the total water drunk on a day in ml. It replaces the day's total instead of adding "
        "to it: to add a glass, read get_water and send the sum.",
        {"date": _DATE, "water_ml": {"type": "integer", "minimum": 0, "description": "Total for the day in ml."}},
        ("date", "water_ml"),
        destructive=True,
        idempotent=True,
    ),
    _tool(
        "add_activity",
        "Log a workout from the activity catalog; returns its row_id. Fitatu stores energy_kcal "
        "as given and does not compute it (see search_activity). Each call adds an activity "
        "unless row_id is reused.",
        {
            "date": _DATE,
            "activity_id": {"type": "integer", "minimum": 1, "description": "activity_id from search_activity."},
            "minutes": {"type": "integer", "minimum": 1, "description": "Duration in minutes."},
            "energy_kcal": {"type": "integer", "minimum": 1, "description": "Energy burned in kcal."},
            "row_id": _NEW_ACTIVITY_ID,
        },
        ("date", "activity_id", "minutes", "energy_kcal"),
    ),
    _tool(
        "remove_activity",
        "Delete a logged activity by row_id (activities[].row_id in get_day, or from "
        "add_activity). An id that is not an activity on the day (already deleted, or a food "
        "row's row_id) gives ok false, reason item_not_found and a message listing the day's "
        "activities, so repeating the call is safe.",
        {"date": _DATE, "row_id": {"type": "string", "description": "row_id of an activity on that day."}},
        ("date", "row_id"),
        destructive=True,
        idempotent=True,
    ),
]

_SCHEMAS = {tool["name"]: tool["input_schema"] for tool in TOOLS}
_JSON_TYPES: dict[str, type | tuple[type, ...]] = {
    "string": str,
    "integer": int,
    "number": (int, float),
    "boolean": bool,
    "array": list,
}


# Both give a fresh copy that callers may adapt in place (e.g. for OpenAI strict mode): call()
# validates against TOOLS, and a JSON round trip, unlike deepcopy, also unshares the property
# schemas that several tools reuse (_DATE, _LIMIT).
def anthropic_tools() -> list[dict[str, Any]]:
    """``TOOLS`` in the Anthropic Messages API ``tools`` format, as a fresh copy."""
    return [
        {"name": t["name"], "description": t["description"], "input_schema": t["input_schema"]}
        for t in json.loads(json.dumps(TOOLS))
    ]


def openai_tools() -> list[dict[str, Any]]:
    """``TOOLS`` in the OpenAI function-calling ``tools`` format, as a fresh copy."""
    return [
        {"type": "function", "function": {"name": t["name"], "description": t["description"], "parameters": t["input_schema"]}}
        for t in json.loads(json.dumps(TOOLS))
    ]


def _checked_arguments(schema: dict[str, Any], arguments: Any) -> dict[str, Any]:
    """Return ``arguments`` once they fit ``schema``; ``null`` counts as an omitted argument."""
    if not isinstance(arguments, dict | None):
        raise FitatuValidationError(f"arguments must be a JSON object, got {type(arguments).__name__}")
    properties = schema["properties"]
    args = {key: value for key, value in (arguments or {}).items() if value is not None}
    unknown = [str(key) for key in args if key not in properties]
    if unknown:
        raise FitatuValidationError(f"unknown argument(s): {', '.join(unknown)}; this tool takes: {', '.join(properties) or 'nothing'}")
    missing = [key for key in schema["required"] if key not in args]
    if missing:
        raise FitatuValidationError(f"missing required argument(s): {', '.join(missing)}")
    for key, value in args.items():
        prop = properties[key]
        # JSON Schema counts 1500.0 as an integer, and json.loads reads it as a float
        if prop["type"] == "integer" and isinstance(value, float) and value.is_integer():
            args[key] = value = int(value)
        # bool is an int in Python, but true is not a number in JSON
        if isinstance(value, bool) is not (prop["type"] == "boolean") or not isinstance(value, _JSON_TYPES[prop["type"]]):
            raise FitatuValidationError(f"{key} must be a JSON {prop['type']}, got {value!r}")
        if "enum" in prop and value not in prop["enum"]:
            raise FitatuValidationError(f"{key} must be one of {', '.join(prop['enum'])}; got {value!r}")
        if "minimum" in prop and value < prop["minimum"]:
            raise FitatuValidationError(f"{key} must be at least {prop['minimum']}, got {value!r}")
        if "exclusiveMinimum" in prop and value <= prop["exclusiveMinimum"]:
            raise FitatuValidationError(f"{key} must be more than {prop['exclusiveMinimum']}, got {value!r}")
    return args


def _food(hit: dict[str, Any]) -> dict[str, Any]:
    """A food search row trimmed to what an agent needs to pick and log it."""
    measure = as_dict(hit.get("measure")) or {}
    return {
        "food_id": hit.get("foodId"),
        "type": hit.get("type"),
        "name": hit.get("name"),
        "brand": hit.get("brand") or None,
        "energy_kcal": hit.get("energy"),
        "measure": {
            "measure_id": measure.get("measureId"),
            "name": measure.get("measureName"),
            "weight_g": measure.get("measureWeight"),
            "capacity_ml": measure.get("measureCapacity"),
        },
    }


def _foods(hits: list[dict[str, Any]]) -> list[dict[str, Any]]:
    # Other hit types cannot be logged with add_food, so an agent should not see them.
    return [_food(hit) for hit in hits if hit.get("type") in FOOD_TYPES]


class DaySummary(TypedDict):
    """One diary day; ``meals[].items[].row_id`` and ``activities[].row_id`` name its rows."""

    user_id: str | None
    date: str
    totals: dict[str, float]
    eaten_totals: dict[str, float]
    meals: list[dict[str, Any]]
    activities: list[dict[str, Any]]


class DailyTargets(TypedDict):
    """Energy (kcal) and macro targets (``{grams, percent}``) with BMR/TMR (kcal).

    ``date`` is the day asked for; ``settings_since`` is when these goals took effect.
    """

    date: str
    settings_since: str | None
    energy: float | None
    protein: dict[str, float | None]
    fat: dict[str, float | None]
    carbohydrate: dict[str, float | None]
    bmr: float | None
    tmr: float | None


class AddResult(TypedDict):
    """The row a write left on the day (add_*, update_item, move_item).

    ``ok`` is the planner's check by re-reading the day where it makes one (updates,
    moves, activities); food adds report True once the sync accepted the row.
    """

    date: str
    meal: NotRequired[str]
    row_id: str
    ok: bool


class RemoveResult(TypedDict):
    """Row ids removed (in a dry run: that would be), still there (failed) and not on the day (missing)."""

    date: str
    ok: bool
    dry_run: bool
    removed: list[str]
    failed: list[str]
    missing: list[str]


class FitatuTools:
    """The agent tool layer over :class:`FitatuApiClient`; see :data:`TOOLS`.

    Each tool is also a method that returns its ``data`` and raises the library's typed
    errors; :meth:`call` is the never-raising entry point for agents.
    """

    def __init__(self, client: FitatuApiClient) -> None:
        self.client = client

    @classmethod
    def from_session(cls, path: str | Path | None = None, **client_kwargs: Any) -> FitatuTools:
        """Tools over :meth:`FitatuApiClient.from_session` (the ``fitatu-api login`` session file)."""
        return cls(FitatuApiClient.from_session(path, **client_kwargs))

    def close(self) -> None:
        """Close the client's HTTP connections; the tools are also a context manager."""
        self.client.close()

    def __enter__(self) -> FitatuTools:
        return self

    def __exit__(self, exc_type: object, exc: object, traceback: object) -> None:
        self.close()

    @property
    def _user_id(self) -> str:
        user_id = self.client.auth.fitatu_user_id
        if not user_id:
            raise FitatuAuthError("the session has no Fitatu user id; run `fitatu-api login`")
        return user_id

    def call(self, name: str, arguments: dict[str, Any] | None = None) -> dict[str, Any]:
        """Run tool ``name`` with JSON ``arguments``; never raises.

        Returns ``{"ok": True, "data": ...}`` or ``{"ok": False, "error": ...}`` with the
        error as :func:`describe_error` reports it. An unknown tool and arguments that do not
        fit its input schema are reported as ``validation`` errors before anything is sent.
        An add without ``row_id`` gets one here. An add that fails with anything but a
        ``validation``, ``not_found`` or ``sync_rejected`` error may have been saved (the
        client resends the day sync itself, and later requests can fail after it landed), so
        the error is not ``retryable`` and its hint names that ``row_id``.
        """
        row_id = None
        try:
            schema = _SCHEMAS.get(name)
            if schema is None:
                raise FitatuValidationError(f"unknown tool {name!r}; tools: {', '.join(_SCHEMAS)}")
            args = _checked_arguments(schema, arguments)
            if name.startswith("add_") and "row_id" not in args:
                row_id = args["row_id"] = _new_row_id()
            return {"ok": True, "data": getattr(self, name)(**args)}
        except Exception as exc:
            error = describe_error(exc)
            # Only these say the add was not saved; after a 429, 401 or break the sync may have landed.
            if row_id is not None and error["error_type"] not in ("validation", "not_found", "sync_rejected"):
                error["retryable"] = False
                retry = f"The add may have been saved: repeat it with row_id {row_id!r}, which cannot log it twice."
                error["hint"] = f"{error['hint']} {retry}" if error["hint"] else retry
            return {"ok": False, "error": error}

    # -- reads ----------------------------------------------------------------------
    def get_day(self, date: str) -> DaySummary:
        day = as_date(date, "date")
        summary = day_summary(self.client.planner.get_day(self._user_id, day), date=day.isoformat(), user_id=self._user_id)
        return cast(DaySummary, summary)

    def get_targets(self, date: str) -> DailyTargets:
        day = as_date(date, "date")
        return cast(DailyTargets, daily_targets(self.client.user_settings.get_new(self._user_id, day), day.isoformat()))

    def get_range_summary(self, from_date: str, to_date: str) -> dict[str, Any]:
        start, end = as_date(from_date, "from_date"), as_date(to_date, "to_date")
        nutrition = self.client.summary.get_nutrition(self._user_id, start, end)
        activities = self.client.summary.get_activities(self._user_id, start, end)
        return {
            "from_date": start.isoformat(),
            "to_date": end.isoformat(),
            "nutrition": {field: (as_dict(nutrition.get(field)) or {}).get("current") for field in MACRO_FIELDS},
            "activity_energy": activities.get("energy"),
        }

    def search_food(self, phrase: str, limit: int = 10) -> list[dict[str, Any]]:
        return _foods(self.client.search_food(phrase, limit=limit))

    def get_food_measures(self, food_id: int, food_type: str) -> dict[str, Any]:
        client = self.client
        details = client.get_recipe(food_id) if food_type == "RECIPE" else client.get_product_details(food_id)
        return {
            "food_id": food_id,
            "type": food_type,
            "name": details.get("name"),
            "measures": [
                {
                    "measure_id": measure.get("id"),
                    "name": measure.get("name"),
                    "weight_g": measure.get("weightPerUnit"),
                    "capacity_ml": measure.get("capacityPerUnit"),
                    "energy_kcal": measure.get("energyPerUnit"),
                }
                for measure in as_dict_list(details.get("measures"))
            ],
        }

    def get_recent_foods(self, meal: str, limit: int = 10) -> list[dict[str, Any]]:
        # The route has no limit and answers with up to 200 foods (checked live).
        return _foods(self.client.foods.get_recent(self._user_id, meal))[:limit]

    def get_favorite_foods(self, limit: int = 10) -> list[dict[str, Any]]:
        return _foods(self.client.foods.get_favorites(self._user_id, limit=limit))

    def get_weight_history(self, from_date: str | None = None, to_date: str | None = None, limit: int = 41) -> list[dict[str, Any]]:
        return self.client.measurements.get_weight_history(self._user_id, from_date=from_date, to_date=to_date, limit=limit)

    def get_water(self, date: str) -> dict[str, Any]:
        day = as_date(date, "date")
        water = as_dict((self.client.water.get_day(self._user_id, day) or {}).get("water")) or {}
        return {"date": day.isoformat(), "water_ml": int(water.get("waterConsumption") or 0)}

    def search_activity(self, phrase: str, limit: int = 10) -> list[dict[str, Any]]:
        return [
            {
                "activity_id": hit.get("activityId"),
                "name": hit.get("name"),
                "energy_kcal": hit.get("energy"),
                "energy_for": hit.get("measuresDescription"),
            }
            for hit in self.client.activities.search(phrase, limit=limit)
        ]

    # -- writes ---------------------------------------------------------------------
    def _meal_of(self, day: dt.date, row_id: str) -> str:
        """The meal a food row is in, found by row id only (the planner would also take a productId)."""
        day_data = self.client.planner.get_day(self._user_id, day)
        for meal, row in PlannerModule._iter_rows(day_data):
            if str(row.get("planDayDietItemId")) == row_id:
                return meal
        if PlannerModule._find_activity(day_data, row_id) is not None:
            raise FitatuNotFoundError(
                f"row_id {row_id!r} is an activity, not a food row; an activity cannot be changed: "
                "remove it with remove_activity and add it again"
            )
        raise FitatuNotFoundError(f"row_id {row_id!r} is not on {day}; read the day with get_day for its row ids")

    def add_food(
        self,
        date: str,
        meal: str,
        food_id: int,
        food_type: str,
        quantity: float,
        measure_id: int | None = None,
        unit: str | None = None,
        eaten: bool = False,
        row_id: str | None = None,
    ) -> AddResult:
        day = as_date(date, "date")
        planner = self.client.planner
        common: dict[str, Any] = {"meal_type": meal, "eaten": eaten, "row_id": row_id}
        if food_type == "RECIPE":
            if unit is not None:
                raise FitatuValidationError("unit is for products only; log a recipe in portions or pass a measure_id from get_food_measures")
            measure: dict[str, Any] = {} if measure_id is None else {"measure_id": measure_id}
            result = planner.add_recipe_to_day_meal(self._user_id, day, recipe_id=food_id, measure_quantity=quantity, **measure, **common)
        elif unit is not None and measure_id is not None:
            raise FitatuValidationError("pass unit or measure_id, not both")
        elif unit is not None:
            result = planner.add_product_to_day_meal_with_unit(self._user_id, day, product_id=food_id, amount=quantity, unit=unit, **common)
        elif measure_id is not None:
            result = planner.add_product_to_day_meal(
                self._user_id, day, product_id=food_id, measure_id=measure_id, measure_quantity=quantity, **common
            )
        else:
            raise FitatuValidationError("a PRODUCT needs unit 'g' or 'ml', or a measure_id from search_food or get_food_measures")
        return {"date": day.isoformat(), "meal": result["meal"], "row_id": result["rowId"], "ok": result["ok"]}

    def add_custom_item(
        self,
        date: str,
        meal: str,
        name: str,
        energy_kcal: float,
        protein_g: float,
        fat_g: float,
        carbohydrate_g: float,
        eaten: bool = False,
        row_id: str | None = None,
    ) -> AddResult:
        day = as_date(date, "date")
        result = self.client.planner.add_custom_item_to_day_meal(
            self._user_id,
            day,
            meal_type=meal,
            name=name,
            calories=energy_kcal,
            protein_g=protein_g,
            fat_g=fat_g,
            carbs_g=carbohydrate_g,
            eaten=eaten,
            row_id=row_id,
        )
        return {"date": day.isoformat(), "meal": result["meal"], "row_id": result["rowId"], "ok": result["ok"]}

    def update_item(self, date: str, row_id: str, quantity: float | None = None, eaten: bool | None = None) -> AddResult:
        if quantity is None and eaten is None:
            raise FitatuValidationError("pass quantity and/or eaten")
        day = as_date(date, "date")
        result = self.client.planner.update_day_item(
            self._user_id, day, meal_type=self._meal_of(day, row_id), item_id=row_id, measure_quantity=quantity, eaten=eaten
        )
        return {"date": day.isoformat(), "meal": result["meal"], "row_id": row_id, "ok": result["ok"]}

    def move_item(self, date: str, row_id: str, to_meal: str, to_date: str | None = None) -> AddResult:
        day = as_date(date, "date")
        target = None if to_date is None else as_date(to_date, "to_date")
        result = self.client.planner.move_day_item(
            self._user_id, day, from_meal_type=self._meal_of(day, row_id), item_id=row_id, to_day=target, to_meal_type=to_meal
        )
        return {"date": result["toDate"], "meal": result["toMeal"], "row_id": result["rowId"], "ok": result["ok"]}

    def remove_items(self, date: str, row_ids: list[str], dry_run: bool = False) -> RemoveResult:
        day = as_date(date, "date")
        if not row_ids:  # the planner reads row_ids=None as every row of the day
            raise FitatuValidationError("row_ids must list at least one row_id from get_day")
        result = self.client.planner.remove_day_items_by_kind(
            self._user_id, day, item_kind="auto", all_meals=True, row_ids=row_ids, dry_run=dry_run
        )
        if dry_run:
            removed, failed = [row["itemId"] for row in result["targets"]], []
        else:
            removed = [attempt["itemId"] for attempt in result["attempts"] if attempt["ok"]]
            failed = [attempt["itemId"] for attempt in result["attempts"] if not attempt["ok"]]
        return {
            "date": day.isoformat(),
            "ok": result["ok"],
            "dry_run": dry_run,
            "removed": removed,
            "failed": failed,
            "missing": result["missingRowIds"],
        }

    def log_weight(self, date: str, weight_kg: float) -> dict[str, Any]:
        day = as_date(date, "date")
        self.client.measurements.set_day(self._user_id, day, weight=weight_kg, weight_unit="KG")
        return {"date": day.isoformat(), "weight_kg": weight_kg}

    def set_water(self, date: str, water_ml: int) -> dict[str, Any]:
        day = as_date(date, "date")
        self.client.water.set_day(self._user_id, day, water_ml)
        return {"date": day.isoformat(), "water_ml": water_ml}

    def add_activity(self, date: str, activity_id: int, minutes: int, energy_kcal: int, row_id: str | None = None) -> AddResult:
        day = as_date(date, "date")
        result = self.client.planner.add_activity(
            self._user_id, day, activity_id=activity_id, minutes=minutes, energy=energy_kcal, row_id=row_id
        )
        return {"date": day.isoformat(), "row_id": result["rowId"], "ok": result["ok"]}

    def remove_activity(self, date: str, row_id: str) -> dict[str, Any]:
        day = as_date(date, "date")
        result = self.client.planner.remove_activity(self._user_id, day, item_id=row_id)
        return {
            "date": day.isoformat(),
            "row_id": row_id,
            "ok": result["ok"],
            "reason": result.get("reason"),
            "message": result.get("message"),
        }
