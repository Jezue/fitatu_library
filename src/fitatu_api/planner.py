"""Planner-related Fitatu API helpers."""

from __future__ import annotations

import logging
import re
import uuid
import warnings
from datetime import date, datetime
from typing import Any, cast

from .exceptions import FitatuApiError, FitatuPlannerSyncError

logger = logging.getLogger(__name__)

if False:  # pragma: no cover
    from .client import FitatuApiClient


def _warn_unsupported_route(name: str, replacement: str) -> None:
    """Emit a DeprecationWarning for helpers built on routes the API does not serve."""
    warnings.warn(
        f"{name}() calls a route that does not exist in the Fitatu API (it was a mobile app "
        f"screen path or a guessed variant) and always fails with 404. Use {replacement} instead. "
        "It will be removed in a future release.",
        DeprecationWarning,
        stacklevel=3,
    )


class PlannerModule:
    """High-level planner API.

    This module contains the day snapshot sync flow used for planner reads and writes.
    It is the most domain-heavy part of the public API and is intentionally exposed as a
    first-class module on :class:`fitatu_api.FitatuApiClient`.
    """

    def __init__(self, client: FitatuApiClient) -> None:
        self._client = client

    @staticmethod
    def _as_dict(value: Any) -> dict[str, Any] | None:
        """Return *value* as a typed dict, or None when it is not a dict."""
        return cast(dict[str, Any], value) if isinstance(value, dict) else None

    @staticmethod
    def _as_dict_list(value: Any) -> list[dict[str, Any]]:
        """Filter *value* down to a list containing only the dict elements."""
        if not isinstance(value, list):
            return []
        out: list[dict[str, Any]] = []
        for item in value:
            if isinstance(item, dict):
                out.append(cast(dict[str, Any], item))
        return out

    @staticmethod
    def _values_match(expected: Any, actual: Any) -> bool:
        """Return True when expected and actual represent the same value, with float tolerance."""
        if expected is None:
            return actual is None
        if isinstance(expected, (int, float)) and isinstance(actual, (int, float)):
            return abs(float(expected) - float(actual)) <= 1e-9
        if isinstance(expected, bool) or isinstance(actual, bool):
            return bool(expected) is bool(actual)
        return str(expected) == str(actual)

    @staticmethod
    def _now_timestamp() -> str:
        return datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    @staticmethod
    def normalize_meal_key(meal_type: str) -> str:
        """Normalize external meal names to the planner payload shape."""
        meal_key = meal_type.strip().lower()
        aliases = {
            "second-breakfast": "second_breakfast",
            "second breakfast": "second_breakfast",
        }
        return aliases.get(meal_key, meal_key)

    # ------------------------------------------------------------------
    # Write model (checked against the live API, see tests/fixtures):
    # POST /diet-plan/{id}/days upserts rows by planDayDietItemId and keeps every
    # row that is not in the payload, so a write only needs the rows it changes.
    # A row is deleted by sending it with deletedAt; the day GET then hides it.
    # A row is replaced as a whole, so a changed row is re-sent in full.
    # ------------------------------------------------------------------

    @classmethod
    def _iter_rows(cls, day_data: dict[str, Any]) -> list[tuple[str, dict[str, Any]]]:
        """Return ``(meal_key, row)`` for every row of a day GET response."""
        diet_plan = cls._as_dict(day_data.get("dietPlan")) or {}
        return [
            (meal_key, row)
            for meal_key, bucket in diet_plan.items()
            for row in cls._as_dict_list((cls._as_dict(bucket) or {}).get("items"))
        ]

    @classmethod
    def _locate_row(
        cls,
        day_data: dict[str, Any],
        item_id: str | int,
        *,
        prefer_meal: str | None = None,
    ) -> tuple[str, dict[str, Any]] | None:
        """Find a row by ``planDayDietItemId`` (or ``productId``), preferring ``prefer_meal``.

        Raises when ``prefer_meal`` is not a meal of that day, so typos fail loudly.
        """
        diet_plan = cls._as_dict(day_data.get("dietPlan"))
        if diet_plan is None:
            raise FitatuApiError("dietPlan not available in planner day response")
        if prefer_meal is not None and cls._as_dict(diet_plan.get(prefer_meal)) is None:
            raise FitatuApiError(f"meal not found in day payload: {prefer_meal}")
        rows = cls._iter_rows(day_data)
        rows.sort(key=lambda entry: entry[0] != prefer_meal)
        wanted = str(item_id)
        for key in ("planDayDietItemId", "productId"):
            for meal_key, row in rows:
                if str(row.get(key)) == wanted:
                    return meal_key, row
        return None

    def _require_meal(self, user_id: str, day: date, meal_type: str) -> str:
        """Return the normalised meal key, failing when the day has no such meal."""
        meal_key = self.normalize_meal_key(meal_type)
        diet_plan = self._as_dict(self.get_day(user_id, day).get("dietPlan"))
        if diet_plan is None:
            raise FitatuApiError("dietPlan not available in planner day response")
        if self._as_dict(diet_plan.get(meal_key)) is None:
            raise FitatuApiError(f"meal not found in day payload: {meal_type}")
        return meal_key

    def _upsert_rows(self, user_id: str, day: date, rows_by_meal: dict[str, list[dict[str, Any]]]) -> Any:
        """Send only these rows; synchronous so the next GET already sees them."""
        diet_plan = {meal_key: {"items": rows} for meal_key, rows in rows_by_meal.items() if rows}
        return self.sync_single_day(user_id, day, {"dietPlan": diet_plan}, synchronous=True)

    @classmethod
    def _mark_deleted(cls, row: dict[str, Any]) -> dict[str, Any]:
        """Return the row as the app deletes it: unchanged, plus ``deletedAt``."""
        return {**row, "deletedAt": cls._now_timestamp()}

    def _delete_rows(
        self,
        user_id: str,
        day: date,
        targets: list[tuple[str, dict[str, Any]]],
    ) -> tuple[Any, set[str]]:
        """Delete rows in one sync and return ``(sync_response, ids still visible)``."""
        rows_by_meal: dict[str, list[dict[str, Any]]] = {}
        for meal_key, row in targets:
            rows_by_meal.setdefault(meal_key, []).append(self._mark_deleted(row))
        sync_response = self._upsert_rows(user_id, day, rows_by_meal)
        visible = {str(row.get("planDayDietItemId")) for _, row in self._iter_rows(self.get_day(user_id, day))}
        wanted = {str(row.get("planDayDietItemId")) for _, row in targets}
        return sync_response, wanted & visible

    def _remove_row(self, user_id: str, day: date, meal_key: str, item_id: str | int) -> dict[str, Any]:
        """Delete one row (looked up in any meal) and confirm it is gone."""
        meal_key = self.normalize_meal_key(meal_key)
        found = self._locate_row(self.get_day(user_id, day), item_id, prefer_meal=meal_key)
        if found is None:
            return {
                "ok": False,
                "meal": meal_key,
                "removedId": None,
                "removedIdAbsentAfterSync": False,
                "cleanupMode": "none",
                "reason": "item_not_found",
                "removed": None,
                "syncResponse": None,
            }
        actual_meal, row = found
        sync_response, still_visible = self._delete_rows(user_id, day, [(actual_meal, row)])
        removed = not still_visible
        return {
            "ok": removed,
            "meal": actual_meal,
            "removedId": str(row.get("planDayDietItemId")),
            "removedIdAbsentAfterSync": removed,
            "cleanupMode": "removed" if removed else "none",
            "removed": row,
            "syncResponse": sync_response,
        }

    def get_day(self, user_id: str, day: date) -> dict[str, Any]:
        """Fetch a single planner day snapshot."""
        data = self._client.request(
            "GET",
            f"/diet-and-activity-plan/{user_id}/day/{day.isoformat()}",
        )
        return cast(dict[str, Any], data) if isinstance(data, dict) else {"raw": data}

    def get_meal(self, user_id: str, day: date, meal_type: str) -> dict[str, Any]:
        """Return a single meal bucket from a planner day."""
        planner_day = self.get_day(user_id, day)
        diet_plan = self._as_dict(planner_day.get("dietPlan"))
        if diet_plan is None:
            raise FitatuApiError("dietPlan not available in planner day response")

        meal = self._as_dict(diet_plan.get(self.normalize_meal_key(meal_type)))
        if meal is None:
            raise FitatuApiError(f"meal not found: {meal_type}")
        return meal

    def list_meal_items(
        self,
        user_id: str,
        day: date,
        meal_type: str,
    ) -> list[dict[str, Any]]:
        """List items inside a meal bucket."""
        return self._as_dict_list(self.get_meal(user_id, day, meal_type).get("items"))

    def find_meal_item(
        self,
        user_id: str,
        day: date,
        meal_type: str,
        query: str,
    ) -> dict[str, Any] | None:
        """Find the first meal item by case-insensitive name match."""
        needle = query.strip().lower()
        if not needle:
            return None
        for item in self.list_meal_items(user_id, day, meal_type):
            if needle in str(item.get("name") or "").lower():
                return item
        return None

    @staticmethod
    def _extract_measure_id(product: dict[str, Any]) -> int | str:
        """Extract the best available measure id from a product or search-result dict."""
        measure = product.get("measure")
        if isinstance(measure, dict):
            measure_dict = cast(dict[str, Any], measure)
            measure_id = measure_dict.get("defaultMeasureId") or measure_dict.get("measureId")
            if measure_id is not None:
                return measure_id
        measure_id = product.get("defaultMeasureId") or product.get("measureId")
        return measure_id if measure_id is not None else 1

    @staticmethod
    def _normalize_measure_unit(unit: str | None) -> str | None:
        """Normalise a free-text unit alias (e.g. "grams", "ml") to a canonical form."""
        if unit is None:
            return None
        text = str(unit).strip().lower()
        if not text:
            return None
        aliases = {
            "gram": "g",
            "grams": "g",
            "gr": "g",
            "g": "g",
            "mililitr": "ml",
            "mililitry": "ml",
            "milliliter": "ml",
            "milliliters": "ml",
            "ml": "ml",
            "opak": "opakowanie",
            "opakowanie": "opakowanie",
            "package": "opakowanie",
            "pack": "opakowanie",
            "szt": "sztuka",
            "sztuka": "sztuka",
            "piece": "sztuka",
            "porcja": "porcja",
            "portion": "porcja",
            "serving": "porcja",
            "lyzeczka": "łyżeczka",
            "łyżeczka": "łyżeczka",
            "łyżeczki": "łyżeczka",
            "teaspoon": "łyżeczka",
            "tsp": "łyżeczka",
            "lyzka": "łyżka",
            "łyżka": "łyżka",
            "łyżki": "łyżka",
            "tablespoon": "łyżka",
            "tbsp": "łyżka",
            "szklanka": "szklanka",
            "szklanki": "szklanka",
            "glass": "szklanka",
            "cup": "szklanka",
            "plaster": "plaster",
            "plastry": "plaster",
            "slice": "plaster",
            "kostka": "kostka",
            "kostki": "kostka",
            "cube": "kostka",
        }
        return aliases.get(text, text)

    @staticmethod
    def _coerce_positive_float(value: float | int, *, field_name: str) -> float:
        """Parse *value* to a positive float, raising FitatuApiError on failure."""
        try:
            parsed = float(value)
        except (TypeError, ValueError) as exc:
            raise FitatuApiError(f"{field_name} must be numeric") from exc
        if parsed <= 0:
            raise FitatuApiError(f"{field_name} must be > 0")
        return parsed

    @staticmethod
    def _measure_name_matches_unit(measure_name: str, canonical_unit: str) -> bool:
        """Return True when a raw measure name corresponds to a canonical unit string."""
        name = measure_name.strip().lower()
        if not name:
            return False
        if canonical_unit == "g":
            return name in {"g", "gram", "gramy", "grams"}
        if canonical_unit == "ml":
            return name in {"ml", "mililitr", "mililitry", "milliliter", "milliliters"}
        if canonical_unit == "opakowanie":
            return "opak" in name or "pack" in name or "package" in name
        if canonical_unit == "sztuka":
            return "szt" in name or "piece" in name
        if canonical_unit == "porcja":
            return "porc" in name or "portion" in name or "serving" in name
        if canonical_unit == "łyżeczka":
            return name in {"łyżeczka", "lyzeczka", "łyżeczki", "teaspoon", "tsp"}
        if canonical_unit == "łyżka":
            return name in {"łyżka", "lyzka", "łyżki", "tablespoon", "tbsp"}
        if canonical_unit == "szklanka":
            return name in {"szklanka", "szklanki", "glass", "cup"}
        if canonical_unit == "plaster":
            return name in {"plaster", "plastry", "slice"}
        if canonical_unit == "kostka":
            return name in {"kostka", "kostki", "cube"}
        return name == canonical_unit

    @staticmethod
    def _optional_positive_float(value: Any) -> float:
        try:
            parsed = float(value)
        except (TypeError, ValueError):
            return 0.0
        return parsed if parsed > 0 else 0.0

    def _extract_measure_candidates(
        self,
        product_details: dict[str, Any],
        *,
        search_product: dict[str, Any] | None = None,
    ) -> list[dict[str, Any]]:
        """Return all measure option dicts from a product details payload."""
        measures_raw = product_details.get("measures")
        simple_raw = product_details.get("simpleMeasures")

        candidates: list[dict[str, Any]] = []

        def add_candidate(
            *,
            measure_id: Any,
            name: Any,
            weight_per_unit: Any = 0,
            capacity_per_unit: Any = 0,
            source: str,
        ) -> None:
            if measure_id is None:
                return
            measure_name = str(name or "").strip()
            weight = self._optional_positive_float(weight_per_unit)
            capacity = self._optional_positive_float(capacity_per_unit)
            candidate = {
                "measureId": measure_id,
                "measureName": measure_name,
                "weightPerUnit": weight,
                "capacityPerUnit": capacity,
                "source": source,
            }

            for existing in candidates:
                if (
                    str(existing.get("measureId")) == str(measure_id)
                    and str(existing.get("measureName") or "").strip().lower()
                    == measure_name.lower()
                    and float(existing.get("weightPerUnit") or 0) == weight
                    and float(existing.get("capacityPerUnit") or 0) == capacity
                ):
                    return
            candidates.append(candidate)

        if isinstance(measures_raw, list):
            for measure_raw in measures_raw:
                measure = self._as_dict(measure_raw)
                if measure is None:
                    continue
                add_candidate(
                    measure_id=measure.get("id"),
                    name=measure.get("name"),
                    weight_per_unit=measure.get("weightPerUnit"),
                    capacity_per_unit=measure.get("capacityPerUnit"),
                    source="measures",
                )

        if isinstance(simple_raw, list):
            for row_raw in simple_raw:
                row = self._as_dict(row_raw)
                if row is None:
                    continue
                portion = self._optional_positive_float(row.get("portion")) or 1.0
                weight = self._optional_positive_float(row.get("weight")) / portion
                capacity = self._optional_positive_float(row.get("capacity")) / portion
                add_candidate(
                    measure_id=row.get("id"),
                    name=row.get("name"),
                    weight_per_unit=weight,
                    capacity_per_unit=capacity,
                    source="simpleMeasures",
                )

        if search_product is not None:
            search_measure = self._as_dict(search_product.get("measure")) or {}
            quantity = self._optional_positive_float(search_measure.get("measureQuantity")) or 1.0
            add_candidate(
                measure_id=search_measure.get("defaultMeasureId") or search_measure.get("measureId"),
                name=search_measure.get("measureName"),
                weight_per_unit=self._optional_positive_float(search_measure.get("measureWeight"))
                / quantity,
                capacity_per_unit=self._optional_positive_float(search_measure.get("measureCapacity"))
                / quantity,
                source="searchMeasure",
            )

        return candidates

    def resolve_product_measure(
        self,
        *,
        product_id: int | str,
        requested_amount: float | int,
        requested_unit: str,
        strict_measure: bool = True,
        search_product: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Resolve measure id and quantity for a target unit (g/ml/opakowanie/etc.).

        Returns a dict with keys: ``measureId``, ``measureName``,
        ``measureQuantity``, ``requestedAmount``, ``requestedUnit``,
        ``strategy``, ``strictMeasure``, ``warnings``, ``productId``.

        Fallback behaviour (``strict_measure=False`` only):

        - ``fallback_convertible_measure``: requested unit not available; a
          measure with a known weight/capacity per unit was selected and
          ``measureQuantity`` was recalculated via conversion.
        - ``fallback_search_measure``: no convertible measure found; the default
          measure from the original search result was used and
          ``measureQuantity`` equals ``requested_amount`` unchanged.
        - ``fallback_first_measure``: no search result provided; the first
          available product measure was used, quantity unchanged.

        When a fallback fires, ``warnings`` is non-empty.  Always check it::

            result = planner.resolve_product_measure(...)
            if result["warnings"]:
                # measure was substituted — verify quantities are sensible

        With ``strict_measure=True`` (default) any unresolvable unit raises
        ``FitatuApiError`` instead of falling back.
        """
        amount = self._coerce_positive_float(requested_amount, field_name="requested_amount")
        canonical_unit = self._normalize_measure_unit(requested_unit)
        if canonical_unit is None:
            raise FitatuApiError("requested_unit is required")

        product_details = self._client.get_product_details(product_id)
        candidates = self._extract_measure_candidates(
            product_details,
            search_product=search_product,
        )

        direct_match: dict[str, Any] | None = None
        for candidate in candidates:
            if self._measure_name_matches_unit(str(candidate.get("measureName") or ""), canonical_unit):
                direct_match = candidate
                break

        strategy = "direct_unit_match"
        warnings: list[str] = []
        selected_measure_id: Any | None = None
        selected_measure_name = ""
        selected_measure_quantity = amount

        if direct_match is not None:
            selected_measure_id = direct_match.get("measureId")
            selected_measure_name = str(direct_match.get("measureName") or "")
            strategy = (
                "direct_unit_match"
                if direct_match.get("source") == "measures"
                else f"direct_unit_match_{direct_match.get('source')}"
            )
        else:
            if canonical_unit == "g":
                weight_candidates = [c for c in candidates if float(c.get("weightPerUnit") or 0) > 0]
                if weight_candidates:
                    best = min(weight_candidates, key=lambda c: abs(float(c.get("weightPerUnit") or 0) - 1.0))
                    weight_per_unit = float(best.get("weightPerUnit") or 0)
                    selected_measure_id = best.get("measureId")
                    selected_measure_name = str(best.get("measureName") or "")
                    selected_measure_quantity = amount / weight_per_unit
                    strategy = "converted_from_weight"
            elif canonical_unit == "ml":
                capacity_candidates = [c for c in candidates if float(c.get("capacityPerUnit") or 0) > 0]
                if capacity_candidates:
                    best = min(capacity_candidates, key=lambda c: abs(float(c.get("capacityPerUnit") or 0) - 1.0))
                    capacity_per_unit = float(best.get("capacityPerUnit") or 0)
                    selected_measure_id = best.get("measureId")
                    selected_measure_name = str(best.get("measureName") or "")
                    selected_measure_quantity = amount / capacity_per_unit
                    strategy = "converted_from_capacity"

        if selected_measure_id is None:
            if strict_measure:
                raise FitatuApiError(
                    f"Cannot resolve unit '{requested_unit}' for product {product_id}"
                )
            convertible_candidates = (
                [
                    c
                    for c in candidates
                    if float(c.get("weightPerUnit") or 0) > 0
                    or float(c.get("capacityPerUnit") or 0) > 0
                ]
                if canonical_unit not in {"g", "ml"}
                else []
            )
            if convertible_candidates:
                selected = min(
                    convertible_candidates,
                    key=lambda c: (
                        0 if c.get("source") == "simpleMeasures" else 1,
                        abs(float(c.get("weightPerUnit") or c.get("capacityPerUnit") or 0) - 1.0),
                    ),
                )
                selected_measure_id = selected.get("measureId")
                selected_measure_name = str(selected.get("measureName") or "")
                strategy = "fallback_convertible_measure"
                warnings.append(
                    "Requested unit not available; falling back to a convertible measure"
                )
            elif search_product is not None:
                selected_measure_id = self._extract_measure_id(search_product)
                search_measure = self._as_dict(search_product.get("measure")) or {}
                selected_measure_name = str(search_measure.get("measureName") or "")
                strategy = "fallback_search_measure"
                warnings.append(
                    "Requested unit not available; falling back to default search measure"
                )
            elif candidates:
                selected_measure_id = candidates[0].get("measureId")
                selected_measure_name = str(candidates[0].get("measureName") or "")
                strategy = "fallback_first_measure"
                warnings.append(
                    "Requested unit not available; falling back to first product measure"
                )
            else:
                raise FitatuApiError(
                    f"No measures available for product {product_id}"
                )

        return {
            "measureId": selected_measure_id,
            "measureName": selected_measure_name,
            "measureQuantity": selected_measure_quantity,
            "requestedAmount": amount,
            "requestedUnit": canonical_unit,
            "strategy": strategy,
            "strictMeasure": strict_measure,
            "warnings": warnings,
            "productId": product_id,
        }

    def add_product_to_day_meal_with_unit(
        self,
        user_id: str,
        day: date,
        *,
        meal_type: str,
        product_id: int | str,
        amount: float | int,
        unit: str,
        eaten: bool = False,
        source: str = "API",
        strict_measure: bool = True,
    ) -> dict[str, Any]:
        """Add a product to a planner meal using user-facing unit semantics."""
        resolution = self.resolve_product_measure(
            product_id=product_id,
            requested_amount=amount,
            requested_unit=unit,
            strict_measure=strict_measure,
        )
        result = self.add_product_to_day_meal(
            user_id,
            day,
            meal_type=meal_type,
            product_id=product_id,
            measure_id=cast(int | str, resolution["measureId"]),
            measure_quantity=cast(float | int, resolution["measureQuantity"]),
            eaten=eaten,
            source=source,
        )
        result["measureResolution"] = resolution
        return result

    def add_product_to_day_meal(
        self,
        user_id: str,
        day: date,
        *,
        meal_type: str,
        product_id: int | str,
        measure_id: int | str,
        measure_quantity: float | int = 1,
        eaten: bool = False,
        source: str = "API",
    ) -> dict[str, Any]:
        """Add a product to a planner meal. Only the new row is sent."""
        meal_key = self._require_meal(user_id, day, meal_type)
        logger.debug("add_product user=%s day=%s meal=%s product=%s qty=%s", user_id, day, meal_key, product_id, measure_quantity)
        new_item: dict[str, Any] = {
            "planDayDietItemId": str(uuid.uuid1()),
            "foodType": "PRODUCT",
            "measureId": measure_id,
            "measureQuantity": measure_quantity,
            "ingredientsServing": None,
            "productId": product_id,
            "source": source,
            "eaten": eaten,
        }
        sync_response = self._upsert_rows(user_id, day, {meal_key: [new_item]})
        return {
            "ok": True,
            "meal": meal_key,
            "productId": product_id,
            "syncResponse": sync_response,
            "addedItem": new_item,
        }

    def add_recipe_to_day_meal(
        self,
        user_id: str,
        day: date,
        *,
        meal_type: str,
        recipe_id: int | str,
        food_type: str = "RECIPE",
        measure_id: int | str = 39,
        measure_quantity: float | int = 1,
        ingredients_serving: float | int | None = 1,
        eaten: bool = False,
        source: str = "API",
        hydrate_from_recipe_details: bool | None = None,
    ) -> dict[str, Any]:
        """Add a recipe to a planner meal. Only the new row is sent.

        The server fills name and nutrition from ``recipeId`` on its own (checked live),
        so ``hydrate_from_recipe_details`` is ignored and kept only for compatibility.
        """
        if hydrate_from_recipe_details is not None:
            warnings.warn(
                "hydrate_from_recipe_details is ignored: the server fills recipe rows from recipeId.",
                DeprecationWarning,
                stacklevel=2,
            )
        normalized_food_type = str(food_type or "RECIPE").strip().upper()
        if normalized_food_type not in {"RECIPE", "RECIPE_AI"}:
            raise FitatuApiError("food_type must be RECIPE or RECIPE_AI")
        meal_key = self._require_meal(user_id, day, meal_type)
        logger.debug("add_recipe user=%s day=%s meal=%s recipe=%s", user_id, day, meal_key, recipe_id)
        new_item: dict[str, Any] = {
            "planDayDietItemId": str(uuid.uuid1()),
            "foodType": normalized_food_type,
            "recipeId": recipe_id,
            "measureId": measure_id,
            "measureQuantity": measure_quantity,
            "ingredientsServing": ingredients_serving,
            "source": source,
            "eaten": eaten,
        }
        sync_response = self._upsert_rows(user_id, day, {meal_key: [new_item]})
        return {
            "ok": True,
            "meal": meal_key,
            "recipeId": recipe_id,
            "foodType": normalized_food_type,
            "syncResponse": sync_response,
            "addedItem": new_item,
        }

    def update_day_item(
        self,
        user_id: str,
        day: date,
        *,
        meal_type: str,
        item_id: str | int,
        measure_quantity: float | int | None = None,
        measure_id: str | int | None = None,
        eaten: bool | None = None,
        name: str | None = None,
        source: str | None = None,
        patch: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Update one planner row and check the result after a reload.

        The row is looked up in ``meal_type`` first and then in the other meals. It is
        re-sent in full (the server replaces rows as a whole), so fields you do not touch,
        ``eaten`` included, keep their values.
        """
        meal_key = self.normalize_meal_key(meal_type)
        found = self._locate_row(self.get_day(user_id, day), item_id, prefer_meal=meal_key)
        if found is None:
            raise FitatuApiError(f"item not found in meal: {meal_type}")
        actual_meal_key, row = found
        before_item = dict(row)

        updates_applied: dict[str, Any] = {
            key: value
            for key, value in (
                ("measureQuantity", measure_quantity),
                ("measureId", measure_id),
                ("eaten", eaten),
                ("name", name),
                ("source", source),
            )
            if value is not None
        }
        updates_applied.update(patch or {})
        sync_response = self._upsert_rows(user_id, day, {actual_meal_key: [{**row, **updates_applied}]})

        reloaded = self._locate_row(self.get_day(user_id, day), str(before_item.get("planDayDietItemId") or item_id))
        matched_after = reloaded[1] if reloaded else None
        validation = {
            key: self._values_match(expected, matched_after.get(key) if matched_after else None)
            for key, expected in updates_applied.items()
        }
        ok = bool(matched_after) and all(validation.values())
        return {
            "ok": ok,
            "meal": actual_meal_key,
            "requestedMeal": meal_key,
            "itemId": str(before_item.get("planDayDietItemId") or item_id),
            "productId": before_item.get("productId"),
            "before": before_item,
            "after": matched_after,
            "updates": updates_applied,
            "validation": validation,
            "syncResponse": sync_response,
        }

    def add_custom_item_to_day_meal(
        self,
        user_id: str,
        day: date,
        *,
        meal_type: str,
        name: str,
        calories: float | int,
        protein_g: float | int,
        fat_g: float | int,
        carbs_g: float | int,
        source: str = "API",
    ) -> dict[str, Any]:
        """Add a custom row (name + kcal/macros) to a planner meal. Only the new row is sent."""
        new_item = self._custom_item_from_values(
            name=name, calories=calories, protein_g=protein_g, fat_g=fat_g, carbs_g=carbs_g, source=source
        )
        meal_key = self._require_meal(user_id, day, meal_type)
        logger.debug("add_custom_item user=%s day=%s meal=%s name=%r kcal=%s", user_id, day, meal_key, name, calories)
        sync_response = self._upsert_rows(user_id, day, {meal_key: [new_item]})
        return {
            "ok": True,
            "meal": meal_key,
            "name": new_item["name"],
            "syncResponse": sync_response,
            "addedItem": new_item,
        }

    @staticmethod
    def _custom_item_from_values(
        *,
        name: str,
        calories: float | int,
        protein_g: float | int,
        fat_g: float | int,
        carbs_g: float | int,
        source: str = "API",
    ) -> dict[str, Any]:
        """Build a CUSTOM_ITEM sync row. Shape confirmed in production (2026-04-22)."""
        item_name = name.strip()
        if not item_name:
            raise FitatuApiError("Custom item name is required")
        return {
            "planDayDietItemId": str(uuid.uuid1()),
            "foodType": "CUSTOM_ITEM",
            "name": item_name,
            "energy": max(float(calories), 0.0),
            "protein": max(float(protein_g), 0.0),
            "fat": max(float(fat_g), 0.0),
            "carbohydrate": max(float(carbs_g), 0.0),
            "measureId": 1,
            "measureQuantity": 100,
            "measureWeight": 100,
            "measureCapacity": 0,
            "source": source,
            "updatedAt": PlannerModule._now_timestamp(),
        }

    def rollback_added_item(
        self,
        user_id: str,
        day: date,
        *,
        meal_type: str,
        plan_day_diet_item_id: str | None = None,
        product_id: int | str | None = None,
        neutralize_on_failure: bool = True,
        neutralized_quantity: float = 0.01,
        mark_invisible: bool = True,
    ) -> dict[str, Any]:
        """Delete a row that was just added to ``meal_type``.

        Only that meal is searched, newest row first: by ``plan_day_diet_item_id``, then by
        ``product_id`` - so an older row of the same product, or one in another meal, is
        never touched. ``neutralize_on_failure``, ``neutralized_quantity`` and
        ``mark_invisible`` are ignored and kept for compatibility.
        """
        if not plan_day_diet_item_id and product_id is None:
            raise FitatuApiError("rollback requires plan_day_diet_item_id or product_id")
        meal_key = self.normalize_meal_key(meal_type)
        day_data = self.get_day(user_id, day)
        self._locate_row(day_data, "", prefer_meal=meal_key)  # raises if the meal does not exist
        newest_first = [row for meal, row in reversed(self._iter_rows(day_data)) if meal == meal_key]
        row = None
        for key, wanted in (("planDayDietItemId", plan_day_diet_item_id), ("productId", product_id)):
            if wanted is not None and row is None:
                row = next((r for r in newest_first if str(r.get(key)) == str(wanted)), None)
        if row is None:
            return {"ok": False, "meal": meal_key, "removed": None, "cleanupMode": "none", "message": "No matching item found to rollback"}
        sync_response, still_visible = self._delete_rows(user_id, day, [(meal_key, row)])
        removed = not still_visible
        return {
            "ok": removed,
            "meal": meal_key,
            "removedId": str(row.get("planDayDietItemId")),
            "removedIdAbsentAfterSync": removed,
            "cleanupMode": "removed" if removed else "none",
            "removed": row,
            "syncResponse": sync_response,
        }

    def add_search_result_to_day_meal(
        self,
        user_id: str,
        day: date,
        *,
        meal_type: str,
        phrase: str,
        index: int = 0,
        measure_quantity: float | int = 1,
        measure_amount: float | int | None = None,
        measure_unit: str | None = None,
        strict_measure: bool = True,
        eaten: bool = False,
    ) -> dict[str, Any]:
        """Search food and add the selected search result to the planner."""
        search_items = self._client.search_food(phrase, limit=max(index + 1, 1))
        if not search_items or index >= len(search_items):
            raise FitatuApiError(f"No search result at index={index} for phrase='{phrase}'")
        product = search_items[index]
        product_id = product.get("foodId") or product.get("id")
        if product_id is None:
            raise FitatuApiError("Selected search item has no foodId/id")

        amount_for_resolution = (
            measure_quantity
            if measure_amount is None
            else self._coerce_positive_float(measure_amount, field_name="measure_amount")
        )

        resolution: dict[str, Any] | None = None
        if measure_unit is not None:
            resolution = self.resolve_product_measure(
                product_id=product_id,
                requested_amount=amount_for_resolution,
                requested_unit=measure_unit,
                strict_measure=strict_measure,
                search_product=product,
            )
            result = self.add_product_to_day_meal(
                user_id,
                day,
                meal_type=meal_type,
                product_id=product_id,
                measure_id=cast(int | str, resolution["measureId"]),
                measure_quantity=cast(float | int, resolution["measureQuantity"]),
                eaten=eaten,
            )
        else:
            measure_id = self._extract_measure_id(product)
            result = self.add_product_to_day_meal(
                user_id,
                day,
                meal_type=meal_type,
                product_id=product_id,
                measure_id=measure_id,
                measure_quantity=amount_for_resolution,
                eaten=eaten,
            )
            resolution = {
                "measureId": measure_id,
                "measureName": (self._as_dict(product.get("measure")) or {}).get("measureName"),
                "measureQuantity": amount_for_resolution,
                "requestedAmount": amount_for_resolution,
                "requestedUnit": None,
                "strategy": "search_default",
                "strictMeasure": strict_measure,
                "warnings": [],
                "productId": product_id,
            }

        result["searchItem"] = {
            "name": product.get("name"),
            "foodId": product.get("foodId"),
            "id": product.get("id"),
            "measureId": resolution.get("measureId") if resolution else None,
        }
        result["measureResolution"] = resolution
        return result

    def get_product_for_meal(self, product_id: str, meal_type: str, day: date) -> Any:
        """Deprecated: ``/product/{id}/{meal}/{day}`` is an app screen route, not an API path."""
        _warn_unsupported_route("get_product_for_meal", "client.get_product_details()")
        return self._client.request_first_success(
            "GET",
            [
                f"/product/{product_id}/{meal_type}/{day.isoformat()}",
                f"/v2/product/{product_id}/{meal_type}/{day.isoformat()}",
            ],
        )

    def quick_add_form(self, payload: dict[str, Any]) -> Any:
        """Deprecated: ``/food/quick-add/form`` is an app screen route, not an API path."""
        _warn_unsupported_route("quick_add_form", "add_custom_item_to_day_meal()")
        return self._client.request_first_success(
            "POST",
            ["/food/quick-add/form", "/v2/food/quick-add/form"],
            json_data=payload,
        )

    def quick_add_form_with_fallback(self, payload: dict[str, Any]) -> Any:
        """Add a quick-add style payload through search + planner sync.

        There is no quick-add endpoint (the app's quick add is a ``CUSTOM_ITEM`` row
        in the day sync), so this goes straight to the search-based add.
        """
        fallback = self._quick_add_form_fallback(payload)
        if fallback is None:
            raise FitatuApiError("quick add payload needs name, mealType, mealDate and userId")
        return fallback

    def _quick_add_form_fallback(self, payload: dict[str, Any]) -> dict[str, Any] | None:
        """Fall back to a search-based add when the quick-add endpoint is unavailable."""
        name = payload.get("name")
        meal_raw = payload.get("mealType") or payload.get("meal")
        day_raw = payload.get("mealDate") or payload.get("date")
        user_id_raw = payload.get("userId") or self._client.auth.fitatu_user_id
        if not isinstance(name, str) or not name.strip():
            return None
        if not isinstance(meal_raw, str) or not meal_raw.strip():
            return None
        if not isinstance(day_raw, str) or not day_raw.strip():
            return None
        if not isinstance(user_id_raw, str) or not user_id_raw.strip():
            return None
        try:
            target_day = date.fromisoformat(day_raw.strip())
        except ValueError:
            return None
        meal_key = self._coerce_meal_type_for_fallback(meal_raw)
        try:
            quantity_raw = payload.get("measureQuantity")
            quantity = float(quantity_raw) if quantity_raw is not None else 1.0
        except (TypeError, ValueError):
            quantity = 1.0
        return {
            "mode": "fallback_search_add",
            "quickAddEndpointAvailable": False,
            "input": {
                "name": name,
                "mealType": meal_raw,
                "mealDate": day_raw,
                "measureQuantity": quantity,
            },
            "result": self.add_search_result_to_day_meal(
                str(user_id_raw),
                target_day,
                meal_type=meal_key,
                phrase=name,
                index=0,
                measure_quantity=quantity,
                eaten=bool(payload.get("eaten", False)),
            ),
        }

    def _coerce_meal_type_for_fallback(self, meal_raw: str) -> str:
        """Normalise a raw meal type string or numeric index to a canonical meal key."""
        text = meal_raw.strip().replace("-", "_").lower()
        num_match = re.fullmatch(r"(?:meal_)?(\d+)", text)
        if num_match:
            return self._meal_from_number(int(num_match.group(1)))
        return self.normalize_meal_key(text)

    @staticmethod
    def _meal_from_number(number: int) -> str:
        """Map a Fitatu meal-slot integer (1–6) to its canonical string key."""
        mapping = {
            1: "breakfast",
            2: "second_breakfast",
            3: "lunch",
            4: "dinner",
            5: "snack",
            6: "supper",
        }
        return mapping.get(number, "snack")

    def send_changes(self, payload: dict[str, Any], method: str = "POST") -> Any:
        """Deprecated: there is no planner changes endpoint; changes go through day sync."""
        _warn_unsupported_route("send_changes", "sync_days()")
        return self._client.request_first_success(
            method.upper(),
            ["/planner/changes", "/v2/planner/changes", "/v2/diet-plan/planner/changes"],
            json_data=payload,
        )

    def add_day_items(self, user_id: str, day: date, items: list[dict[str, Any]]) -> Any:
        """Deprecated: there is no day-items endpoint; rows are added through day sync."""
        _warn_unsupported_route("add_day_items", "sync_single_day()")
        return self._client.request_first_success(
            "POST",
            [
                f"/diet-plan/{user_id}/day-items/{day.isoformat()}",
                f"/v2/diet-plan/{user_id}/day-items/{day.isoformat()}",
                f"/v3/diet-plan/{user_id}/day-items/{day.isoformat()}",
            ],
            json_data={"items": items},
        )

    def sync_days(
        self,
        user_id: str,
        days_payload: dict[str, Any],
        *,
        synchronous: bool = False,
    ) -> Any:
        """Sync one or more planner days.

        ``days_payload`` keys must be ISO-format date strings (``date.isoformat()``),
        not ``date`` objects — the payload is serialised directly to JSON::

            planner.sync_days(user_id, {date.today().isoformat(): day_snapshot})

        Returns the server's per-day result list, e.g.
        ``[{"date": "2026-10-13", "revision": "...", "errorMessage": None}]``.

        Raises:
            FitatuPlannerSyncError: When any day comes back with an ``errorMessage``.
                The endpoint still answers 202 in that case and nothing is saved for
                the rejected day (e.g. a ``planDayDietItemId`` that is not a UUID v1).
        """
        response = self._client.request(
            "POST",
            f"/diet-plan/{user_id}/days",
            json_data=days_payload,
            params={"synchronous": "true"} if synchronous else None,
        )
        if isinstance(response, list):
            rejected = [row for row in response if isinstance(row, dict) and row.get("errorMessage")]
            if rejected:
                details = "; ".join(f"{row.get('date')}: {row.get('errorMessage')}" for row in rejected)
                raise FitatuPlannerSyncError(f"Planner sync rejected: {details}", results=response)
        return response

    def get_day_revisions(self, user_id: str, day: date) -> list[str]:
        """Return the day's revision ids, newest first.

        Every accepted sync adds one, so comparing this list with a previously seen
        one is a cheap way to notice changes made elsewhere (e.g. on the phone)
        without downloading the whole day.
        """
        data = self._client.request("GET", f"/diet-plan/{user_id}/days/{day.isoformat()}/revisions")
        return [str(rev) for rev in data] if isinstance(data, list) else []

    def sync_single_day(
        self,
        user_id: str,
        day: date,
        day_payload: dict[str, Any],
        *,
        synchronous: bool = False,
    ) -> Any:
        """Sync a single day snapshot."""
        logger.debug("sync_single_day user=%s day=%s", user_id, day)
        return self.sync_days(user_id, {day.isoformat(): day_payload}, synchronous=synchronous)

    def move_day_item(
        self,
        user_id: str,
        from_day: date,
        *,
        from_meal_type: str,
        item_id: str | int,
        to_day: date | None = None,
        to_meal_type: str | None = None,
        synchronous: bool = True,
    ) -> dict[str, Any]:
        """Move a row to another meal and/or day: delete it and add a copy, in one sync.

        The row is looked up in ``from_meal_type`` first and then in the other meals.
        """
        target_day = to_day or from_day
        from_meal_key = self.normalize_meal_key(from_meal_type)
        to_meal_key = self.normalize_meal_key(to_meal_type or from_meal_type)

        found = self._locate_row(self.get_day(user_id, from_day), item_id, prefer_meal=from_meal_key)
        if found is None:
            raise FitatuApiError(f"item not found in meal: {from_meal_type}")
        source_meal_key, source_item = found

        moved_item = {key: value for key, value in source_item.items() if key != "deletedAt"}
        moved_item["planDayDietItemId"] = str(uuid.uuid1())
        delete_marker = self._mark_deleted(source_item)

        days_payload: dict[str, Any] = {from_day.isoformat(): {"dietPlan": {source_meal_key: {"items": [delete_marker]}}}}
        target_plan = days_payload.setdefault(target_day.isoformat(), {"dietPlan": {}})["dietPlan"]
        target_plan.setdefault(to_meal_key, {"items": []})["items"].append(moved_item)

        sync_response = self.sync_days(user_id, days_payload, synchronous=synchronous)
        return {
            "ok": True,
            "operation": "move_day_item",
            "fromDate": from_day.isoformat(),
            "toDate": target_day.isoformat(),
            "fromMeal": source_meal_key,
            "toMeal": to_meal_key,
            "removedId": source_item.get("planDayDietItemId"),
            "addedItem": moved_item,
            "deleteMarker": delete_marker,
            "syncPayload": days_payload,
            "syncResponse": sync_response,
        }

    def replace_day_item_with_custom_item(
        self,
        user_id: str,
        day: date,
        *,
        meal_type: str,
        item_id: str | int,
        name: str,
        calories: float | int,
        protein_g: float | int,
        fat_g: float | int,
        carbs_g: float | int,
        source: str = "API",
        synchronous: bool = True,
    ) -> dict[str, Any]:
        """Replace a row with a new custom row (delete + add) in one sync."""
        meal_key = self.normalize_meal_key(meal_type)
        found = self._locate_row(self.get_day(user_id, day), item_id, prefer_meal=meal_key)
        if found is None:
            raise FitatuApiError(f"item not found in meal: {meal_type}")
        source_meal_key, source_item = found

        delete_marker = self._mark_deleted(source_item)
        replacement = self._custom_item_from_values(
            name=name, calories=calories, protein_g=protein_g, fat_g=fat_g, carbs_g=carbs_g, source=source
        )
        days_payload = {day.isoformat(): {"dietPlan": {source_meal_key: {"items": [delete_marker, replacement]}}}}
        sync_response = self.sync_days(user_id, days_payload, synchronous=synchronous)
        return {
            "ok": True,
            "operation": "replace_day_item_with_custom_item",
            "date": day.isoformat(),
            "meal": source_meal_key,
            "removedId": source_item.get("planDayDietItemId"),
            "replacementItem": replacement,
            "deleteMarker": delete_marker,
            "syncPayload": days_payload,
            "syncResponse": sync_response,
        }

    def remove_day_item_via_snapshot(
        self,
        user_id: str,
        day: date,
        meal_key: str,
        item_id: str | int,
    ) -> dict[str, Any]:
        """Delete a row with ``deletedAt`` (same as :meth:`remove_day_item`).

        Kept for compatibility: leaving a row out of the sync, which this used to do,
        never deletes it.
        """
        return self._remove_row(user_id, day, meal_key, item_id)

    def soft_remove_day_item_via_snapshot(
        self,
        user_id: str,
        day: date,
        meal_key: str,
        item_id: str | int,
        *,
        soft_delete_quantity: float = 0.01,
        mark_invisible: bool = False,
        use_deleted_at: bool = True,
    ) -> dict[str, Any]:
        """Delete a row with ``deletedAt`` (same as :meth:`remove_day_item`).

        ``soft_delete_quantity``, ``mark_invisible`` and ``use_deleted_at`` are ignored:
        quantity and visibility tricks never removed anything server-side.
        """
        return self._remove_row(user_id, day, meal_key, item_id)

    def classify_day_item_for_removal(
        self,
        user_id: str,
        day: date,
        meal_key: str,
        item_id: str | int,
    ) -> dict[str, Any]:
        """Report which kind of row ``item_id`` is (``normal_item``, ``custom_add_item``,
        ``custom_recipe_item``). Every kind is deleted the same way; the kind is only a
        filter for :meth:`remove_day_items_by_kind`.
        """
        meal_key = self.normalize_meal_key(meal_key)
        found = self._locate_row(self.get_day(user_id, day), item_id, prefer_meal=meal_key)
        if found is None:
            return {
                "found": False,
                "requestedItemId": str(item_id),
                "resolvedKind": "normal_item",
                "reason": "item_not_found_defaulting_to_normal",
                "meal": meal_key,
                "item": None,
            }
        actual_meal, row = found
        resolved_kind, reason = self._resolve_item_kind(row)
        return {
            "found": True,
            "requestedItemId": str(item_id),
            "resolvedKind": resolved_kind,
            "reason": reason,
            "meal": actual_meal,
            "item": row,
        }

    @staticmethod
    def _resolve_item_kind(item: dict[str, Any]) -> tuple[str, str]:
        """Classify a day-item dict as normal_item, custom_add_item, or custom_recipe_item."""
        food_type = str(item.get("foodType") or "").strip().upper()
        source = str(item.get("source") or "").strip().upper()
        product_id = item.get("productId")
        try:
            quantity = float(item.get("measureQuantity") or 0)
        except (TypeError, ValueError):
            quantity = 0.0

        if food_type == "PRODUCT" or product_id is not None:
            return "normal_item", "product_or_has_product_id"
        if food_type == "CUSTOM_ITEM":
            # Live data shows that API quick-add rows often materialize as CUSTOM_ITEM
            # with serving-like quantities, so we keep treating those as the
            # recipe-like bucket for deletion strategy purposes.
            if source == "API" and quantity <= 2.0:
                return "custom_recipe_item", "custom_item_api_serving_like"
            return "custom_add_item", "custom_item_manual_like"
        return "normal_item", "default"

    def list_day_items_for_removal(
        self,
        user_id: str,
        day: date,
        *,
        meal_key: str | None = None,
    ) -> list[dict[str, Any]]:
        """Return the day's rows (optionally one meal) with their removal kind."""
        planner_day = self.get_day(user_id, day)
        diet_plan = self._as_dict(planner_day.get("dietPlan"))
        if diet_plan is None:
            raise FitatuApiError("dietPlan not available in planner day response")
        selected_meal = self.normalize_meal_key(meal_key) if meal_key is not None else None
        if selected_meal is not None and self._as_dict(diet_plan.get(selected_meal)) is None:
            raise FitatuApiError(f"meal not found in day payload: {meal_key}")

        rows: list[dict[str, Any]] = []
        for current_meal_key, item in self._iter_rows(planner_day):
            if selected_meal is not None and current_meal_key != selected_meal:
                continue
            resolved_kind, reason = self._resolve_item_kind(item)
            rows.append(
                {
                    "meal": current_meal_key,
                    "itemId": str(item.get("planDayDietItemId") or ""),
                    "productId": item.get("productId"),
                    "foodType": item.get("foodType"),
                    "source": item.get("source"),
                    "resolvedKind": resolved_kind,
                    "reason": reason,
                    "item": item,
                }
            )
        return rows

    def remove_day_items_by_kind(
        self,
        user_id: str,
        day: date,
        *,
        item_kind: str,
        meal_key: str | None = None,
        max_items: int | None = None,
        delete_all_related_meals: bool = False,
        use_aggressive_soft_delete: bool = True,
        max_soft_delete_retries: int = 2,
    ) -> dict[str, Any]:
        """Delete every row of one kind (or ``auto`` for all), optionally in one meal.

        All matching rows go out in a single sync. The last three arguments are ignored
        and kept for compatibility.
        """
        requested_kind = (item_kind or "").strip().lower()
        valid_kinds = {"normal_item", "custom_add_item", "custom_recipe_item", "auto"}
        if requested_kind not in valid_kinds:
            raise FitatuApiError(
                "item_kind must be one of: auto, normal_item, custom_add_item, custom_recipe_item"
            )
        logger.debug("remove_by_kind user=%s day=%s kind=%s meal=%s", user_id, day, requested_kind, meal_key)
        targets = [
            row
            for row in self.list_day_items_for_removal(user_id, day, meal_key=meal_key)
            if row["itemId"] and requested_kind in {"auto", row["resolvedKind"]}
        ]
        if max_items is not None:
            targets = targets[: max(0, int(max_items))]

        sync_response: Any = None
        still_visible: set[str] = set()
        if targets:
            sync_response, still_visible = self._delete_rows(
                user_id, day, [(row["meal"], row["item"]) for row in targets]
            )
        attempts = [
            {
                "seq": index,
                "meal": row["meal"],
                "itemId": row["itemId"],
                "requestedKind": requested_kind,
                "resolvedKind": row["resolvedKind"],
                "ok": row["itemId"] not in still_visible,
                "cleanupMode": "none" if row["itemId"] in still_visible else "removed",
            }
            for index, row in enumerate(targets, start=1)
        ]
        removed_count = sum(1 for attempt in attempts if attempt["ok"])
        return {
            "ok": removed_count == len(targets),
            "requestedKind": requested_kind,
            "targetedCount": len(targets),
            "removedCount": removed_count,
            "failedCount": len(targets) - removed_count,
            "meal": self.normalize_meal_key(meal_key) if meal_key is not None else None,
            "attempts": attempts,
            "syncResponse": sync_response,
        }

    def remove_day_item_with_strategy(
        self,
        user_id: str,
        day: date,
        meal_key: str,
        item_id: str | int,
        *,
        item_kind: str = "auto",
        delete_all_related_meals: bool = False,
        use_aggressive_soft_delete: bool = True,
        max_soft_delete_retries: int = 2,
    ) -> dict[str, Any]:
        """Delete a row with ``deletedAt`` and confirm it is gone.

        There used to be a different fallback chain per item kind; once it was clear
        that only ``deletedAt`` removes rows, every kind is handled the same way.
        ``item_kind`` is still validated and reported. The other options are ignored.
        """
        requested_kind = (item_kind or "auto").strip().lower()
        if requested_kind not in {"auto", "normal_item", "custom_add_item", "custom_recipe_item"}:
            raise FitatuApiError(
                "item_kind must be one of: auto, normal_item, custom_add_item, custom_recipe_item"
            )
        result = self._remove_row(user_id, day, meal_key, item_id)
        removed_row = result.get("removed")
        resolved_kind = (
            self._resolve_item_kind(removed_row)[0] if requested_kind == "auto" and removed_row else requested_kind
        )
        result.update(
            {
                "requestedKind": requested_kind,
                "resolvedKind": resolved_kind,
                "attempts": [{"step": "deleted_at", "ok": result["ok"], "cleanupMode": result["cleanupMode"]}],
            }
        )
        return result

    def remove_day_item(
        self,
        user_id: str,
        day: date,
        meal_key: str,
        item_id: str | int,
        *,
        delete_all_related_meals: bool = False,
        use_aggressive_soft_delete: bool = True,
    ) -> Any:
        """Delete a planner row the way the app does (``deletedAt``) and confirm it is gone.

        ``item_id`` is the row's ``planDayDietItemId`` (a ``productId`` also works). The
        row is looked up in ``meal_key`` first, then in the other meals. Check ``ok``:
        ``False`` means the row was not found or is still there after the sync. The two
        keyword options are ignored and kept for compatibility.
        """
        logger.debug("remove_day_item user=%s day=%s meal=%s item=%s", user_id, day, meal_key, item_id)
        return self.remove_day_item_with_strategy(user_id, day, meal_key, item_id)

    def remove_activity_day_item(self, user_id: str, day: date, item_id: str | int) -> Any:
        """Deprecated: activities have no DELETE route.

        The app removes an activity by re-sending it in the day sync ``activities`` list
        with ``deletedAt`` set.
        """
        _warn_unsupported_route("remove_activity_day_item", "a day sync with deletedAt on the activity")
        return self._client.request_first_success(
            "DELETE",
            [
                f"/activity-plan/{user_id}/day/{day.isoformat()}/{item_id}",
                f"/v2/activity-plan/{user_id}/day/{day.isoformat()}/{item_id}",
                f"/v3/activity-plan/{user_id}/day/{day.isoformat()}/{item_id}",
            ],
        )
