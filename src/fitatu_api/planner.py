"""Planner-related Fitatu API helpers."""

from __future__ import annotations

import logging
import re
import time
import uuid
from datetime import date, datetime
from typing import Any, cast

from .exceptions import FitatuApiError

logger = logging.getLogger(__name__)

if False:  # pragma: no cover
    from .client import FitatuApiClient


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
    def _first_non_empty(*values: Any) -> Any:
        """Return the first value that is not None and not a blank string."""
        for value in values:
            if value is None:
                continue
            if isinstance(value, str) and not value.strip():
                continue
            return value
        return None

    @staticmethod
    def _now_timestamp() -> str:
        return datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    @staticmethod
    def _parse_optional_float(value: Any) -> float | None:
        """Parse *value* to float, returning None on failure or when value is None."""
        if value is None:
            return None
        try:
            parsed = float(value)
        except (TypeError, ValueError):
            return None
        return parsed

    def _hydrate_recipe_item_from_details(
        self,
        recipe_id: int | str,
        base_item: dict[str, Any],
    ) -> dict[str, Any]:
        """Fetch recipe nutritional fields and merge them into base_item for sync."""
        try:
            recipe_payload = self._client.get_recipe(recipe_id)
        except FitatuApiError as exc:
            return {
                "status": "unavailable",
                "fields": {},
                "message": str(exc),
                "statusCode": exc.status_code,
            }

        if not isinstance(recipe_payload, dict):
            return {
                "status": "unavailable",
                "fields": {},
                "message": "recipe details response is not an object",
                "statusCode": None,
            }

        containers: list[dict[str, Any]] = [recipe_payload]
        for key in ("recipe", "data", "result"):
            nested = self._as_dict(recipe_payload.get(key))
            if nested is not None:
                containers.append(nested)
                nested_recipe = self._as_dict(nested.get("recipe"))
                if nested_recipe is not None:
                    containers.append(nested_recipe)

        nutrition_containers: list[dict[str, Any]] = []
        for container in containers:
            for key in (
                "nutritionalValues",
                "nutritionalValue",
                "nutrition",
                "macros",
                "nutrients",
            ):
                nested = self._as_dict(container.get(key))
                if nested is not None:
                    nutrition_containers.append(nested)

        def pick(*keys: str) -> Any:
            """Return the first non-empty value found under any of *keys* across recipe containers."""
            for container in containers:
                value = self._first_non_empty(*(container.get(key) for key in keys))
                if value is not None:
                    return value
            return None

        def pick_numeric(*keys: str) -> float | None:
            """Like pick(), but coerces the result to float and searches nutrition containers too."""
            direct = self._parse_optional_float(pick(*keys))
            if direct is not None:
                return direct
            for nutrition in nutrition_containers:
                value = self._parse_optional_float(
                    self._first_non_empty(*(nutrition.get(key) for key in keys))
                )
                if value is not None:
                    return value
            return None

        recipe_name = pick("name", "title")
        photo_candidate = pick("photo", "photoUrl", "image", "imageUrl", "thumbnailUrl")
        photo_value: str | None = None
        if isinstance(photo_candidate, dict):
            photo_dict = cast(dict[str, Any], photo_candidate)
            raw_photo = self._first_non_empty(
                photo_dict.get("url"),
                photo_dict.get("src"),
                photo_dict.get("original"),
            )
            if isinstance(raw_photo, str) and raw_photo.strip():
                photo_value = raw_photo.strip()
        elif isinstance(photo_candidate, str) and photo_candidate.strip():
            photo_value = photo_candidate.strip()

        hydrated: dict[str, Any] = {}
        if isinstance(recipe_name, str) and recipe_name.strip():
            hydrated["name"] = recipe_name.strip()
        if photo_value is not None:
            hydrated["photo"] = photo_value

        energy = pick_numeric("energy", "kcal", "calories", "energyKcal")
        protein = pick_numeric("protein", "proteinG")
        fat = pick_numeric("fat", "fatG")
        carbohydrate = pick_numeric("carbohydrate", "carbohydrates", "carb", "carbs", "carbohydrateG")
        if energy is not None:
            hydrated["energy"] = energy
        if protein is not None:
            hydrated["protein"] = protein
        if fat is not None:
            hydrated["fat"] = fat
        if carbohydrate is not None:
            hydrated["carbohydrate"] = carbohydrate

        details_measure_id = pick("measureId", "defaultMeasureId")
        details_measure_quantity = self._parse_optional_float(
            pick("measureQuantity", "quantity", "servingQuantity")
        )
        details_ingredients_serving = self._parse_optional_float(
            pick("ingredientsServing", "serving", "servings")
        )

        if base_item.get("measureId") is None and details_measure_id is not None:
            hydrated["measureId"] = details_measure_id
        if base_item.get("measureQuantity") is None and details_measure_quantity is not None:
            hydrated["measureQuantity"] = details_measure_quantity
        if base_item.get("ingredientsServing") is None and details_ingredients_serving is not None:
            hydrated["ingredientsServing"] = details_ingredients_serving

        return {
            "status": "hydrated" if hydrated else "no_fields",
            "fields": hydrated,
            "recipeId": recipe_id,
        }

    @staticmethod
    def normalize_meal_key(meal_type: str) -> str:
        """Normalize external meal names to the planner payload shape."""
        meal_key = meal_type.strip().lower()
        aliases = {
            "second-breakfast": "second_breakfast",
            "second breakfast": "second_breakfast",
        }
        return aliases.get(meal_key, meal_key)

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
    def _build_day_sync_payload(day_data: dict[str, Any]) -> dict[str, Any]:
        """Extract the fields required by the day-sync POST endpoint from a full day snapshot."""
        return {
            "dietPlan": day_data.get("dietPlan") or {},
            "toiletItems": day_data.get("toiletItems") or [],
            "note": day_data.get("note"),
            "tagsIds": day_data.get("tagsIds") or [],
        }

    @staticmethod
    def _compact_diet_item_for_sync(item: dict[str, Any]) -> dict[str, Any]:
        """Strip a day-item dict to the minimal set of fields accepted by the sync endpoint."""
        compact: dict[str, Any] = {
            "planDayDietItemId": item.get("planDayDietItemId"),
            "foodType": item.get("foodType"),
            "measureId": item.get("measureId"),
            "measureQuantity": item.get("measureQuantity"),
            "ingredientsServing": item.get("ingredientsServing"),
            "source": item.get("source") or "API",
        }

        # Recipe items are keyed by recipeId (not productId); keep it in sync payloads.
        if item.get("recipeId") is not None:
            compact["recipeId"] = item.get("recipeId")
        recipe_ai = item.get("recipeAI")
        if isinstance(recipe_ai, dict):
            compact["recipeAI"] = recipe_ai

        has_product_id = item.get("productId") is not None
        if has_product_id:
            compact["productId"] = item.get("productId")
        else:
            for key in ("name", "energy", "protein", "fat", "carbohydrate"):
                if key in item and item.get(key) is not None:
                    compact[key] = item.get(key)

        existing_updated_at = item.get("updatedAt")
        if isinstance(existing_updated_at, str) and existing_updated_at.strip():
            compact["updatedAt"] = existing_updated_at
        else:
            compact["updatedAt"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        return compact

    def _compact_diet_plan_for_sync(self, diet_plan: dict[str, Any]) -> dict[str, Any]:
        """Compact every meal bucket in a full dietPlan dict for use in a sync payload."""
        compact_plan: dict[str, Any] = {}
        for meal_key, meal_raw in diet_plan.items():
            meal_bucket = self._as_dict(meal_raw)
            if meal_bucket is None:
                continue
            items = self._as_dict_list(meal_bucket.get("items"))
            if not items:
                continue
            compact_plan[meal_key] = {
                "items": [self._compact_diet_item_for_sync(item) for item in items]
            }
        return compact_plan

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
        """Add a product to a planner day using the day sync flow."""
        meal_key = self.normalize_meal_key(meal_type)
        logger.debug("add_product user=%s day=%s meal=%s product=%s qty=%s", user_id, day, meal_key, product_id, measure_quantity)
        planner_day = self.get_day(user_id, day)
        day_payload = self._build_day_sync_payload(planner_day)
        diet_plan = self._as_dict(day_payload.get("dietPlan"))
        if diet_plan is None:
            raise FitatuApiError("dietPlan not available in planner day response")

        meal_bucket = self._as_dict(diet_plan.get(meal_key))
        if meal_bucket is None:
            raise FitatuApiError(f"meal not found in day payload: {meal_type}")

        items = self._as_dict_list(meal_bucket.get("items"))
        meal_bucket["items"] = items
        new_item_id = str(uuid.uuid1())
        new_item: dict[str, Any] = {
            "planDayDietItemId": new_item_id,
            "foodType": "PRODUCT",
            "measureId": measure_id,
            "measureQuantity": measure_quantity,
            "ingredientsServing": None,
            "productId": product_id,
            "source": source,
            "eaten": eaten,
            "updatedAt": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        }
        items.append(new_item)
        day_payload["dietPlan"] = self._compact_diet_plan_for_sync(diet_plan)
        sync_response = self.sync_single_day(user_id, day, day_payload)
        logger.debug("add_product ok item_id=%s", new_item_id)
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
        hydrate_from_recipe_details: bool = True,
    ) -> dict[str, Any]:
        """Add a recipe entry to a planner day using the day snapshot sync flow.

        Fitatu requires both recipeId and planDayDietItemId for recipe-like items.
        """
        meal_key = self.normalize_meal_key(meal_type)
        logger.debug("add_recipe user=%s day=%s meal=%s recipe=%s", user_id, day, meal_key, recipe_id)
        normalized_food_type = str(food_type or "RECIPE").strip().upper()
        if normalized_food_type not in {"RECIPE", "RECIPE_AI"}:
            raise FitatuApiError("food_type must be RECIPE or RECIPE_AI")

        planner_day = self.get_day(user_id, day)
        day_payload = self._build_day_sync_payload(planner_day)
        diet_plan = self._as_dict(day_payload.get("dietPlan"))
        if diet_plan is None:
            raise FitatuApiError("dietPlan not available in planner day response")

        meal_bucket = self._as_dict(diet_plan.get(meal_key))
        if meal_bucket is None:
            raise FitatuApiError(f"meal not found in day payload: {meal_type}")

        items = self._as_dict_list(meal_bucket.get("items"))
        meal_bucket["items"] = items
        new_item_id = str(uuid.uuid1())
        new_item: dict[str, Any] = {
            "planDayDietItemId": new_item_id,
            "foodType": normalized_food_type,
            "recipeId": recipe_id,
            "measureId": measure_id,
            "measureQuantity": measure_quantity,
            "ingredientsServing": ingredients_serving,
            "source": source,
            "eaten": eaten,
            "updatedAt": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        }

        recipe_hydration: dict[str, Any] = {
            "status": "disabled",
            "fields": {},
            "recipeId": recipe_id,
        }
        if hydrate_from_recipe_details:
            recipe_hydration = self._hydrate_recipe_item_from_details(recipe_id, new_item)
            hydrated_fields = recipe_hydration.get("fields")
            if isinstance(hydrated_fields, dict):
                for key, value in hydrated_fields.items():
                    # Preserve explicitly provided serving/measure fields.
                    if key in {"measureId", "measureQuantity", "ingredientsServing"} and new_item.get(key) is not None:
                        continue
                    new_item[key] = value

        items.append(new_item)

        # Recipe adds are currently materialized only when syncing the full day snapshot.
        # Compact payloads are accepted but may not persist recipe entries server-side.
        sync_response = self.sync_single_day(user_id, day, day_payload)
        logger.debug("add_recipe ok item_id=%s", new_item_id)
        return {
            "ok": True,
            "meal": meal_key,
            "recipeId": recipe_id,
            "foodType": normalized_food_type,
            "syncResponse": sync_response,
            "addedItem": new_item,
            "recipeDetailsHydration": recipe_hydration,
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
        """Update a single planner item and validate the result after reload.

        Returns a dict with ``ok: bool``.  Retries ``get_day`` up to 3 times with a
        short delay when the item is not yet visible in the snapshot — the backend sync
        is eventually consistent and a freshly added item may take a moment to appear.
        Cross-meal fallback is also applied so items that landed in a different bucket
        are found automatically.
        """
        meal_key = self.normalize_meal_key(meal_type)
        _, day_payload, target_item = self._get_day_retrying_for_item(
            user_id, day, meal_key, item_id, any_meal=True,
        )

        if target_item is None:
            raise FitatuApiError(f"item not found in meal: {meal_type}")

        diet_plan = self._as_dict(day_payload.get("dietPlan"))
        if diet_plan is None:
            raise FitatuApiError("dietPlan not available in planner day response")

        # Determine which bucket the item actually lives in (cross-meal fallback).
        actual_meal_key = meal_key
        meal_bucket: dict[str, Any] = {}
        for bucket_key, bucket_raw in diet_plan.items():
            bucket = self._as_dict(bucket_raw)
            if bucket is None:
                continue
            for _item in self._as_dict_list(bucket.get("items")):
                if str(_item.get("planDayDietItemId")) == str(target_item.get("planDayDietItemId")):
                    actual_meal_key = bucket_key
                    meal_bucket = bucket
                    break
            if meal_bucket:
                break

        items = self._as_dict_list(meal_bucket.get("items"))
        meal_bucket["items"] = items

        before_item = dict(target_item)
        updates_applied: dict[str, Any] = {}
        if measure_quantity is not None:
            target_item["measureQuantity"] = measure_quantity
            updates_applied["measureQuantity"] = measure_quantity
        if measure_id is not None:
            target_item["measureId"] = measure_id
            updates_applied["measureId"] = measure_id
        if eaten is not None:
            target_item["eaten"] = eaten
            updates_applied["eaten"] = eaten
        if name is not None:
            target_item["name"] = name
            updates_applied["name"] = name
        if source is not None:
            target_item["source"] = source
            updates_applied["source"] = source
        if patch:
            for key, value in patch.items():
                target_item[key] = value
                updates_applied[key] = value

        sync_response = self.sync_single_day(user_id, day, day_payload)
        reloaded_day = self.get_day(user_id, day)
        reloaded_diet = self._as_dict(reloaded_day.get("dietPlan"))
        reloaded_meal = self._as_dict(reloaded_diet.get(actual_meal_key) if reloaded_diet else None)
        reloaded_items = self._as_dict_list(reloaded_meal.get("items") if reloaded_meal else None)

        matched_after: dict[str, Any] | None = None
        for item in reloaded_items:
            if str(item.get("planDayDietItemId")) == str(before_item.get("planDayDietItemId")):
                matched_after = item
                break
        if matched_after is None:
            for item in reloaded_items:
                if str(item.get("productId")) == str(before_item.get("productId")):
                    matched_after = item
                    break

        validation = {
            key: self._values_match(expected, matched_after.get(key) if matched_after else None)
            for key, expected in updates_applied.items()
        }
        ok = bool(matched_after) and all(validation.values()) if validation else bool(matched_after)
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
        """Add a custom manual planner item."""
        meal_key = self.normalize_meal_key(meal_type)
        logger.debug("add_custom_item user=%s day=%s meal=%s name=%r kcal=%s", user_id, day, meal_key, name, calories)
        item_name = name.strip()
        if not item_name:
            raise FitatuApiError("Custom item name is required")
        planner_day = self.get_day(user_id, day)
        day_payload = self._build_day_sync_payload(planner_day)
        diet_plan = self._as_dict(day_payload.get("dietPlan"))
        if diet_plan is None:
            raise FitatuApiError("dietPlan not available in planner day response")
        meal_bucket = self._as_dict(diet_plan.get(meal_key))
        if meal_bucket is None:
            raise FitatuApiError(f"meal not found in day payload: {meal_type}")
        items = self._as_dict_list(meal_bucket.get("items"))
        meal_bucket["items"] = items
        new_item = {
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
        }
        items.append(new_item)
        day_payload["dietPlan"] = self._compact_diet_plan_for_sync(diet_plan)
        sync_response = self.sync_single_day(user_id, day, day_payload)
        return {
            "ok": True,
            "meal": meal_key,
            "name": item_name,
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

    @staticmethod
    def _deleted_item_marker(item: dict[str, Any], *, deleted_at: str | None = None) -> dict[str, Any]:
        """Build a frontend-style deletedAt marker. Shape confirmed in production (2026-04-22)."""
        marker: dict[str, Any] = {
            "planDayDietItemId": item.get("planDayDietItemId"),
            "foodType": item.get("foodType", "CUSTOM_ITEM"),
            "measureId": item.get("measureId", 1),
            "measureQuantity": item.get("measureQuantity", 1),
            "source": item.get("source", "API"),
            "deletedAt": deleted_at or PlannerModule._now_timestamp(),
            "updatedAt": PlannerModule._now_timestamp(),
        }
        food_type = str(marker.get("foodType") or "").upper()
        if food_type == "CUSTOM_ITEM":
            for key in ("name", "energy", "protein", "fat", "carbohydrate"):
                marker[key] = item.get(key, 0 if key != "name" else "x")
        else:
            product_id = item.get("productId")
            if product_id is not None:
                marker["productId"] = product_id
        return marker

    def _find_item_in_day_payload(
        self,
        day_payload: dict[str, Any],
        meal_key: str,
        item_id: str | int,
        *,
        any_meal: bool = False,
    ) -> dict[str, Any] | None:
        """Find an item by id in a day payload.

        With ``any_meal=True`` the search falls back to all meal buckets when the
        item is not found in ``meal_key`` — mirrors the cross-meal fallback in
        ``update_day_item``.
        """
        diet_plan = self._as_dict(day_payload.get("dietPlan"))
        if diet_plan is None:
            raise FitatuApiError("dietPlan not available in planner day response")
        meal_bucket = self._as_dict(diet_plan.get(meal_key))
        if meal_bucket is None:
            raise FitatuApiError(f"meal not found in day payload: {meal_key}")

        def _scan(bucket: dict[str, Any]) -> dict[str, Any] | None:
            for item in self._as_dict_list(bucket.get("items")):
                if str(item.get("planDayDietItemId")) == str(item_id) or str(item.get("productId")) == str(item_id):
                    return item
            return None

        found = _scan(meal_bucket)
        if found is not None or not any_meal:
            return found

        for alt_key, alt_raw in diet_plan.items():
            if alt_key == meal_key:
                continue
            alt_bucket = self._as_dict(alt_raw)
            if alt_bucket is None:
                continue
            found = _scan(alt_bucket)
            if found is not None:
                logger.debug("_find_item_in_day_payload: item %s not in %r, found in %r", item_id, meal_key, alt_key)
                return found
        return None

    def _get_day_retrying_for_item(
        self,
        user_id: str,
        day: date,
        meal_key: str,
        item_id: str | int,
        *,
        any_meal: bool = False,
        retries: int = 3,
        retry_delay: float = 1.5,
    ) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any] | None]:
        """Fetch the day snapshot, retrying until item_id is visible or retries are exhausted.

        The Fitatu backend sync is eventually consistent — a freshly added item may not
        appear in the next ``get_day`` call for up to a few seconds.  This helper
        transparently retries with a fixed delay so callers do not need to add manual
        ``time.sleep`` calls between add and update/remove operations.

        Returns ``(planner_day, day_payload, item)`` where ``item`` is ``None`` if the
        item was still not found after all retry attempts.
        """
        planner_day: dict[str, Any] = {}
        day_payload: dict[str, Any] = {}
        item: dict[str, Any] | None = None
        for attempt in range(max(1, retries)):
            planner_day = self.get_day(user_id, day)
            day_payload = self._build_day_sync_payload(planner_day)
            item = self._find_item_in_day_payload(day_payload, meal_key, item_id, any_meal=any_meal)
            if item is not None:
                return planner_day, day_payload, item
            if attempt < retries - 1:
                logger.debug(
                    "_get_day_retrying_for_item: item %s not found on attempt %d/%d, retrying in %.1fs",
                    item_id,
                    attempt + 1,
                    retries,
                    retry_delay,
                )
                time.sleep(retry_delay)
        return planner_day, day_payload, None

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
        """Rollback helper for snapshot-sync add flows.

        Hard delete for planner items is still experimental. If the backend keeps a
        custom item around after the removal sync, this falls back to a soft cleanup by
        shrinking quantity and optionally marking the item invisible.
        """
        if not plan_day_diet_item_id and product_id is None:
            raise FitatuApiError("rollback requires plan_day_diet_item_id or product_id")

        meal_key = self.normalize_meal_key(meal_type)
        planner_day = self.get_day(user_id, day)
        day_payload = self._build_day_sync_payload(planner_day)

        diet_plan = self._as_dict(day_payload.get("dietPlan"))
        if diet_plan is None:
            raise FitatuApiError("dietPlan not available in planner day response")

        meal_bucket = self._as_dict(diet_plan.get(meal_key))
        if meal_bucket is None:
            raise FitatuApiError(f"meal not found in day payload: {meal_type}")

        items_raw = meal_bucket.get("items")
        items = self._as_dict_list(items_raw)
        if not isinstance(items_raw, list):
            raise FitatuApiError("meal items are not available")
        meal_bucket["items"] = items

        before_count = len(items)
        removed_item: dict[str, Any] | None = None

        if plan_day_diet_item_id:
            for idx in range(len(items) - 1, -1, -1):
                item = items[idx]
                if str(item.get("planDayDietItemId")) == str(plan_day_diet_item_id):
                    removed_item = items.pop(idx)
                    break

        if removed_item is None and product_id is not None:
            for idx in range(len(items) - 1, -1, -1):
                item = items[idx]
                if str(item.get("productId")) == str(product_id):
                    removed_item = items.pop(idx)
                    break

        if removed_item is None:
            return {
                "ok": False,
                "meal": meal_key,
                "beforeCount": before_count,
                "afterCount": before_count,
                "removed": None,
                "message": "No matching item found to rollback",
            }

        day_payload["dietPlan"] = self._compact_diet_plan_for_sync(diet_plan)
        sync_response = self.sync_single_day(user_id, day, day_payload)

        reloaded_day = self.get_day(user_id, day)
        reloaded_diet = self._as_dict(reloaded_day.get("dietPlan"))
        reloaded_meal = self._as_dict(reloaded_diet.get(meal_key) if reloaded_diet else None)
        reloaded_items = self._as_dict_list(reloaded_meal.get("items") if reloaded_meal else None)
        after_count = len(reloaded_items)

        removed_id = str(removed_item.get("planDayDietItemId"))
        reloaded_ids = {
            str(i.get("planDayDietItemId"))
            for i in reloaded_items
            if i.get("planDayDietItemId") is not None
        }
        removed_id_absent = bool(removed_id and removed_id not in reloaded_ids)
        count_decreased = after_count < before_count

        ok = removed_id_absent or count_decreased
        cleanup_mode = "removed" if ok else "none"
        neutralize_sync_response: Any | None = None

        if not ok and neutralize_on_failure:
            reloaded_day_for_neutralize = self.get_day(user_id, day)
            reloaded_diet = self._as_dict(reloaded_day_for_neutralize.get("dietPlan"))
            if reloaded_diet is not None:
                reloaded_meal_bucket = self._as_dict(reloaded_diet.get(meal_key))
                reloaded_items2 = self._as_dict_list(
                    reloaded_meal_bucket.get("items") if reloaded_meal_bucket else None
                )
                for item in reloaded_items2:
                    if str(item.get("planDayDietItemId")) != str(removed_id):
                        continue
                    item["measureQuantity"] = neutralized_quantity
                    if mark_invisible:
                        item["visible"] = False
                    neutralize_payload: dict[str, Any] = {
                        "dietPlan": reloaded_diet,
                        "toiletItems": reloaded_day_for_neutralize.get("toiletItems")
                        or reloaded_day_for_neutralize.get("toilet")
                        or [],
                        "note": reloaded_day_for_neutralize.get("note"),
                        "tagsIds": reloaded_day_for_neutralize.get("tagsIds") or [],
                    }
                    neutralize_sync_response = self.sync_single_day(user_id, day, neutralize_payload)
                    post_neutralize_day = self.get_day(user_id, day)
                    post_diet = self._as_dict(post_neutralize_day.get("dietPlan"))
                    post_meal = self._as_dict(post_diet.get(meal_key) if post_diet else None)
                    post_neutralize_items = self._as_dict_list(
                        post_meal.get("items") if post_meal else None
                    )
                    for reloaded_item in post_neutralize_items:
                        if str(reloaded_item.get("planDayDietItemId")) != str(removed_id):
                            continue
                        qty = float(reloaded_item.get("measureQuantity") or 0)
                        if qty <= float(neutralized_quantity):
                            ok = True
                            cleanup_mode = "neutralized"
                        break
                    break

        return {
            "ok": ok,
            "meal": meal_key,
            "beforeCount": before_count,
            "afterCount": after_count,
            "removedId": removed_id,
            "removedIdAbsentAfterSync": removed_id_absent,
            "countDecreased": count_decreased,
            "cleanupMode": cleanup_mode,
            "neutralizedQuantity": neutralized_quantity if cleanup_mode == "neutralized" else None,
            "removed": removed_item,
            "syncResponse": sync_response,
            "neutralizeSyncResponse": neutralize_sync_response,
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
        """Try known route variants for planner product lookup."""
        return self._client.request_first_success(
            "GET",
            [
                f"/product/{product_id}/{meal_type}/{day.isoformat()}",
                f"/v2/product/{product_id}/{meal_type}/{day.isoformat()}",
            ],
        )

    def quick_add_form(self, payload: dict[str, Any]) -> Any:
        """Call the quick-add endpoint directly."""
        return self._client.request_first_success(
            "POST",
            ["/food/quick-add/form", "/v2/food/quick-add/form"],
            json_data=payload,
        )

    def quick_add_form_with_fallback(self, payload: dict[str, Any]) -> Any:
        """Attempt quick-add and fall back to search+add behavior on 404."""
        try:
            return self.quick_add_form(payload)
        except FitatuApiError as exc:
            if exc.status_code != 404:
                raise
            fallback = self._quick_add_form_fallback(payload)
            if fallback is not None:
                return fallback
            raise

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
        """Send a planner changes payload to the first route variant that works."""
        return self._client.request_first_success(
            method.upper(),
            ["/planner/changes", "/v2/planner/changes", "/v2/diet-plan/planner/changes"],
            json_data=payload,
        )

    def add_day_items(self, user_id: str, day: date, items: list[dict[str, Any]]) -> Any:
        """Try known route variants for adding day items directly."""
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
        """
        return self._client.request_first_success(
            "POST",
            [
                f"/diet-plan/{user_id}/days",
                f"/v2/diet-plan/{user_id}/days",
                f"/v3/diet-plan/{user_id}/days",
            ],
            json_data=days_payload,
            params={"synchronous": "true"} if synchronous else None,
        )

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
        """Move an item by syncing deletedAt marker + copied item.

        Searches all meal buckets when the item is not found in ``from_meal_type``
        (cross-meal fallback). Live-tested 2026-04-22.
        """
        target_day = to_day or from_day
        from_meal_key = self.normalize_meal_key(from_meal_type)
        to_meal_key = self.normalize_meal_key(to_meal_type or from_meal_type)

        source_snapshot = self.get_day(user_id, from_day)
        source_payload = self._build_day_sync_payload(source_snapshot)
        source_item = self._find_item_in_day_payload(source_payload, from_meal_key, item_id, any_meal=True)
        if source_item is None:
            raise FitatuApiError(f"item not found in meal: {from_meal_type}")

        moved_item = dict(source_item)
        moved_item["planDayDietItemId"] = str(uuid.uuid1())
        moved_item["updatedAt"] = self._now_timestamp()
        moved_item.pop("deletedAt", None)
        delete_marker = self._deleted_item_marker(source_item)

        days_payload: dict[str, Any] = {}
        if target_day == from_day:
            days_payload[from_day.isoformat()] = {
                "dietPlan": {
                    from_meal_key: {"items": [delete_marker]},
                    to_meal_key: {"items": [moved_item]},
                }
            }
        else:
            days_payload[from_day.isoformat()] = {
                "dietPlan": {from_meal_key: {"items": [delete_marker]}}
            }
            days_payload[target_day.isoformat()] = {
                "dietPlan": {to_meal_key: {"items": [moved_item]}}
            }

        sync_response = self.sync_days(user_id, days_payload, synchronous=synchronous)
        return {
            "ok": True,
            "experimental": True,
            "liveTested": True,
            "operation": "move_day_item",
            "fromDate": from_day.isoformat(),
            "toDate": target_day.isoformat(),
            "fromMeal": from_meal_key,
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
        """Replace item with a new CUSTOM_ITEM in one sync payload.

        Searches all meal buckets when the item is not found in ``meal_type``
        (cross-meal fallback). Live-tested 2026-04-22.
        """
        meal_key = self.normalize_meal_key(meal_type)
        planner_day = self.get_day(user_id, day)
        day_payload = self._build_day_sync_payload(planner_day)
        source_item = self._find_item_in_day_payload(day_payload, meal_key, item_id, any_meal=True)
        if source_item is None:
            raise FitatuApiError(f"item not found in meal: {meal_type}")

        delete_marker = self._deleted_item_marker(source_item)
        replacement = self._custom_item_from_values(
            name=name,
            calories=calories,
            protein_g=protein_g,
            fat_g=fat_g,
            carbs_g=carbs_g,
            source=source,
        )
        days_payload = {
            day.isoformat(): {
                "dietPlan": {
                    meal_key: {"items": [delete_marker, replacement]}
                }
            }
        }
        sync_response = self.sync_days(user_id, days_payload, synchronous=synchronous)
        return {
            "ok": True,
            "experimental": True,
            "liveTested": True,
            "operation": "replace_day_item_with_custom_item",
            "date": day.isoformat(),
            "meal": meal_key,
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
        """Remove a planner item by syncing a trimmed day snapshot."""
        meal_key = self.normalize_meal_key(meal_key)
        _, day_payload, removed_item = self._get_day_retrying_for_item(
            user_id, day, meal_key, item_id, any_meal=True,
        )

        diet_plan = self._as_dict(day_payload.get("dietPlan"))
        if diet_plan is None:
            raise FitatuApiError("dietPlan not available in planner day response")

        if removed_item is None:
            meal_bucket = self._as_dict(diet_plan.get(meal_key))
            before_count = len(self._as_dict_list((meal_bucket or {}).get("items")))
            return {
                "ok": False,
                "meal": meal_key,
                "beforeCount": before_count,
                "afterCount": before_count,
                "removedId": None,
                "removedIdAbsentAfterSync": False,
                "countDecreased": False,
                "cleanupMode": "none",
                "removed": None,
                "syncResponse": None,
            }

        # Pop the item from whichever bucket it lives in.
        before_count = 0
        for bucket_raw in diet_plan.values():
            bucket = self._as_dict(bucket_raw)
            if bucket is None:
                continue
            items = self._as_dict_list(bucket.get("items"))
            before_count += len(items)
            for idx in range(len(items) - 1, -1, -1):
                if str(items[idx].get("planDayDietItemId")) == str(item_id) or str(items[idx].get("productId")) == str(item_id):
                    items.pop(idx)
                    bucket["items"] = items
                    break

        day_payload["dietPlan"] = self._compact_diet_plan_for_sync(diet_plan)
        sync_response = self.sync_single_day(user_id, day, day_payload)

        reloaded_day = self.get_day(user_id, day)
        reloaded_diet = self._as_dict(reloaded_day.get("dietPlan"))
        reloaded_meal = self._as_dict(reloaded_diet.get(meal_key) if reloaded_diet else None)
        reloaded_items = self._as_dict_list(reloaded_meal.get("items") if reloaded_meal else None)
        after_count = len(reloaded_items)

        removed_id = str(removed_item.get("planDayDietItemId"))
        reloaded_ids = {
            str(reloaded_item.get("planDayDietItemId"))
            for reloaded_item in reloaded_items
            if reloaded_item.get("planDayDietItemId") is not None
        }
        removed_id_absent = bool(removed_id and removed_id not in reloaded_ids)
        count_decreased = after_count < before_count
        ok = removed_id_absent or count_decreased

        return {
            "ok": ok,
            "meal": meal_key,
            "beforeCount": before_count,
            "afterCount": after_count,
            "removedId": removed_id,
            "removedIdAbsentAfterSync": removed_id_absent,
            "countDecreased": count_decreased,
            "cleanupMode": "removed" if ok else "none",
            "removed": removed_item,
            "syncResponse": sync_response,
        }

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
        """Soft-remove a planner item by syncing the full day snapshot back."""
        meal_key = self.normalize_meal_key(meal_key)
        _, day_payload, target_item = self._get_day_retrying_for_item(
            user_id, day, meal_key, item_id, any_meal=True,
        )

        diet_plan = self._as_dict(day_payload.get("dietPlan"))
        if diet_plan is None:
            raise FitatuApiError("dietPlan not available in planner day response")

        meal_bucket = self._as_dict(diet_plan.get(meal_key))
        if meal_bucket is None:
            raise FitatuApiError(f"meal not found in day payload: {meal_key}")

        items_raw = meal_bucket.get("items")
        items = self._as_dict_list(items_raw)
        if not isinstance(items_raw, list):
            raise FitatuApiError("meal items are not available")

        before_count = len(items)

        if target_item is None:
            return {
                "ok": False,
                "meal": meal_key,
                "beforeCount": before_count,
                "afterCount": before_count,
                "removedId": None,
                "removedIdAbsentAfterSync": False,
                "countDecreased": False,
                "cleanupMode": "none",
                "useDeletedAt": use_deleted_at,
                "deletedAt": None,
                "removed": None,
                "syncResponse": None,
            }

        deleted_at = None
        if use_deleted_at:
            # Frontend-compatible deletion marker accepted by backend for API-sourced items.
            deleted_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            target_item["deletedAt"] = deleted_at

        target_item["measureQuantity"] = float(soft_delete_quantity)
        if mark_invisible:
            target_item["visible"] = False

        sync_response = self.sync_single_day(user_id, day, day_payload)

        reloaded_day = self.get_day(user_id, day)
        reloaded_diet = self._as_dict(reloaded_day.get("dietPlan"))
        reloaded_meal = self._as_dict(reloaded_diet.get(meal_key) if reloaded_diet else None)
        reloaded_items = self._as_dict_list(reloaded_meal.get("items") if reloaded_meal else None)
        after_count = len(reloaded_items)

        removed_id = str(target_item.get("planDayDietItemId"))
        reloaded_target = None
        for reloaded_item in reloaded_items:
            if str(reloaded_item.get("planDayDietItemId")) == removed_id:
                reloaded_target = reloaded_item
                break

        removed_id_absent = reloaded_target is None
        count_decreased = after_count < before_count
        target_visible = reloaded_target.get("visible") if reloaded_target is not None else None
        target_quantity = float(reloaded_target.get("measureQuantity") or 0) if reloaded_target is not None else 0.0
        reloaded_deleted_at = reloaded_target.get("deletedAt") if reloaded_target is not None else None
        soft_deleted_via_visibility_or_qty = bool(
            reloaded_target is not None
            and (target_visible is False or target_quantity <= float(soft_delete_quantity) + 1e-9)
        )
        ok = bool(removed_id_absent or reloaded_deleted_at is not None or soft_deleted_via_visibility_or_qty)

        cleanup_mode = "none"
        if removed_id_absent:
            cleanup_mode = "removed"
        elif reloaded_deleted_at is not None:
            cleanup_mode = "deleted_at"
        elif soft_deleted_via_visibility_or_qty:
            cleanup_mode = "soft_deleted"

        return {
            "ok": ok,
            "meal": meal_key,
            "beforeCount": before_count,
            "afterCount": after_count,
            "removedId": removed_id,
            "removedIdAbsentAfterSync": removed_id_absent,
            "countDecreased": count_decreased,
            "cleanupMode": cleanup_mode,
            "softDeleteQuantity": soft_delete_quantity if ok else None,
            "markInvisible": mark_invisible,
            "useDeletedAt": use_deleted_at,
            "deletedAt": reloaded_deleted_at or deleted_at,
            "removed": target_item,
            "syncResponse": sync_response,
        }

    def classify_day_item_for_removal(
        self,
        user_id: str,
        day: date,
        meal_key: str,
        item_id: str | int,
    ) -> dict[str, Any]:
        """Classify planner item into removal strategy buckets.

        The classification is runtime-shape-based, not intent-based. In practice this
        means the current live backend may classify rows differently than their
        creation story would suggest. For example, a `RECIPE` row may still resolve to
        `normal_item`, while a `CUSTOM_ITEM` created through an API quick-add flow may
        resolve to `custom_recipe_item` if it looks like a serving-sized API row.

        Buckets:
        - ``normal_item``: products/search-based planner rows
        - ``custom_add_item``: manual custom rows created in planner
        - ``custom_recipe_item``: recipe-like custom rows (serving-like quantity)
        """
        meal_key = self.normalize_meal_key(meal_key)
        _, _, target = self._get_day_retrying_for_item(
            user_id, day, meal_key, item_id, any_meal=True,
        )

        if target is None:
            return {
                "found": False,
                "requestedItemId": str(item_id),
                "resolvedKind": "normal_item",
                "reason": "item_not_found_defaulting_to_normal",
                "meal": meal_key,
                "item": None,
            }

        resolved_kind, reason = self._resolve_item_kind(target)

        return {
            "found": True,
            "requestedItemId": str(item_id),
            "resolvedKind": resolved_kind,
            "reason": reason,
            "meal": meal_key,
            "item": target,
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
        """Return day items enriched with removal kind metadata.

        This is designed for deterministic deletion workflows that first read the full
        day snapshot and then apply per-item strategies based on item type.
        """
        planner_day = self.get_day(user_id, day)
        day_payload = self._build_day_sync_payload(planner_day)
        diet_plan = self._as_dict(day_payload.get("dietPlan"))
        if diet_plan is None:
            raise FitatuApiError("dietPlan not available in planner day response")

        selected_meal: str | None = None
        if meal_key is not None:
            selected_meal = self.normalize_meal_key(meal_key)
            if self._as_dict(diet_plan.get(selected_meal)) is None:
                raise FitatuApiError(f"meal not found in day payload: {meal_key}")

        rows: list[dict[str, Any]] = []
        for current_meal_key, meal_raw in diet_plan.items():
            if selected_meal is not None and current_meal_key != selected_meal:
                continue
            meal_bucket = self._as_dict(meal_raw)
            if meal_bucket is None:
                continue
            items = self._as_dict_list(meal_bucket.get("items"))
            for item in items:
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
        """Remove items from a day using item-kind-aware strategy selection.

        This method reads the day snapshot once, selects matching rows by kind and
        optional meal filter, then removes them one-by-one with per-item strategy.
        """
        requested_kind = (item_kind or "").strip().lower()
        valid_kinds = {"normal_item", "custom_add_item", "custom_recipe_item", "auto"}
        if requested_kind not in valid_kinds:
            raise FitatuApiError(
                "item_kind must be one of: auto, normal_item, custom_add_item, custom_recipe_item"
            )
        logger.debug("remove_by_kind user=%s day=%s kind=%s meal=%s", user_id, day, requested_kind, meal_key)
        candidates = self.list_day_items_for_removal(user_id, day, meal_key=meal_key)
        targets: list[dict[str, Any]] = []
        for row in candidates:
            row_kind = str(row.get("resolvedKind") or "normal_item")
            if requested_kind != "auto" and row_kind != requested_kind:
                continue
            if not str(row.get("itemId") or "").strip():
                continue
            targets.append(row)

        if max_items is not None:
            targets = targets[: max(0, int(max_items))]

        attempts: list[dict[str, Any]] = []
        removed_count = 0
        for index, row in enumerate(targets, start=1):
            row_kind = str(row.get("resolvedKind") or "normal_item")
            item_id = str(row.get("itemId") or "")
            meal = str(row.get("meal") or "")
            result = self.remove_day_item_with_strategy(
                user_id,
                day,
                meal,
                item_id,
                item_kind=row_kind,
                delete_all_related_meals=delete_all_related_meals,
                use_aggressive_soft_delete=use_aggressive_soft_delete,
                max_soft_delete_retries=max_soft_delete_retries,
            )
            ok = self._is_cleanup_ok(result)
            if ok:
                removed_count += 1
            attempts.append(
                {
                    "seq": index,
                    "meal": meal,
                    "itemId": item_id,
                    "requestedKind": requested_kind,
                    "resolvedKind": row_kind,
                    "ok": ok,
                    "cleanupMode": result.get("cleanupMode") if isinstance(result, dict) else None,
                    "result": result,
                }
            )

        logger.debug("remove_by_kind done targeted=%d removed=%d failed=%d", len(targets), removed_count, len(targets) - removed_count)
        return {
            "ok": removed_count == len(targets),
            "requestedKind": requested_kind,
            "targetedCount": len(targets),
            "removedCount": removed_count,
            "failedCount": len(targets) - removed_count,
            "meal": self.normalize_meal_key(meal_key) if meal_key is not None else None,
            "attempts": attempts,
        }

    def _hard_delete_day_item(
        self,
        user_id: str,
        day: date,
        meal_key: str,
        item_id: str | int,
        *,
        delete_all_related_meals: bool,
    ) -> dict[str, Any]:
        """Issue a DELETE request across known route variants for a single diet item."""
        suffix = "true" if delete_all_related_meals else "false"
        response = self._client.request_first_success(
            "DELETE",
            [
                (
                    f"/diet-plan/{user_id}/day/{day.isoformat()}/{meal_key}/{item_id}"
                    f"?deleteAllRelatedMeals={suffix}"
                ),
                (
                    f"/v2/diet-plan/{user_id}/day/{day.isoformat()}/{meal_key}/{item_id}"
                    f"?deleteAllRelatedMeals={suffix}"
                ),
                (
                    f"/v3/diet-plan/{user_id}/day/{day.isoformat()}/{meal_key}/{item_id}"
                    f"?deleteAllRelatedMeals={suffix}"
                ),
            ],
        )
        return {
            "ok": True,
            "meal": meal_key,
            "removedId": str(item_id),
            "cleanupMode": "hard_deleted",
            "hardDeleteResponse": response,
        }

    @staticmethod
    def _hard_delete_unavailable_attempt() -> dict[str, Any]:
        """Return a sentinel attempt record indicating hard-delete is not available on this cluster."""
        return {
            "step": "hard_delete",
            "ok": False,
            "cleanupMode": "not_supported",
            "skipped": True,
            "reason": "hard delete route is not functional on the current live cluster",
        }

    @staticmethod
    def _is_cleanup_ok(result: dict[str, Any] | Any) -> bool:
        """Return True when a cleanup attempt result dict indicates success."""
        if not isinstance(result, dict):
            return bool(result)
        if "ok" in result:
            return bool(result.get("ok"))
        status = str(result.get("status") or "").strip().lower()
        return status in {"ok", "success"}

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
        """Remove planner item using strategy per item kind.

        Supported ``item_kind``:
        - ``auto``: infer from planner snapshot
        - ``normal_item``
        - ``custom_add_item``
        - ``custom_recipe_item``
        """
        meal_key = self.normalize_meal_key(meal_key)
        requested_kind = (item_kind or "auto").strip().lower()
        valid_kinds = {"auto", "normal_item", "custom_add_item", "custom_recipe_item"}
        if requested_kind not in valid_kinds:
            raise FitatuApiError(
                "item_kind must be one of: auto, normal_item, custom_add_item, custom_recipe_item"
            )

        classification = self.classify_day_item_for_removal(user_id, day, meal_key, item_id)
        resolved_kind = (
            classification.get("resolvedKind")
            if requested_kind == "auto"
            else requested_kind
        )
        if not isinstance(resolved_kind, str):
            resolved_kind = "normal_item"

        attempts: list[dict[str, Any]] = []

        def run_attempt(step: str, fn: Any) -> tuple[bool, dict[str, Any]]:
            """Execute one cleanup strategy step and record the result in attempts."""
            result = fn()
            ok = self._is_cleanup_ok(result)
            attempts.append(
                {
                    "step": step,
                    "ok": ok,
                    "cleanupMode": result.get("cleanupMode") if isinstance(result, dict) else None,
                    "result": result,
                }
            )
            return ok, cast(dict[str, Any], result) if isinstance(result, dict) else {"raw": result}

        last_result: dict[str, Any] | None = None

        def attempt_hard_delete() -> tuple[bool, dict[str, Any]]:
            """Record a hard-delete as unavailable on the current cluster."""
            attempts.append(self._hard_delete_unavailable_attempt())
            return False, {}

        def attempt_snapshot_remove() -> tuple[bool, dict[str, Any]]:
            """Try removing the item by resyncing the day snapshot without it."""
            return run_attempt(
                "snapshot_remove",
                lambda: self.remove_day_item_via_snapshot(user_id, day, meal_key, item_id),
            )

        def attempt_soft_deleted_at(step_name: str = "soft_deleted_at") -> tuple[bool, dict[str, Any]]:
            """Try a soft-delete by setting deletedAt on the item in the snapshot."""
            return run_attempt(
                step_name,
                lambda: self.soft_remove_day_item_via_snapshot(
                    user_id,
                    day,
                    meal_key,
                    item_id,
                    mark_invisible=False,
                    use_deleted_at=True,
                ),
            )

        def attempt_soft_aggressive() -> tuple[bool, dict[str, Any]]:
            """Try an aggressive soft-delete that also marks the item invisible."""
            if not use_aggressive_soft_delete:
                attempts.append(
                    {
                        "step": "soft_aggressive",
                        "ok": False,
                        "cleanupMode": "disabled",
                        "result": None,
                    }
                )
                return False, {}
            return run_attempt(
                "soft_aggressive",
                lambda: self.soft_remove_day_item_via_snapshot(
                    user_id,
                    day,
                    meal_key,
                    item_id,
                    soft_delete_quantity=0.0,
                    mark_invisible=True,
                    use_deleted_at=False,
                ),
            )

        if resolved_kind == "normal_item":
            for runner in (
                attempt_snapshot_remove,
                attempt_soft_deleted_at,
                attempt_soft_aggressive,
            ):
                ok, result = runner()
                last_result = result
                if ok:
                    break
        elif resolved_kind == "custom_add_item":
            for runner in (
                attempt_soft_deleted_at,
                attempt_snapshot_remove,
                attempt_soft_aggressive,
            ):
                ok, result = runner()
                last_result = result
                if ok:
                    break
        else:
            retry_count = max(1, int(max_soft_delete_retries))
            for idx in range(retry_count):
                ok, result = attempt_soft_deleted_at(step_name=f"soft_deleted_at_retry_{idx + 1}")
                last_result = result
                if ok:
                    break
            if not self._is_cleanup_ok(last_result):
                for runner in (
                    attempt_soft_aggressive,
                    attempt_snapshot_remove,
                ):
                    ok, result = runner()
                    last_result = result
                    if ok:
                        break

        final_ok = self._is_cleanup_ok(last_result)
        response: dict[str, Any] = dict(last_result or {})
        response.update(
            {
                "ok": final_ok,
                "requestedKind": requested_kind,
                "resolvedKind": resolved_kind,
                "classification": classification,
                "attempts": attempts,
            }
        )
        return response

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
        """Remove a planner item using automatic kind-based strategy selection.

        Returns a dict containing an ``ok`` boolean.  A falsy ``ok`` means all
        delete strategies were exhausted without backend confirmation — handle
        this case explicitly rather than assuming the item was removed.
        """
        logger.debug("remove_day_item user=%s day=%s meal=%s item=%s", user_id, day, meal_key, item_id)
        return self.remove_day_item_with_strategy(
            user_id,
            day,
            meal_key,
            item_id,
            item_kind="auto",
            delete_all_related_meals=delete_all_related_meals,
            use_aggressive_soft_delete=use_aggressive_soft_delete,
            max_soft_delete_retries=2,
        )

    def remove_activity_day_item(self, user_id: str, day: date, item_id: str | int) -> Any:
        """Try known route variants for deleting an activity item."""
        return self._client.request_first_success(
            "DELETE",
            [
                f"/activity-plan/{user_id}/day/{day.isoformat()}/{item_id}",
                f"/v2/activity-plan/{user_id}/day/{day.isoformat()}/{item_id}",
                f"/v3/activity-plan/{user_id}/day/{day.isoformat()}/{item_id}",
            ],
        )
