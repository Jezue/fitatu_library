"""Plain-dict summaries of planner days and diet targets.

The facade and the tool layer both return these, so they are JSON-safe, snake_case
and leave out the raw payloads.
"""

from __future__ import annotations

from typing import Any

from ._shapes import as_dict, as_dict_list

MACRO_FIELDS = ("energy", "protein", "fat", "carbohydrate", "fiber", "sugars", "salt")


def _number(value: Any) -> float:
    """Return *value* as a float; missing or non-numeric values (the API sends null) count as 0."""
    if isinstance(value, bool) or value is None:
        return 0.0
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def item_summary(item: dict[str, Any]) -> dict[str, Any]:
    """Summarise one ``dietPlan`` row."""
    return {
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


def activity_summary(activity: dict[str, Any]) -> dict[str, Any]:
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
    eaten. Energy burned in ``activities`` is not subtracted from either.
    """
    totals = dict.fromkeys(MACRO_FIELDS, 0.0)
    eaten_totals = dict.fromkeys(MACRO_FIELDS, 0.0)
    meals: list[dict[str, Any]] = []
    for meal_key, meal_data in (as_dict(day.get("dietPlan")) or {}).items():
        if not isinstance(meal_data, dict):
            continue
        meal_totals = dict.fromkeys(MACRO_FIELDS, 0.0)
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
                "totals": meal_totals,
                "items": items,
            }
        )
    return {
        "user_id": user_id,
        "date": date,
        "totals": totals,
        "eaten_totals": eaten_totals,
        "meals": meals,
        "activities": [activity_summary(a) for a in as_dict_list(day.get("activityPlan"))],
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
