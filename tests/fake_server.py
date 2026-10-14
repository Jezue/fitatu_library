"""A fake Fitatu planner backend that behaves like the recorded one.

Every rule below is taken from ``tests/fixtures/api_recording.json`` (real responses,
see ``scripts/record_fixtures.py``), and ``tests/test_recorded_contract.py`` replays that
recording through this fake to keep the two in step:

- ``POST /diet-plan/{id}/days`` upserts rows by ``planDayDietItemId`` and keeps every row
  that is not in the payload; it answers ``[{date, revision, errorMessage}]``
- a row is replaced as a whole: fields that are not sent fall back to the server's
  defaults (``eaten`` becomes ``False``)
- a new row whose id is not a UUID v1 makes the server reject the whole day (still 202)
- rows sent with ``deletedAt`` disappear from the day GET
- the day GET always returns every meal of the day, empty or not, with the meal's own fields
  (``mealName``, the name the user gave it, ``mealTime``, ...)
- activities go in the same sync under ``activities``, upserted by ``uuid``, and only in
  the write shape; any other key (e.g. a row copied from the day GET) rejects the day
  with a field-by-field ``errorMessage``. The day GET lists them under ``activityPlan``
  in a longer read shape, without the ones sent with ``deletedAt``
- ``note`` and ``tagsIds`` sent in the sync replace the day's; a sync without them keeps them
  (checked live 2026-10-14, not recorded)

Nutrition is not recalculated; tests here are about ids, meals and flags.
"""

from __future__ import annotations

import copy
import json
import re
import uuid
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from fitatu_api import FitatuApiError, FitatuNotFoundError, PlannerModule

RECORDING = json.loads((Path(__file__).parent / "fixtures" / "api_recording.json").read_text(encoding="utf-8"))
CALLS: dict[str, Any] = RECORDING["calls"]
RECORDED_USER_ID: str = RECORDING["userId"]
# Recorded food searches by phrase: "jajko kurze" (PRODUCT hits), "owsianka" (RECIPE and PRODUCT hits).
RECORDED_SEARCHES = {CALLS[name]["request"]["query"]["phrase"]: name for name in ("search_food", "search_food_recipe")}

DAY_GET = re.compile(r"/diet-and-activity-plan/(?P<user>[^/]+)/day/(?P<day>\d{4}-\d{2}-\d{2})")
DAY_SYNC = re.compile(r"/diet-plan/(?P<user>[^/]+)/days")
REVISIONS = re.compile(r"/diet-plan/(?P<user>[^/]+)/days/(?P<day>\d{4}-\d{2}-\d{2})/revisions")


def recorded(name: str) -> Any:
    """Return a deep copy of a recorded response body."""
    return copy.deepcopy(CALLS[name]["body"])


# The keys the server puts on every row, with the values it uses when a row omits them.
ROW_DEFAULTS: dict[str, Any] = {
    **{key: None for key in recorded("day_three_rows")["dietPlan"]["snack"]["items"][0]},
    "eaten": False,
    "visible": True,
    "liquid": False,
}


# An activity as the day sync accepts it, and as the day GET returns it.
ACTIVITY_WRITE_KEYS = list(CALLS["sync_add_activity"]["request"]["body"]["2027-01-03"]["activities"][0])
ACTIVITY_READ_DEFAULTS: dict[str, Any] = {key: None for key in recorded("day_after_activity")["activityPlan"][0]}


def _activity_errors(row: dict[str, Any]) -> str:
    """The server's errorMessage for an ``activities`` row ("" when it is accepted)."""
    message = "".join(
        f"Array[{key}]:\n    This field was not expected. (code 7703c766-b5d5-4cef-ace7-ae0dd82304e9)\n"
        for key in row
        if key not in ACTIVITY_WRITE_KEYS
    )
    if not row.get("simpleMeasure") and not row.get("advancedMeasures"):
        message += "Array:\n    Either simpleMeasure or advancedMeasures must be set\n"
    return message


def _is_uuid_v1(value: Any) -> bool:
    try:
        return uuid.UUID(str(value)).version == 1
    except ValueError:
        return False


class FakeFitatuServer:
    """Stands in for ``FitatuApiClient`` in planner tests.

    ``day`` seeds the stored day (its meals define which meals exist; its rows are stored
    as they are). ``products``/``recipes``/``search_results`` back ``get_product_details``,
    ``get_recipe`` and ``search_food`` for planner flows that look things up; ``search_food``
    also serves the recorded searches.

    ``request`` also answers what a real client sends for everything else: a food search
    from ``search_results``, then ``routes`` (canned replies by ``(method, path)`` for routes
    the recording lacks), then the first recorded call with the same method and path; a
    recorded 4xx is raised as ``FitatuApiError`` with that status (a 404 as ``FitatuNotFoundError``).
    """

    def __init__(
        self,
        day: dict[str, Any] | None = None,
        *,
        products: dict[str, dict[str, Any]] | None = None,
        recipes: dict[str, dict[str, Any]] | None = None,
        search_results: dict[str, list[dict[str, Any]]] | None = None,
        user_id: str = RECORDED_USER_ID,
    ) -> None:
        skeleton = copy.deepcopy(day) if day is not None else recorded("day_empty")
        # Each meal's own fields (mealName, mealTime, ...) as seeded; the day GET returns them with its rows.
        self.meal_fields = {
            meal_key: {key: value for key, value in (bucket or {}).items() if key != "items"}
            for meal_key, bucket in (skeleton.get("dietPlan") or {}).items()
        }
        self.day_extras = {key: value for key, value in skeleton.items() if key not in {"dietPlan", "activityPlan"}}
        self.days: dict[str, dict[str, tuple[str, dict[str, Any]]]] = {}
        self.day_activities: dict[str, dict[str, dict[str, Any]]] = {}
        self.day_notes: dict[str, dict[str, Any]] = {}  # note and tagsIds per day, once a sync sends them
        self._activity_seed = skeleton.get("activityPlan") or []
        self._seed = [
            (meal_key, row)
            for meal_key, bucket in (skeleton.get("dietPlan") or {}).items()
            for row in (bucket or {}).get("items", [])
        ]
        self.products = {str(k): v for k, v in (products or {}).items()}
        self.recipes = {str(k): v for k, v in (recipes or {}).items()}
        self.search_results = {
            **{phrase: recorded(name) for phrase, name in RECORDED_SEARCHES.items()},
            **(search_results or {}),
        }
        self.routes: dict[tuple[str, str], Any] = {}
        self.requests: list[dict[str, Any]] = []
        self.sync_payloads: list[dict[str, Any]] = []
        self.auth = SimpleNamespace(fitatu_user_id=user_id)
        self.planner = PlannerModule(self)  # type: ignore[arg-type]

    # -- what the planner calls -------------------------------------------------
    def request(self, method: str, path: str, **kwargs: Any) -> Any:
        self.requests.append({"method": method, "path": path, **copy.deepcopy(kwargs)})
        if method == "GET" and (match := DAY_GET.fullmatch(path)):
            return self.day_view(match["day"])
        if method == "POST" and DAY_SYNC.fullmatch(path):
            payload = kwargs.get("json_data")
            if not isinstance(payload, dict):
                raise AssertionError("day sync without a JSON body")
            self.sync_payloads.append(copy.deepcopy(payload))
            return self._sync(payload)
        if method == "GET" and REVISIONS.fullmatch(path):
            return recorded("day_revisions")
        if method == "GET" and path == "/search/new/food":
            return self.search_food(kwargs["params"]["phrase"], limit=kwargs["params"]["limit"])
        if (method, path) in self.routes:
            return copy.deepcopy(self.routes[method, path])
        call = next((c for c in CALLS.values() if (c["request"]["method"], c["request"]["path"]) == (method, path)), None)
        if call is None:
            raise AssertionError(f"unexpected request: {method} {path}")
        if call["status"] >= 400:
            error = FitatuNotFoundError if call["status"] == 404 else FitatuApiError  # as the client raises it
            raise error(f"{method} {path} -> {call['status']}", status_code=call["status"], body=json.dumps(call["body"]))
        return copy.deepcopy(call["body"])

    def search_food(self, phrase: str, *, limit: int = 5, **kwargs: Any) -> list[dict[str, Any]]:
        return copy.deepcopy(self.search_results.get(phrase.lower(), [])[:limit])

    def get_product_details(self, product_id: int | str) -> dict[str, Any]:
        if str(product_id) not in self.products:
            raise FitatuNotFoundError(f"product {product_id} not found", status_code=404)
        return copy.deepcopy(self.products[str(product_id)])

    def get_recipe(self, recipe_id: int | str) -> dict[str, Any]:
        if str(recipe_id) not in self.recipes:
            raise FitatuNotFoundError(f"recipe {recipe_id} not found", status_code=404)
        return copy.deepcopy(self.recipes[str(recipe_id)])

    # -- server behaviour ---------------------------------------------------------
    def _rows(self, day: str) -> dict[str, tuple[str, dict[str, Any]]]:
        if day not in self.days:
            self.days[day] = {}
            for index, (meal_key, row) in enumerate(self._seed):
                self.days[day][str(row.get("planDayDietItemId") or f"seed-{index}")] = (meal_key, copy.deepcopy(row))
        return self.days[day]

    def _activities(self, day: str) -> dict[str, dict[str, Any]]:
        if day not in self.day_activities:
            self.day_activities[day] = {str(row["uuid"]): copy.deepcopy(row) for row in self._activity_seed}
        return self.day_activities[day]

    def _sync(self, payload: dict[str, Any]) -> list[dict[str, Any]]:
        results = []
        for day, day_payload in payload.items():
            activities = (day_payload or {}).get("activities") or []
            activity_errors = "".join(_activity_errors(row) for row in activities)
            if activity_errors:
                results.append({"date": day, "revision": None, "errorMessage": activity_errors})
                continue
            stored = self._rows(day)
            incoming = [
                (meal_key, row)
                for meal_key, bucket in ((day_payload or {}).get("dietPlan") or {}).items()
                for row in (bucket or {}).get("items", [])
            ]
            bad = next(
                (row for _, row in incoming if str(row.get("planDayDietItemId")) not in stored and not _is_uuid_v1(row.get("planDayDietItemId"))),
                None,
            )
            if bad is not None:
                message = f"Invalid pddiId UUID version, only version 1 is acceptable,UUID: {bad.get('planDayDietItemId')}."
                results.append({"date": day, "revision": None, "errorMessage": message})
                continue
            for meal_key, row in incoming:
                stored[str(row["planDayDietItemId"])] = (meal_key, {**ROW_DEFAULTS, **self._catalog_fields(row), **row})
            for row in activities:
                self._activities(day)[str(row["uuid"])] = self._activity_read_shape(day, row)
            self.day_notes.setdefault(day, {}).update({key: day_payload[key] for key in ("note", "tagsIds") if key in (day_payload or {})})
            results.append({"date": day, "revision": str(uuid.uuid1()), "errorMessage": None})
        return results

    @staticmethod
    def _activity_read_shape(day: str, row: dict[str, Any]) -> dict[str, Any]:
        measure = row.get("simpleMeasure") or {}
        return {
            **ACTIVITY_READ_DEFAULTS,
            **{key: row[key] for key in ("uuid", "planDayActivityType", "activityId", "energy", "deletedAt")},
            "date": day,
            "simpleMeasures": [{"measureId": measure.get("measureId"), "value": measure.get("value")}] if measure else [],
        }

    def _catalog_fields(self, row: dict[str, Any]) -> dict[str, Any]:
        # The server fills name (and nutrition) of product/recipe rows from the catalog.
        source = self.products.get(str(row.get("productId"))) or self.recipes.get(str(row.get("recipeId"))) or {}
        return {"name": source["name"]} if "name" in source else {}

    def day_view(self, day: str) -> dict[str, Any]:
        """The day as ``GET /diet-and-activity-plan/{id}/day/{day}`` returns it."""
        view = copy.deepcopy({**self.day_extras, **self.day_notes.get(day, {})})
        view["dietPlan"] = {meal_key: {**copy.deepcopy(fields), "items": []} for meal_key, fields in self.meal_fields.items()}
        for meal_key, row in self._rows(day).values():
            if not row.get("deletedAt"):
                view["dietPlan"].setdefault(meal_key, {"items": []})["items"].append(copy.deepcopy(row))
        view["activityPlan"] = [copy.deepcopy(row) for row in self._activities(day).values() if not row.get("deletedAt")]
        return view

    def all_rows(self, day: str) -> dict[str, dict[str, Any]]:
        """Every stored row by id, deleted ones included."""
        return {row_id: copy.deepcopy(row) for row_id, (_, row) in self._rows(day).items()}
