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
- the day GET always returns every meal of the day, empty or not

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

from fitatu_api import FitatuApiError, PlannerModule

RECORDING = json.loads((Path(__file__).parent / "fixtures" / "api_recording.json").read_text(encoding="utf-8"))
CALLS: dict[str, Any] = RECORDING["calls"]
RECORDED_USER_ID: str = RECORDING["userId"]

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


def _is_uuid_v1(value: Any) -> bool:
    try:
        return uuid.UUID(str(value)).version == 1
    except ValueError:
        return False


class FakeFitatuServer:
    """Stands in for ``FitatuApiClient`` in planner tests.

    ``day`` seeds the stored day (its meals define which meals exist; its rows are stored
    as they are). ``products``/``recipes``/``search_results`` back ``get_product_details``,
    ``get_recipe`` and ``search_food`` for planner flows that look things up.
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
        self.meals = list((skeleton.get("dietPlan") or {}).keys())
        self.day_extras = {key: value for key, value in skeleton.items() if key != "dietPlan"}
        self.days: dict[str, dict[str, tuple[str, dict[str, Any]]]] = {}
        self._seed = [
            (meal_key, row)
            for meal_key, bucket in (skeleton.get("dietPlan") or {}).items()
            for row in (bucket or {}).get("items", [])
        ]
        self.products = {str(k): v for k, v in (products or {}).items()}
        self.recipes = {str(k): v for k, v in (recipes or {}).items()}
        self.search_results = search_results or {}
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
        raise AssertionError(f"unexpected request: {method} {path}")

    def request_first_success(self, method: str, paths: list[str], **kwargs: Any) -> Any:
        # Only the deprecated helpers use this; their routes do not exist.
        self.requests.append({"method": method, "path": paths[0], **copy.deepcopy(kwargs)})
        raise FitatuApiError("not found", status_code=404)

    def search_food(self, phrase: str, **kwargs: Any) -> list[dict[str, Any]]:
        return copy.deepcopy(self.search_results.get(phrase.lower(), []))

    def get_product_details(self, product_id: int | str) -> dict[str, Any]:
        if str(product_id) not in self.products:
            raise FitatuApiError(f"product {product_id} not found", status_code=404)
        return copy.deepcopy(self.products[str(product_id)])

    def get_recipe(self, recipe_id: int | str) -> dict[str, Any]:
        if str(recipe_id) not in self.recipes:
            raise FitatuApiError(f"recipe {recipe_id} not found", status_code=404)
        return copy.deepcopy(self.recipes[str(recipe_id)])

    # -- server behaviour ---------------------------------------------------------
    def _rows(self, day: str) -> dict[str, tuple[str, dict[str, Any]]]:
        if day not in self.days:
            self.days[day] = {}
            for index, (meal_key, row) in enumerate(self._seed):
                self.days[day][str(row.get("planDayDietItemId") or f"seed-{index}")] = (meal_key, copy.deepcopy(row))
        return self.days[day]

    def _sync(self, payload: dict[str, Any]) -> list[dict[str, Any]]:
        results = []
        for day, day_payload in payload.items():
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
            results.append({"date": day, "revision": str(uuid.uuid1()), "errorMessage": None})
        return results

    def _catalog_fields(self, row: dict[str, Any]) -> dict[str, Any]:
        # The server fills name (and nutrition) of product/recipe rows from the catalog.
        source = self.products.get(str(row.get("productId"))) or self.recipes.get(str(row.get("recipeId"))) or {}
        return {"name": source["name"]} if "name" in source else {}

    def day_view(self, day: str) -> dict[str, Any]:
        """The day as ``GET /diet-and-activity-plan/{id}/day/{day}`` returns it."""
        view = copy.deepcopy(self.day_extras)
        view["dietPlan"] = {meal_key: {"items": []} for meal_key in self.meals}
        for meal_key, row in self._rows(day).values():
            if not row.get("deletedAt"):
                view["dietPlan"].setdefault(meal_key, {"items": []})["items"].append(copy.deepcopy(row))
        return view

    def all_rows(self, day: str) -> dict[str, dict[str, Any]]:
        """Every stored row by id, deleted ones included."""
        return {row_id: copy.deepcopy(row) for row_id, (_, row) in self._rows(day).items()}
