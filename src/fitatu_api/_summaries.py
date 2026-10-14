"""Plain-dict summaries of planner days and diet targets.

The facade and the tool layer both return these, so they are JSON-safe, snake_case
and leave out the raw payloads. Quantities and nutrition are rounded to 2 decimals: the API
sends float noise such as ``16.799999999999997``.
"""

from __future__ import annotations

from typing import Any, Literal, TypedDict, cast

from ._shapes import as_dict, as_dict_list

Macro = Literal["energy", "protein", "fat", "carbohydrate", "fiber", "sugars", "salt"]
MACRO_FIELDS: tuple[Macro, ...] = ("energy", "protein", "fat", "carbohydrate", "fiber", "sugars", "salt")


class ItemSummary(TypedDict):
    """One food row of a day; ``row_id`` names it. Energy in kcal, nutrients and ``weight`` in g."""

    row_id: str | None
    food_type: str | None
    product_id: int | None
    recipe_id: int | None
    name: str
    brand: str | None
    measure_id: int | None
    measure_name: str | None
    measure_quantity: float
    weight: float
    energy: float
    protein: float
    fat: float
    carbohydrate: float
    fiber: float
    sugars: float
    salt: float
    eaten: bool


class MealSummary(TypedDict):
    """One meal of a day: ``meal_key`` is the slot, ``meal_name`` the name the user gave it."""

    meal_key: str
    meal_name: str
    meal_time: str | None
    recommended_percent: float | None
    item_count: int
    totals: dict[str, float]
    items: list[ItemSummary]


class ActivitySummary(TypedDict):
    """One logged workout; ``row_id`` names it and ``energy`` is the kcal burned."""

    row_id: str | None
    activity_id: int | None
    name: str | None
    description: str | None
    energy: float


def _number(value: Any) -> float:
    """Return *value* as a float to 2 decimals; missing or non-numeric values (the API sends null) count as 0."""
    if isinstance(value, bool) or value is None:
        return 0.0
    try:
        return round(float(value), 2)
    except (TypeError, ValueError):
        return 0.0


def _rounded(totals: dict[str, float]) -> dict[str, float]:
    # Sums of rounded floats pick the noise up again (0.1 + 0.2).
    return {field: round(value, 2) for field, value in totals.items()}


def item_summary(item: dict[str, Any]) -> ItemSummary:
    """Summarise one ``dietPlan`` row."""
    summary: dict[str, Any] = {
        "row_id": item.get("planDayDietItemId"),
        "food_type": item.get("foodType"),
        "product_id": item.get("productId"),
        "recipe_id": item.get("recipeId"),
        "name": item.get("name") or "Unknown",
        "brand": item.get("brand"),
        "measure_id": item.get("measureId"),
        "measure_name": item.get("measureName"),
        "measure_quantity": _number(item.get("measureQuantity")),
        "weight": _number(item.get("weight")),
        **{field: _number(item.get(field)) for field in MACRO_FIELDS},
        "eaten": bool(item.get("eaten", False)),
    }
    return cast(ItemSummary, summary)  # mypy cannot check the ** of the nutrition fields


def activity_summary(activity: dict[str, Any]) -> ActivitySummary:
    """Summarise one ``activityPlan`` entry (a logged workout)."""
    return {
        "row_id": activity.get("uuid"),
        "activity_id": activity.get("activityId"),
        "name": activity.get("activityName"),
        "description": activity.get("activityDescription"),
        "energy": _number(activity.get("energy")),
    }


def day_summary(day: dict[str, Any], *, date: str, user_id: str | None = None) -> dict[str, Any]:
    """Summarise a planner day (``planner.get_day``) per meal, with totals.

    ``totals`` counts every row, planned or eaten; ``eaten_totals`` only rows marked
    eaten. Energy burned in ``activities`` is not subtracted from either. ``note`` is the
    user's free text for the day (None without one) and ``tags_ids`` the ids of its tags.
    """
    totals: dict[str, float] = dict.fromkeys(MACRO_FIELDS, 0.0)
    eaten_totals: dict[str, float] = dict.fromkeys(MACRO_FIELDS, 0.0)
    meals: list[MealSummary] = []
    for meal_key, meal_data in (as_dict(day.get("dietPlan")) or {}).items():
        if not isinstance(meal_data, dict):
            continue
        meal_totals: dict[str, float] = dict.fromkeys(MACRO_FIELDS, 0.0)
        items = [item_summary(row) for row in as_dict_list(meal_data.get("items"))]
        for item in items:
            for field in MACRO_FIELDS:
                meal_totals[field] += item[field]
                totals[field] += item[field]
                if item["eaten"]:
                    eaten_totals[field] += item[field]
        meals.append(
            {
                "meal_key": meal_key,
                "meal_name": meal_data.get("mealName") or meal_key,
                "meal_time": meal_data.get("mealTime"),
                "recommended_percent": meal_data.get("recommendedPercent"),
                "item_count": len(items),
                "totals": _rounded(meal_totals),
                "items": items,
            }
        )
    return {
        "user_id": user_id,
        "date": date,
        "totals": _rounded(totals),
        "eaten_totals": _rounded(eaten_totals),
        "meals": meals,
        "activities": [activity_summary(a) for a in as_dict_list(day.get("activityPlan"))],
        "note": day.get("note") or None,
        "tags_ids": day.get("tagsIds") if isinstance(day.get("tagsIds"), list) else [],
    }


def daily_targets(settings: dict[str, Any], date: str) -> dict[str, Any]:
    """Pull energy (kcal) and macro targets (grams and percent) out of ``user_settings.get_new``.

    ``date`` is the day the settings were read for; the payload's own ``date`` is when that
    version of the settings took effect, reported as ``settings_since``.
    """
    diet = as_dict(settings.get("userDietSettings")) or {}
    calculated = as_dict(settings.get("calculatedValues")) or {}
    return {
        "date": date,
        "settings_since": settings.get("date"),
        "energy": diet.get("energy"),
        **{
            macro: {"grams": diet.get(f"{macro}Weight"), "percent": diet.get(f"{macro}Percentage")}
            for macro in ("protein", "fat", "carbohydrate")
        },
        "bmr": calculated.get("BMR"),
        "tmr": calculated.get("TMR"),
    }
