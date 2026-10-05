"""Record real API responses for the test suite and scrub them.

The unit tests replay ``tests/fixtures/api_recording.json`` instead of hand-written
fakes, so this script is how that file gets refreshed when Fitatu changes something:

    python -m fitatu_api login -o session_data.json
    python scripts/record_fixtures.py --session session_data.json

It only touches an empty day (``--day``, default 2027-01-03) and an empty measurement
date (``--measurement-day``, default 2020-01-01), uses public catalog items plus one
synthetic custom row, and deletes everything it created (also when a step fails). Before
anything is written, ``scrub`` replaces the user id, profile, goals, usage history and
custom meal names with fixed synthetic values, and ``leaked_values`` refuses to write the
file if any personal value from the raw recording is still in it.
"""

from __future__ import annotations

import argparse
import copy
import json
import secrets
import sys
import uuid
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

FAKE_USER_ID = "1234567"
OUTPUT = Path(__file__).resolve().parent.parent / "tests" / "fixtures" / "api_recording.json"

NEUTRAL_MEALS = {
    "breakfast": ("Breakfast", "08:00"),
    "second_breakfast": ("Second breakfast", "11:00"),
    "lunch": ("Lunch", "14:00"),
    "dinner": ("Dinner", "17:00"),
    "snack": ("Snack", "19:00"),
    "supper": ("Supper", "21:00"),
}
SYNTHETIC_PROFILE = {
    "sex": 1,
    "birthDate": "1990-01-01",
    "age": 36,
    "height": 180,
    "heightCm": 180,
    "weightStart": 85,
    "weightStartKg": 85,
    "weightCurrent": 80,
    "weightCurrentKg": 80,
    "weightTarget": 75,
    "weightTargetKg": 75,
}
SYNTHETIC_DIET = {
    "energy": 2000,
    "proteinPercentage": 20,
    "proteinWeight": 100,
    "fatPercentage": 30,
    "fatWeight": 67,
    "carbohydratePercentage": 50,
    "carbohydrateWeight": 250,
    "activityBase": 1.4,
    "activityTraining": 1.4,
    "weightChangeSpeed": 0,
    "weightChangeSpeedKg": 0,
    "weightChangeDirection": 0,
}
SYNTHETIC_CALCULATED = {
    "BMR": 1700,
    "TMR": 2300,
    "PAL": 1.4,
    "calculatedEnergy": 2000,
    "calculatedTempo": 0,
    "deficit": 0,
    "activityEnergy": 0,
}
# Top-level settings keys that may stay (with scrubbed contents); anything else is dropped.
SETTINGS_KEYS = {
    "userId", "date", "userSettings", "plannerSettings", "userDietSettings", "calculatedValues",
    "foodProposalSettings", "updatedAt", "defaultDietSettings", "defaultUserSettings",
    "weekMealSchema", "waterSettings", "weekMealRecommendedEnergy",
}
# Keys whose values are labels/enums, not personal data.
LABEL_KEYS = {"meal", "mealId", "label", "mealLabel", "active", "required", "selected", "date", "userId"}


def _neutral(value: Any, key: str = "") -> Any:
    """Keep the shape of a value but drop its content (fail closed)."""
    if key.endswith("Unit") or key in LABEL_KEYS:
        return value
    if isinstance(value, dict):
        return {k: _neutral(v, k) for k, v in value.items()}
    if isinstance(value, list):
        return []
    if isinstance(value, bool):
        return False
    if isinstance(value, (int, float)):
        return 0
    return None


def _replace_strings(value: Any, replacements: dict[str, str]) -> Any:
    if isinstance(value, dict):
        return {key: _replace_strings(item, replacements) for key, item in value.items()}
    if isinstance(value, list):
        return [_replace_strings(item, replacements) for item in value]
    if isinstance(value, str):
        for old, new in replacements.items():
            value = value.replace(old, new)
        return value
    return value


def _replace_user_id(value: Any, user_id: str) -> Any:
    if isinstance(value, dict):
        return {key: _replace_user_id(item, user_id) for key, item in value.items()}
    if isinstance(value, list):
        return [_replace_user_id(item, user_id) for item in value]
    if isinstance(value, str):
        return value.replace(user_id, FAKE_USER_ID)
    if isinstance(value, int) and not isinstance(value, bool) and str(value) == user_id:
        return int(FAKE_USER_ID)
    return value


def _meal_entries(settings: dict[str, Any]) -> list[dict[str, Any]]:
    """Every meal entry in the week meal schema (default and per-day custom lists)."""
    schema = settings.get("weekMealSchema") or {}
    found: list[dict[str, Any]] = []

    def walk(node: Any) -> None:
        if isinstance(node, dict):
            if "mealName" in node or "mealTime" in node:
                found.append(node)
            for child in node.values():
                walk(child)
        elif isinstance(node, list):
            for child in node:
                walk(child)

    walk(schema)
    return found


def _personal_meal_strings(calls: dict[str, Any]) -> dict[str, str]:
    """Custom meal names (they also show up in the CSV export) -> neutral names."""
    names: dict[str, str] = {}
    for call in calls.values():
        body = call.get("body")
        if isinstance(body, dict) and isinstance(body.get("dietPlan"), dict):
            for meal_key, meal in body["dietPlan"].items():
                if isinstance(meal, dict) and meal.get("mealName") and meal_key in NEUTRAL_MEALS:
                    names[str(meal["mealName"])] = NEUTRAL_MEALS[meal_key][0]
    return names


def scrub(recording: dict[str, Any]) -> dict[str, Any]:
    """Return a copy of a raw recording with personal data replaced by synthetic values."""
    user_id = str(recording["uid"])
    calls = _replace_strings(_replace_user_id(copy.deepcopy(recording["calls"]), user_id), _personal_meal_strings(recording["calls"]))

    for call in calls.values():
        body = call.get("body")
        if isinstance(body, dict):
            for meal_key, meal in (body.get("dietPlan") or {}).items():
                if isinstance(meal, dict) and meal_key in NEUTRAL_MEALS:
                    meal["mealName"], meal["mealTime"] = NEUTRAL_MEALS[meal_key]
                    if isinstance(meal.get("recommendedEnergy"), (int, float)):
                        meal["recommendedEnergy"] = 400
            # Per-account flags on catalog items.
            for flag, value in (("favorite", False), ("currentUserPrivateBarcode", None)):
                if flag in body:
                    body[flag] = value

    settings = (calls.get("settings_new") or {}).get("body")
    if isinstance(settings, dict):
        for key in list(settings):
            if key not in SETTINGS_KEYS:
                del settings[key]
        for section, synthetic in (
            ("userSettings", SYNTHETIC_PROFILE),
            ("userDietSettings", SYNTHETIC_DIET),
            ("calculatedValues", SYNTHETIC_CALCULATED),
        ):
            if isinstance(settings.get(section), dict):
                settings[section] = {k: synthetic.get(k, _neutral(v, k)) for k, v in settings[section].items()}
        for key in ("plannerSettings", "waterSettings", "weekMealRecommendedEnergy", "foodProposalSettings"):
            if key in settings:
                settings[key] = _neutral(settings[key])
        for meal in _meal_entries(settings):
            label = meal.get("label") or meal.get("mealLabel")
            name, time = NEUTRAL_MEALS.get(str(label), ("Meal", "12:00"))
            meal["mealName"], meal["mealTime"] = name, time

    search = calls.get("search_food") or {}
    if isinstance(search.get("body"), list):
        # Search puts the account's own history first ("LAST_USED") and counts its uses;
        # keep only plain catalog hits, without the per-account fields.
        search["body"] = [row for row in search["body"] if isinstance(row, dict) and row.get("index") != "LAST_USED"]
        for row in search["body"]:
            row.update({"userNumberOfUse": 0, "favorite": False})
            row.pop("lastUsed", None)

    nutrition = (calls.get("summary_nutrition") or {}).get("body")
    if isinstance(nutrition, dict):
        for nutrient in nutrition.values():
            if isinstance(nutrient, dict):
                for bound in ("min", "max"):
                    if nutrient.get(bound) is not None:
                        nutrient[bound] = 100
    for name, key, value in (("summary_nutrient_energy", "targets", 2000), ("measurement_weight_chart", "targets", 75)):
        body = (calls.get(name) or {}).get("body")
        if isinstance(body, dict) and isinstance(body.get(key), dict):
            body[key] = {day: value for day in body[key]}
    activities = (calls.get("summary_activities") or {}).get("body")
    if isinstance(activities, dict) and activities.get("targetEnergy") is not None:
        activities["targetEnergy"] = 300
    for name in ("measurement_weight_history", "measurement_body_part_waist"):
        for row in (calls.get(name) or {}).get("body") or []:
            if isinstance(row, dict) and "difference" in row:
                row["difference"] = None  # relative to the account's earlier, real measurements

    revisions = (calls.get("day_revisions") or {}).get("body")
    if isinstance(revisions, list):
        calls["day_revisions"]["body"] = revisions[:3]

    return {"recordedAt": recording.get("recordedAt"), "userId": FAKE_USER_ID, "calls": calls}


def leaked_values(raw: dict[str, Any], scrubbed_text: str) -> list[str]:
    """Personal values from the raw recording that still appear in the scrubbed output."""
    calls = raw["calls"]
    sensitive = {str(raw["uid"])}
    sensitive.update(_personal_meal_strings(calls))
    settings = (calls.get("settings_new") or {}).get("body") or {}
    for meal in _meal_entries(settings):
        sensitive.update(str(meal[k]) for k in ("mealName", "mealTime") if meal.get(k))
    for call in calls.values():
        for meal in ((call.get("body") or {}).get("dietPlan") or {}).values() if isinstance(call.get("body"), dict) else []:
            if isinstance(meal, dict) and meal.get("mealTime"):
                sensitive.add(str(meal["mealTime"]))
    for key in ("birthDate", "nickname", "email", "username"):
        for source in (settings, settings.get("userSettings") or {}):
            if source.get(key):
                sensitive.add(str(source[key]))
    neutral = {name for name, _ in NEUTRAL_MEALS.values()} | {t for _, t in NEUTRAL_MEALS.values()}
    return sorted(value for value in sensitive - neutral if len(value) >= 4 and f'"{value}' in scrubbed_text)


def _uuid_v1() -> str:
    # The day sync only accepts UUID v1. Use a random node (multicast bit set, RFC 4122
    # section 4.5) so the recorder's MAC address never ends up in the committed file.
    return str(uuid.uuid1(node=secrets.randbits(48) | 1 << 40))


class RecordingError(RuntimeError):
    """Raised when a step fails in a way the fixture must not capture."""


def record(client: Any, user_id: str, day: date, measurement_day: date) -> dict[str, Any]:
    """Run the recording scenario and return the raw calls (real statuses and bodies)."""
    import requests

    calls: dict[str, Any] = {}
    day_iso = day.isoformat()

    def call(name: str | None, method: str, path: str, *, query: dict[str, Any] | None = None, body: Any = None) -> Any:
        response = requests.request(
            method,
            client._url(path),
            headers=client._headers(include_auth=True),
            params=query,
            json=body,
            timeout=client.timeout_seconds,
        )
        content_type = response.headers.get("content-type") or ""
        parsed: Any = response.text
        if "json" in content_type and response.text:
            parsed = response.json()
        rejected = path.endswith("/days") and isinstance(parsed, list) and any(row.get("errorMessage") for row in parsed)
        if rejected and name != "sync_rejected":
            raise RecordingError(f"{name}: day sync rejected: {parsed}")
        if name:
            calls[name] = {
                "request": {"method": method, "path": path, "query": query, "body": body},
                "status": response.status_code,
                "contentType": content_type or None,
                "body": parsed,
            }
        return parsed

    def rows(payload: Any) -> list[tuple[str, dict[str, Any]]]:
        diet_plan = payload.get("dietPlan") if isinstance(payload, dict) else None
        return [(meal, row) for meal, bucket in (diet_plan or {}).items() for row in (bucket or {}).get("items", [])]

    day_path = f"/diet-and-activity-plan/{user_id}/day/{day_iso}"
    sync_path = f"/diet-plan/{user_id}/days"
    measurement_path = f"/users/{user_id}/measurements/{measurement_day.isoformat()}"

    def sync(name: str | None, diet_plan: dict[str, Any]) -> Any:
        return call(name, "POST", sync_path, query={"synchronous": "true"}, body={day_iso: {"dietPlan": diet_plan}})

    def now() -> str:
        return datetime.now(UTC).strftime("%Y-%m-%d %H:%M:%S")

    if rows(call("day_empty", "GET", day_path)):
        raise SystemExit(f"{day_iso} is not empty; pick another --day")
    call("measurement_missing", "GET", measurement_path)
    if calls["measurement_missing"]["status"] != 404:
        raise SystemExit(f"{measurement_day} already has a measurement; pick another --measurement-day")

    measurement_written = False
    try:
        found = call("search_food", "GET", "/search/new/food", query={"phrase": "jajko kurze", "page": 1, "limit": 5, "locale": "pl_PL"})
        product_id = next(row["foodId"] for row in found if row.get("type") == "PRODUCT")
        product = call("product_details", "GET", f"/products/{product_id}")
        call("recipe_details", "GET", "/recipes/590200")
        measure_id = (product.get("measures") or [{}])[0].get("id", 1)

        product_row_id, recipe_row_id, custom_row_id = _uuid_v1(), _uuid_v1(), _uuid_v1()
        sync("sync_add_product", {"breakfast": {"items": [{"planDayDietItemId": product_row_id, "foodType": "PRODUCT", "productId": product_id, "measureId": measure_id, "measureQuantity": 1, "source": "API"}]}})
        sync("sync_add_recipe", {"lunch": {"items": [{"planDayDietItemId": recipe_row_id, "foodType": "RECIPE", "recipeId": 590200, "measureId": 39, "measureQuantity": 1, "ingredientsServing": 1, "source": "API"}]}})
        sync("sync_add_custom", {"snack": {"items": [{"planDayDietItemId": custom_row_id, "foodType": "CUSTOM_ITEM", "name": "fitatu-api fixture", "measureId": 1, "measureQuantity": 1, "energy": 250, "protein": 10, "fat": 8, "carbohydrate": 30, "source": "API"}]}})
        three = call("day_three_rows", "GET", day_path)

        product_row = next(row for _, row in rows(three) if row["planDayDietItemId"] == product_row_id)
        sync("sync_update_product", {"breakfast": {"items": [{**product_row, "eaten": True, "measureQuantity": 2}]}})
        call("day_after_update", "GET", day_path)
        sync("sync_row_without_eaten", {"breakfast": {"items": [{"planDayDietItemId": product_row_id, "foodType": "PRODUCT", "productId": product_id, "measureId": measure_id, "measureQuantity": 2, "source": "API"}]}})
        call("day_after_row_without_eaten", "GET", day_path)
        sync("sync_omit_rows", {"lunch": {"items": []}})
        call("day_after_omission", "GET", day_path)
        sync("sync_rejected", {"snack": {"items": [{"planDayDietItemId": str(uuid.uuid4()), "foodType": "CUSTOM_ITEM", "name": "rejected", "measureId": 1, "measureQuantity": 1, "energy": 1, "protein": 0, "fat": 0, "carbohydrate": 0, "source": "API"}]}})

        current = next(row for _, row in rows(call(None, "GET", day_path)) if row["planDayDietItemId"] == product_row_id)
        sync("sync_delete_product", {"breakfast": {"items": [{**current, "deletedAt": now()}]}})
        call("day_after_delete", "GET", day_path)
        call("day_revisions", "GET", f"/diet-plan/{user_id}/days/{day_iso}/revisions")

        span = {"fromDate": day_iso, "toDate": day_iso}
        call("summary_nutrition", "GET", f"/v2/diet-plan/{user_id}/summary/custom", query=span)
        call("summary_nutrient_energy", "GET", f"/v2/diet-plan/{user_id}/summary/energy/custom", query=span)
        call("summary_top_products_energy", "GET", f"/diet-plan/{user_id}/summary-product/energy/custom", query={**span, "page": 1, "limit": 5})
        call("summary_activities", "GET", f"/activity-plan/{user_id}/summary/custom", query=span)
        call("export_csv", "GET", f"/diet-plan/{user_id}/export", query={"format": "csv", **span})
        call("export_empty_range", "GET", f"/diet-plan/{user_id}/export", query={"format": "csv", "fromDate": "2027-02-01", "toDate": "2027-02-02"})
        call("export_reversed_range", "GET", f"/diet-plan/{user_id}/export", query={"format": "csv", "fromDate": "2027-01-04", "toDate": day_iso})

        leftovers: dict[str, Any] = {}
        for meal, row in rows(call(None, "GET", day_path)):
            leftovers.setdefault(meal, {"items": []})["items"].append({**row, "deletedAt": now()})
        if leftovers:
            sync("sync_cleanup", leftovers)
        call("day_after_cleanup", "GET", day_path)

        m_span = {"fromDate": measurement_day.isoformat(), "toDate": measurement_day.isoformat()}
        measurement_written = True
        call("measurement_put", "PUT", measurement_path, body={"date": measurement_day.isoformat(), "weight": "70.0", "waist": "80.0", "fatPercentage": "0.0", "weightUnit": "KG", "sizeUnit": "CM"})
        call("measurement_day", "GET", measurement_path)
        call("measurement_weight_history", "GET", f"/users/{user_id}/measurements/summary/weight", query={**m_span, "limit": 41, "page": 1})
        call("measurement_weight_chart", "GET", f"/users/{user_id}/measurements/chart/weight", query=m_span)
        call("measurement_body_part_waist", "GET", f"/users/{user_id}/measurements/size/waist", query={**m_span, "limit": 41, "page": 1})
        call("measurement_size_summary", "GET", f"/users/{user_id}/measurements/summary/size", query=m_span)
        call("measurement_delete", "DELETE", measurement_path)
        call("measurement_after_delete", "GET", measurement_path)

        call("settings_new", "GET", f"/users/{user_id}/settings-new/{day_iso}")
        call("settings_new_without_date", "GET", f"/users/{user_id}/settings-new")
        call("activities_catalog", "GET", "/activities", query={"page": 1, "limit": 2, "locale": "pl_PL"})
        call("activity_search", "GET", "/search/activity/bieganie", query={"page": 1, "limit": 2, "locale": "pl_PL"})
    finally:
        # Leave the account as we found it, even if a step above failed.
        left_behind = []
        try:
            leftover_rows: dict[str, Any] = {}
            for meal, row in rows(call(None, "GET", day_path)):
                leftover_rows.setdefault(meal, {"items": []})["items"].append({**row, "deletedAt": now()})
            if leftover_rows:
                sync(None, leftover_rows)
            if rows(call(None, "GET", day_path)):
                left_behind.append(f"rows on {day_iso}")
        except Exception as exc:  # noqa: BLE001 - report every cleanup failure, keep going
            left_behind.append(f"rows on {day_iso} ({exc})")
        if measurement_written:
            try:
                call(None, "DELETE", measurement_path)
                call("measurement_cleanup_check", "GET", measurement_path)
                if calls.pop("measurement_cleanup_check")["status"] != 404:
                    left_behind.append(f"measurement on {measurement_day}")
            except Exception as exc:  # noqa: BLE001
                left_behind.append(f"measurement on {measurement_day} ({exc})")
        if left_behind:
            raise RecordingError("cleanup failed, remove by hand: " + ", ".join(left_behind))

    return {"uid": user_id, "recordedAt": datetime.now(UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z"), "calls": calls}


def main(argv: list[str] | None = None) -> int:
    from fitatu_api import FitatuApiClient, FitatuAuthContext

    parser = argparse.ArgumentParser(description=(__doc__ or "").splitlines()[0])
    parser.add_argument("--session", required=True, help="session_data.json from `fitatu-api login`")
    parser.add_argument("--day", type=date.fromisoformat, default=date(2027, 1, 3))
    parser.add_argument("--measurement-day", type=date.fromisoformat, default=date(2020, 1, 1))
    parser.add_argument("--output", type=Path, default=OUTPUT)
    args = parser.parse_args(argv)

    auth = FitatuAuthContext.from_session_data(json.loads(Path(args.session).read_text(encoding="utf-8")))
    if not auth.fitatu_user_id:
        raise SystemExit("session has no fitatu_user_id")
    with FitatuApiClient(auth=auth) as client:
        raw = record(client, auth.fitatu_user_id, args.day, args.measurement_day)
    text = json.dumps(scrub(raw), ensure_ascii=False, indent=1) + "\n"
    leaks = leaked_values(raw, text)
    if leaks:
        print(f"not written: personal values survived scrubbing: {leaks}", file=sys.stderr)
        return 1
    args.output.write_text(text, encoding="utf-8")
    print(f"wrote {args.output} ({len(raw['calls'])} calls)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
