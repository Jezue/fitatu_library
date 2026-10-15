"""A small made-up Fitatu kept in memory, for :meth:`FitatuTools.demo` and ``fitatu-mcp --demo``.

:class:`DemoClient` is a :class:`FitatuApiClient` whose requests never leave the process: they
are answered from ``demo_data.json`` (a few products, recipes and activities, three days of diary,
weights and water around today) in the shapes the real API uses, and writes change only this
copy. It covers the routes the agent tools use; any other request is an error that says so.
"""

from __future__ import annotations

import datetime as dt
import json
import re
import unicodedata
from importlib import resources
from typing import Any
from urllib.parse import unquote
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from ._summaries import MACRO_FIELDS
from .auth import FitatuAuthContext
from .client import FitatuApiClient
from .exceptions import FitatuApiError, FitatuNotFoundError
from .planner import _new_row_id

_DATA = json.loads(resources.files("fitatu_api").joinpath("demo_data.json").read_text(encoding="utf-8"))
_SIZES = ("neck", "chest", "waist", "stomach", "hips", "thigh", "calf", "biceps", "fatPercentage")
# A product's measures by the app's measureKey, with the ids Fitatu gives them (checked live).
_MEASURE_KEYS = {"PIECE": (46, "piece"), "PACKAGE": (2, "package"), "PORTION": (39, "portion"), "SLICE": (8, "slice"), "BAR": (54, "bar")}


def _fold(text: str) -> str:
    """Lower case without Polish diacritics, so 'jablko' finds 'Jabłko'."""
    text = unicodedata.normalize("NFKD", text.lower().replace("ł", "l"))
    return "".join(char for char in text if not unicodedata.combining(char))


def _matches(phrase: str, name: str) -> bool:
    # The first four letters of each word, so 'jajka' finds 'Jajko' as the real search does.
    return all(word[:4] in _fold(name) for word in _fold(phrase).split() if len(word) > 1)


class DemoBackend:
    """The in-memory API: :meth:`request` answers like Fitatu would."""

    def __init__(self) -> None:
        self.user_id: str = _DATA["user_id"]
        try:
            self.today = dt.datetime.now(ZoneInfo("Europe/Warsaw")).date()  # what get_today calls today
        except ZoneInfoNotFoundError:  # pragma: no cover - Windows without tzdata
            self.today = dt.date.today()
        self.foods: dict[tuple[str, int], dict[str, Any]] = {}
        for key, product in _DATA["products"].items():
            self._add_product(int(key), product)
        for key, recipe in _DATA["recipes"].items():
            self._add_recipe(int(key), recipe)
        self.favorites = {(kind, food_id) for kind, food_id in _DATA["favorites"]}
        self.days: dict[str, dict[str, tuple[str, dict[str, Any]]]] = {}
        for offset, meals in _DATA["days"].items():
            for meal, rows in meals.items():
                for kind, food_id, measure_id, quantity, eaten in rows:
                    key = "productId" if kind == "PRODUCT" else "recipeId"
                    row = {"planDayDietItemId": _new_row_id(), "foodType": kind, key: food_id, "measureId": measure_id, "measureQuantity": quantity, "eaten": eaten}
                    self._rows(self._day(offset))[row["planDayDietItemId"]] = (meal, self._filled(row))
        self.activities: dict[str, dict[str, dict[str, Any]]] = {}
        for offset, logged in _DATA["day_activities"].items():
            for activity_id, minutes, energy in logged:
                activity = {"uuid": _new_row_id(), "activityId": activity_id, "energy": energy, "simpleMeasure": {"measureId": 1, "value": minutes}}
                self._store_activity(self._day(offset), activity)
        self.water = {self._day(offset): ml for offset, ml in _DATA["water"]["days"].items()}
        self.targets = dict(_DATA["targets"])
        self.glass_ml: int = _DATA["water"]["glass_ml"]
        self.manual: dict[str, Any] | None = None  # the manual goal's userDietSettings fields, as sent
        self.notes: dict[str, dict[str, Any]] = {}  # note and tagsIds per day, as the sync sent them
        self.measurements: dict[str, dict[str, Any]] = {}
        for offset, weight in _DATA["weights"].items():
            self.measurements.setdefault(self._day(offset), {})["weight"] = weight
        for offset, sizes in _DATA["sizes"].items():
            self.measurements.setdefault(self._day(offset), {}).update(sizes)

    def _day(self, offset: str) -> str:
        return (self.today + dt.timedelta(days=int(offset))).isoformat()

    def _add_product(self, product_id: int, product: dict[str, Any]) -> None:
        measures = [{"id": 1, "name": "g", "weight": 1, "capacity": None}]
        if product.get("liquid"):
            measures.append({"id": 17, "name": "ml", "weight": 1, "capacity": 1})
        for measure_id, name, weight in product["measures"]:
            measures.append({"id": measure_id, "name": name, "weight": weight, "capacity": weight if product.get("liquid") else None})
        self._add_food("PRODUCT", product_id, product, base=100, measures=measures)

    def _add_food(self, kind: str, food_id: int, data: dict[str, Any], *, base: float, measures: list[dict[str, Any]]) -> None:
        self.foods[kind, food_id] = {
            "id": food_id,
            "type": kind,
            "name": data["name"],
            "brand": data.get("brand"),
            "base": base,  # nutrition is per this many grams: 100 for a product, a portion for a recipe
            "nutrition": dict(zip(MACRO_FIELDS, data["nutrition"], strict=True)),
            "measures": measures,
            "barcode": data.get("barcode"),
            "own": data.get("own", False),
            "deleted": False,
            "liquid": data.get("liquid", False),
            "saturatedFat": data.get("saturatedFat"),
        }

    def _add_recipe(self, recipe_id: int, recipe: dict[str, Any]) -> None:
        measures = [{"id": 1, "name": "g", "weight": 1, "capacity": None}, {"id": 39, "name": "portion", "weight": recipe["weight"], "capacity": None}]
        self._add_food("RECIPE", recipe_id, recipe, base=recipe["weight"], measures=measures)
        # Its products as [product_id, measure_id, quantity], for the servings it makes.
        self.foods["RECIPE", recipe_id] |= {"items": recipe.get("items", []), "serving": recipe.get("serving", 1), "description": recipe.get("description")}
        # What the catalog search filters on: tags as bare names (vegan for tag.vegan).
        self.foods["RECIPE", recipe_id] |= {"cooking_time": recipe.get("cooking_time"), "meals": recipe.get("meals", []), "tags": recipe.get("tags", [])}

    # -- the shapes the API sends ----------------------------------------------------
    def _measure(self, food: dict[str, Any], measure_id: Any) -> dict[str, Any] | None:
        return next((measure for measure in food["measures"] if str(measure["id"]) == str(measure_id)), None)

    def _filled(self, row: dict[str, Any]) -> dict[str, Any]:
        """A synced row as the day GET returns it: the server fills in name and nutrition."""
        filled = {"eaten": False, "source": "API", **row}  # a row is replaced as a whole, eaten false unless sent
        if row.get("foodType") == "CUSTOM_ITEM":
            return {**{field: 0 for field in MACRO_FIELDS}, **filled, "measureName": "portion", "weight": None}
        food = self.foods[row["foodType"], int(row.get("productId") or row.get("recipeId") or 0)]
        measure = self._measure(food, row["measureId"])
        weight = (measure or {"weight": 0})["weight"] * float(row["measureQuantity"])
        nutrition = {field: round(value * weight / food["base"], 2) for field, value in food["nutrition"].items()}
        return {**filled, "name": food["name"], "brand": food["brand"], "measureName": (measure or {}).get("name"), "weight": weight, **nutrition}

    def _rejection(self, row: dict[str, Any]) -> str | None:
        kind = row.get("foodType")
        if kind == "CUSTOM_ITEM":
            return None
        food = self.foods.get((str(kind), int(row.get("productId") or row.get("recipeId") or 0)))
        if food is None or food["deleted"]:
            return f"{kind} {row.get('productId') or row.get('recipeId')} does not exist"
        if self._measure(food, row.get("measureId")) is None:
            return f"{kind} {food['id']} has no measure {row.get('measureId')}"
        return None

    def _hit(self, food: dict[str, Any], uses: int = 0) -> dict[str, Any]:
        measure = next((m for m in food["measures"] if m["id"] != 1 and m["name"] != "ml"), food["measures"][0])
        energy = round(food["nutrition"]["energy"] * measure["weight"] / food["base"], 2)
        return {
            "foodId": food["id"],
            "type": food["type"],
            "name": food["name"],
            "brand": food["brand"] or "",
            "energy": energy,
            "measure": {
                "measureId": measure["id"],
                "measureName": measure["name"],
                "measureQuantity": 1,
                "measureWeight": measure["weight"],
                "measureCapacity": measure["capacity"],
                "measureEnergy": energy,
            },
            "userNumberOfUse": uses,
            "favorite": (food["type"], food["id"]) in self.favorites,
        }

    def _details(self, food: dict[str, Any]) -> dict[str, Any]:
        measures = [
            {
                "id": m["id"],
                "name": m["name"],
                "weightPerUnit": m["weight"],
                **({"capacityPerUnit": m["capacity"]} if m["capacity"] else {}),
                "energyPerUnit": round(food["nutrition"]["energy"] * m["weight"] / food["base"], 4),
            }
            for m in food["measures"]
        ]
        own = {"private": True, "shared": False, "userId": int(self.user_id)} if food["own"] else {"private": False, "shared": True, "userId": 1}
        # A barcode the user gave their own product stays theirs, as live.
        barcode = {"barcode": None, "currentUserPrivateBarcode": food["barcode"]} if food["own"] else {"barcode": food["barcode"]}
        return {
            "id": food["id"],
            "name": food["name"],
            "brand": food["brand"],
            "foodType": food["type"],
            "measures": measures,
            **barcode,
            "weight": food["base"],
            **food["nutrition"],
            "saturatedFat": food["saturatedFat"],
            "liquid": food["liquid"],
            **own,
            "deleted": food["deleted"],
            **(self._recipe_fields(food) if food["type"] == "RECIPE" else {}),
        }

    def _recipe_fields(self, recipe: dict[str, Any]) -> dict[str, Any]:
        items = []
        for product_id, measure_id, quantity in recipe["items"]:
            product = self.foods["PRODUCT", product_id]
            measure = self._measure(product, measure_id) or {"name": None, "weight": 0}
            items.append(
                {
                    "itemId": product_id,
                    "productId": product_id,
                    "type": "PRODUCT",
                    "name": product["name"],
                    "measureId": measure_id,
                    "measureName": measure["name"],
                    "measureWeight": f"{measure['weight']:.2f}",  # strings, as the API sends them
                    "measureQuantity": f"{quantity:.2f}",
                }
            )
        return {
            "serving": recipe["serving"],
            "items": items,
            "recipeDescription": recipe["description"],
            "mealSchema": recipe["meals"],
            "cookingTime": recipe["cooking_time"],
            "tags": [{"name": f"tag.{tag}"} for tag in recipe["tags"]] or None,
            "premiumInRecipesCatalog": False,
        }

    # -- requests --------------------------------------------------------------------
    def request(self, method: str, path: str, params: dict[str, Any] | None = None, json_data: Any = None) -> Any:
        params = params or {}
        for route_method, pattern, handler in _ROUTES:
            match = re.fullmatch(pattern, path)
            if route_method == method and match:
                return getattr(self, handler)(params=params, body=json_data, **match.groupdict())
        raise FitatuApiError(f"{method} {path} -> the demo does not serve this route")

    def _rows(self, day: str) -> dict[str, tuple[str, dict[str, Any]]]:
        return self.days.setdefault(day, {})

    def _store_activity(self, day: str, activity: dict[str, Any]) -> None:
        name, _, description = _DATA["activities"].get(str(activity["activityId"]), ["Activity", 0, None])
        measure = activity.get("simpleMeasure") or {}
        self.activities.setdefault(day, {})[activity["uuid"]] = {
            "uuid": activity["uuid"],
            "planDayActivityType": activity.get("planDayActivityType", 1),
            "activityId": activity["activityId"],
            "activityName": name,
            "activityDescription": description,
            "energy": activity["energy"],
            "simpleMeasures": [{"measureId": measure.get("measureId"), "value": measure.get("value")}],
            "deletedAt": None,
        }

    def day(self, *, day: str, **_: Any) -> dict[str, Any]:
        meals = {key: {"mealName": name, "mealTime": time, "recommendedPercent": percent, "items": []} for key, (name, time, percent) in _DATA["meals"].items()}
        for meal, row in self._rows(day).values():
            meals[meal]["items"].append(dict(row))
        return {
            "dietPlan": meals,
            "activityPlan": [dict(activity) for activity in self.activities.get(day, {}).values()],
            "note": None,
            "tagsIds": [],
            **self.notes.get(day, {}),
            "water": {"waterConsumption": self.water.get(day, 0)},
        }

    def sync(self, *, body: dict[str, Any], **_: Any) -> list[dict[str, Any]]:
        results = []
        for day, payload in body.items():
            rows = [(meal, row) for meal, bucket in (payload.get("dietPlan") or {}).items() for row in bucket.get("items", [])]
            error = next((reason for _meal, row in rows if not row.get("deletedAt") and (reason := self._rejection(row))), None)
            if error:  # the real sync answers 202 and rejects the whole day
                results.append({"date": day, "revision": None, "errorMessage": error})
                continue
            for meal, row in rows:
                if row.get("deletedAt"):
                    self._rows(day).pop(str(row["planDayDietItemId"]), None)
                else:
                    self._rows(day)[str(row["planDayDietItemId"])] = (meal, self._filled(row))
            self.notes.setdefault(day, {}).update({key: payload[key] for key in ("note", "tagsIds") if key in payload})
            for activity in payload.get("activities") or []:
                if activity.get("deletedAt"):
                    self.activities.get(day, {}).pop(activity["uuid"], None)
                else:
                    self._store_activity(day, activity)
            results.append({"date": day, "revision": _new_row_id(), "errorMessage": None})
        return results

    def search(self, *, params: dict[str, Any], **_: Any) -> list[dict[str, Any]]:
        phrase = str(params.get("phrase", ""))
        foods = [food for food in self.foods.values() if not food["deleted"]]
        hits = [food for food in foods if food["barcode"] == phrase] or [food for food in foods if _matches(phrase, food["name"])]
        return [self._hit(food) for food in hits][: int(params.get("limit", 20))]

    def search_recipes(self, *, params: dict[str, Any], **_: Any) -> list[dict[str, Any]]:
        def fits(recipe: dict[str, Any]) -> bool:  # per portion, as a recipe's nutrition is
            nutrition, inf = recipe["nutrition"], float("inf")
            return (
                nutrition["energy"] <= float(params.get("maxEnergy", inf))
                and nutrition["protein"] >= float(params.get("minProtein", 0))
                and nutrition["carbohydrate"] <= float(params.get("maxCarbohydrate", inf))
                and (recipe["cooking_time"] or 0) <= float(params.get("maxCookingTime", inf))  # the tool sends only 10 or 30
                and {f"tag.{tag}" for tag in recipe["tags"]} >= set(params.get("tags[]", []))
                and (not params.get("meals[]") or bool(set(recipe["meals"]) & set(params["meals[]"])))
            )

        recipes = [food for food in self.foods.values() if food["type"] == "RECIPE" and not food["own"] and not food["deleted"]]
        hits = [food for food in recipes if _matches(str(params.get("phrase") or ""), food["name"]) and fits(food)]
        extra = {"premiumInRecipesCatalog": False, "rating": None, "preparationTime": None}
        return [{**self._hit(food), **extra, "cookingTime": food["cooking_time"]} for food in hits][: int(params.get("limit", 20))]

    def food(self, *, kind: str, food_id: str, **_: Any) -> dict[str, Any]:
        food = self.foods.get((kind, int(food_id)))
        if food is None:
            raise FitatuNotFoundError(f"GET /{kind.lower()}s/{food_id} -> 404", status_code=404)
        return self._details(food)

    def product(self, *, food_id: str, **_: Any) -> dict[str, Any]:
        return self.food(kind="PRODUCT", food_id=food_id)

    def recipe(self, *, food_id: str, **_: Any) -> dict[str, Any]:
        return self.food(kind="RECIPE", food_id=food_id)

    def create_recipe(self, *, body: dict[str, Any], **_: Any) -> dict[str, Any]:
        return self._save_recipe(max(food_id for _, food_id in self.foods) + 1, body)

    def update_recipe(self, *, food_id: str, body: dict[str, Any], **_: Any) -> dict[str, Any]:
        return self._save_recipe(int(food_id), body)

    def _save_recipe(self, recipe_id: int, body: dict[str, Any]) -> dict[str, Any]:
        servings = max(1.0, float(body.get("serving") or 1))
        totals = dict.fromkeys(MACRO_FIELDS, 0.0)
        weight = 0.0
        for item in body["items"]:
            row = self._filled({"foodType": "PRODUCT", "productId": item["itemId"], "measureId": item["measureId"], "measureQuantity": item["measureQuantity"]})
            weight += row["weight"]
            totals = {field: totals[field] + row[field] for field in MACRO_FIELDS}
        per_serving = [round(totals[field] / servings, 2) for field in MACRO_FIELDS]
        items = [[int(item["itemId"]), int(item["measureId"]), float(item["measureQuantity"])] for item in body["items"]]
        recipe = {"name": body["name"], "weight": round(weight / servings, 2), "nutrition": per_serving, "own": True}
        self._add_recipe(recipe_id, {**recipe, "items": items, "serving": servings, "description": body.get("recipeDescription")})
        return {"id": recipe_id, "name": body["name"]}

    def delete_recipe(self, *, food_id: str, **_: Any) -> list[Any]:
        self.foods["RECIPE", int(food_id)]["deleted"] = True  # soft, as Fitatu does
        return []

    def create_product(self, *, body: dict[str, Any], **_: Any) -> dict[str, Any]:
        return self._save_product(max(food_id for _, food_id in self.foods) + 1, body)

    def update_product(self, *, food_id: str, body: dict[str, Any], **_: Any) -> dict[str, Any]:
        return {**self._save_product(int(food_id), body), "newProductCreated": False}  # the demo changes it in place

    def _save_product(self, product_id: int, body: dict[str, Any]) -> dict[str, Any]:
        # shortcut: the values are taken per 100 g or ml, as the tools send them (base measure first); Fitatu takes
        # them per the first measure, whatever it is.
        base, *servings = body.get("measures") or [{"measureUnit": "g"}]
        product = {
            "name": body["name"],
            "brand": body.get("brand"),
            "nutrition": [body.get(field) or 0 for field in MACRO_FIELDS],
            "measures": [[*_MEASURE_KEYS[measure["measureKey"]], measure["weight"]] for measure in servings],
            "liquid": base["measureUnit"] == "ml",
            "saturatedFat": body.get("saturatedFat"),
            "barcode": body.get("barcode"),
            "own": True,
        }
        self._add_product(product_id, product)
        return {"id": product_id, "name": body["name"]}

    def delete_product(self, *, food_id: str, **_: Any) -> dict[str, Any]:
        self.foods["PRODUCT", int(food_id)]["deleted"] = True  # soft, as Fitatu does
        return {"deleted": True}

    def my_foods(self, *, params: dict[str, Any], **_: Any) -> list[dict[str, Any]]:
        phrase = str(params.get("phrase") or "")
        own = [food for food in self.foods.values() if food["own"] and not food["deleted"] and _matches(phrase, food["name"])]
        return [self._hit(food) for food in own][: int(params.get("limit", 50))]

    def recent(self, *, params: dict[str, Any], **_: Any) -> list[dict[str, Any]]:
        uses: dict[tuple[str, int], int] = {}
        for rows in self.days.values():
            for meal, row in rows.values():
                if meal == params.get("meal") and row["foodType"] in ("PRODUCT", "RECIPE"):
                    key = (row["foodType"], int(row.get("productId") or row.get("recipeId") or 0))
                    uses[key] = uses.get(key, 0) + 1
        return [self._hit(self.foods[key], count) for key, count in sorted(uses.items(), key=lambda item: -item[1])]

    def favorite_foods(self, **_: Any) -> list[dict[str, Any]]:
        return [self._hit(self.foods[kind, food_id]) for kind, food_id in sorted(self.favorites)]

    def set_favorite(self, *, kind: str, food_id: str, body: dict[str, Any], **_: Any) -> None:
        if body.get("markAsFavorite"):
            self.favorites.add((kind, int(food_id)))
        else:
            self.favorites.discard((kind, int(food_id)))

    def settings(self, **_: Any) -> dict[str, Any]:
        targets = self.targets
        diet = {"energy": targets["energy"]}
        for macro, kcal in (("protein", 4), ("fat", 9), ("carbohydrate", 4)):
            grams = sum(targets[macro]) / 2
            diet |= {f"{macro}Weight": grams, f"{macro}Percentage": round(grams * kcal * 100 / targets["energy"])}
        profile = _DATA["profile"]
        diet |= {"weightChangeDirection": profile["direction"], "weightChangeSpeedKg": profile["pace_kg"]}
        diet |= {"manualEnergyTarget": self.manual is not None, **(self.manual or {})}
        since = (self.today - dt.timedelta(days=30)).isoformat()
        # The profile's current weight is the newest weigh-in up to today, so a log_weight for today shows, as live.
        today = self.today.isoformat()
        weight = next((values["weight"] for day, values in sorted(self.measurements.items(), reverse=True) if day <= today and values.get("weight")), None)
        return {
            "date": f"{since}T00:00:00+00:00",
            "userSettings": {
                "sex": profile["sex"],
                "age": profile["age"],
                "heightCm": profile["height_cm"],
                "weightStartKg": profile["start_kg"],
                "weightTargetKg": profile["target_kg"],
                "weightCurrentKg": weight,
            },
            "userDietSettings": diet,
            "calculatedValues": {"BMR": targets["bmr"], "TMR": targets["tmr"], "PAL": round(targets["tmr"] / targets["bmr"], 2)},
            "waterSettings": {"unitCapacity": self.glass_ml},
        }

    def set_settings(self, *, body: dict[str, Any], **_: Any) -> dict[str, Any]:
        # shortcut: a change applies to every day, past ones too (Fitatu keeps earlier days' goals); version the goals by day if a demo task needs that.
        diet = body.get("userDietSettings")
        if diet is not None:
            keys = ("energy", *(f"{macro}{part}" for macro in ("protein", "fat", "carbohydrate") for part in ("Percentage", "Weight")))
            self.manual = {key: diet[key] for key in keys} if diet.get("manualEnergyTarget") else None
            self.targets = dict(_DATA["targets"])
            if self.manual:  # a manual split's macro goals are one number each, as on a free account (checked live)
                self.targets |= {"energy": diet["energy"], **{m: [diet[f"{m}Weight"]] * 2 for m in ("protein", "fat", "carbohydrate")}}
        if body.get("waterSettings"):
            self.glass_ml = int(body["waterSettings"]["unitCapacity"])
        return self.settings()

    def _range(self, params: dict[str, Any]) -> list[str]:
        start, end = dt.date.fromisoformat(params["fromDate"]), dt.date.fromisoformat(params["toDate"])
        return [(start + dt.timedelta(days=offset)).isoformat() for offset in range((end - start).days + 1)]

    def _logged(self, day: str, field: str, *, eaten_only: bool = False) -> float | None:
        rows = [row for _, row in self._rows(day).values() if row["eaten"] or not eaten_only]
        return round(sum(float(row.get(field) or 0) for row in rows), 2) if rows else None

    def by_day(self, *, params: dict[str, Any], nutrient: str, **_: Any) -> dict[str, Any]:
        days = self._range(params)
        reply: dict[str, Any] = {"measures": {day: self._logged(day, nutrient) for day in days}}
        if nutrient == "energy":
            reply["targets"] = dict.fromkeys(days, self.targets["energy"])
        else:
            low, high = self.targets[nutrient]
            reply["targetRanges"] = dict.fromkeys(days, {"min": low, "max": high})
        return reply

    def summary(self, *, params: dict[str, Any], **_: Any) -> dict[str, Any]:
        days = self._range(params)
        reply: dict[str, Any] = {}
        for field in MACRO_FIELDS:
            logged = [value for day in days if (value := self._logged(day, field)) is not None]
            eaten = [value for day in days if (value := self._logged(day, field, eaten_only=True)) is not None]
            goal = self.targets.get(field)
            low, high = (None, goal) if field == "energy" else (goal or [None, None])
            reply[field] = {
                "current": round(sum(logged), 2) if logged else None,
                "eaten": round(sum(eaten), 2) if eaten else None,
                "min": None if low is None else low * len(days),
                "max": None if high is None else high * len(days),
            }
        return reply

    def activity_summary(self, *, params: dict[str, Any], **_: Any) -> dict[str, Any]:
        return {"energy": sum(activity["energy"] for day in self._range(params) for activity in self.activities.get(day, {}).values())}

    def top_foods(self, *, params: dict[str, Any], nutrient: str, **_: Any) -> dict[str, Any]:
        amounts: dict[str, float] = {}
        per100: dict[str, float | None] = {}
        for day in self._range(params):
            for _meal, row in self._rows(day).values():
                amounts[row["name"]] = amounts.get(row["name"], 0) + float(row.get(nutrient) or 0)
                food = self.foods.get((row["foodType"], int(row.get("productId") or row.get("recipeId") or 0)))
                per100[row["name"]] = None if food is None else food["nutrition"][nutrient] * 100 / food["base"]
        ranked = sorted(((name, amount) for name, amount in amounts.items() if amount > 0), key=lambda item: -item[1])
        listed = [
            {"id": index, "name": name, "amount": {"current": f"{amount:.2f}", "per100": None if per100[name] is None else f"{per100[name]:.2f}"}}
            for index, (name, amount) in enumerate(ranked[: int(params.get("limit", 20))], start=1)
        ]
        return {"list": listed, "count": len(listed)}

    def weights(self, *, params: dict[str, Any], **_: Any) -> list[dict[str, Any]]:
        dated = sorted((day, values["weight"]) for day, values in self.measurements.items() if values.get("weight"))
        entries = [
            {"date": day, "value": value, "difference": None if index == 0 else round(value - dated[index - 1][1], 2), "direction": -1}
            for index, (day, value) in enumerate(dated)
            if params.get("fromDate", day) <= day <= params.get("toDate", day)
        ][::-1]
        page, limit = int(params.get("page", 1)), int(params.get("limit", 41))
        return entries[(page - 1) * limit : page * limit]

    def size_history(self, *, part: str, params: dict[str, Any], **_: Any) -> list[dict[str, Any]]:
        end = params.get("toDate", self.today.isoformat())  # without toDate Fitatu stops at today (checked live)
        entries = [
            {"date": day, "value": values[part]}
            for day, values in sorted(self.measurements.items(), reverse=True)
            if values.get(part) is not None and params.get("fromDate", day) <= day <= end
        ]
        page, limit = int(params.get("page", 1)), int(params.get("limit", 41))
        return entries[(page - 1) * limit : page * limit]

    def sizes(self, *, params: dict[str, Any], **_: Any) -> dict[str, Any]:
        days = [day for day in sorted(self.measurements) if params.get("fromDate", day) <= day <= params.get("toDate", day)]
        reply = {}
        for part in _SIZES:
            values = [self.measurements[day][part] for day in days if self.measurements[day].get(part) is not None]
            first, last = (values[0], values[-1]) if values else (None, None)
            reply[part] = {"startValue": first, "endValue": last, "difference": None if first is None else round(last - first, 2)}
        return reply

    def measurement(self, *, day: str, **_: Any) -> dict[str, Any]:
        if day not in self.measurements:
            raise FitatuNotFoundError(f"GET /users/{self.user_id}/measurements/{day} -> 404", status_code=404)
        return {"weight": None, **dict.fromkeys(_SIZES), **self.measurements[day], "weightUnit": "KG", "sizeUnit": "CM"}

    def set_measurement(self, *, day: str, body: dict[str, Any], **_: Any) -> dict[str, Any]:
        # The app sends numbers as strings, and a fat percentage of 0.0 when it has none.
        values = {key: float(value) for key, value in body.items() if key in ("weight", *_SIZES) and float(value)}
        self.measurements[day] = {**self.measurements.get(day, {}), **values}
        return self.measurement(day=day)

    def profile(self, **_: Any) -> dict[str, Any]:
        return {"id": self.user_id, "timezone": "Europe/Warsaw", "weightUnit": "KG", "sizeUnit": "CM"}

    def subscriptions(self, **_: Any) -> dict[str, Any]:
        # The demo serves the Premium-only routes (get_top_foods, the macros day by day), so it has a plan.
        return {"subscriptions": [{"startDate": self._day("-30"), "endDate": self._day("335")}]}

    def get_water(self, *, day: str, **_: Any) -> dict[str, Any]:
        water = _DATA["water"]
        return {"water": {"waterConsumption": self.water.get(day, 0), "target": water["target_ml"]}, "settings": {"waterUnitCapacity": self.glass_ml}}

    def set_water(self, *, day: str, body: dict[str, Any], **_: Any) -> None:
        self.water[day] = int(body["waterConsumption"])

    def fasting_summary(self, **_: Any) -> list[Any]:
        return []  # the demo user does not fast, as the live API answers then

    def habits(self, **_: Any) -> list[dict[str, Any]]:
        return [{"id": key, "name": name, "translationKey": f"habit_{key}", "length": length, "premium": premium} for key, name, length, premium in _DATA["habits"]]

    def user_habits(self, **_: Any) -> list[dict[str, Any]]:
        catalog = {habit["id"]: habit for habit in self.habits()}
        return [
            {
                "completed": False,
                "enrolledDate": self._day(enrolled),
                "habit": catalog[int(key)],
                "habitItems": [{"dayNumber": number, "date": self._day(offset)} for number, offset in enumerate(checked, start=1)],
            }
            for key, (enrolled, *checked) in _DATA["user_habits"].items()
        ]

    def good_days(self, **_: Any) -> list[str]:
        return [self._day(offset) for offset in _DATA["good_days"]]

    def search_activity(self, *, phrase: str, params: dict[str, Any], **_: Any) -> list[dict[str, Any]]:
        hits = [
            {"activityId": int(key), "name": name, "energy": energy, "measuresDescription": effort}
            for key, (name, energy, effort) in _DATA["activities"].items()
            if _matches(unquote(phrase), name)
        ]
        return hits[: int(params.get("limit", 20))]


_U, _D = r"(?:\d+)", r"(?P<day>\d{4}-\d{2}-\d{2})"
_ROUTES = [
    ("GET", rf"/diet-and-activity-plan/{_U}/day/{_D}", "day"),
    ("POST", rf"/diet-plan/{_U}/days", "sync"),
    ("GET", r"/search/new/food", "search"),
    ("GET", r"/search/catalog/recipes", "search_recipes"),
    ("GET", r"/products/(?P<food_id>\d+)", "product"),
    ("POST", r"/products", "create_product"),
    ("PUT", r"/products/(?P<food_id>\d+)", "update_product"),
    ("DELETE", r"/products/(?P<food_id>\d+)", "delete_product"),
    ("GET", r"/recipes/(?P<food_id>\d+)", "recipe"),
    ("POST", r"/recipes", "create_recipe"),
    ("PUT", r"/recipes/(?P<food_id>\d+)", "update_recipe"),
    ("DELETE", r"/recipes/(?P<food_id>\d+)", "delete_recipe"),
    ("GET", rf"/search/food/user/{_U}", "my_foods"),
    ("GET", rf"/search/food/{_U}/last-used", "recent"),
    ("GET", rf"/users/{_U}/favorite/foods", "favorite_foods"),
    ("PUT", rf"/users/{_U}/favorite/food/(?P<kind>PRODUCT|RECIPE)/(?P<food_id>\d+)", "set_favorite"),
    ("GET", rf"/users/{_U}/settings-new/{_D}", "settings"),
    ("PATCH", rf"/users/{_U}/settings-new", "set_settings"),
    ("GET", rf"/v2/diet-plan/{_U}/summary/custom", "summary"),
    ("GET", rf"/v2/diet-plan/{_U}/summary/(?P<nutrient>\w+)/custom", "by_day"),
    ("GET", rf"/activity-plan/{_U}/summary/custom", "activity_summary"),
    ("GET", rf"/diet-plan/{_U}/summary-product/(?P<nutrient>\w+)/custom", "top_foods"),
    ("GET", rf"/users/{_U}/measurements/summary/weight", "weights"),
    ("GET", rf"/users/{_U}/measurements/summary/size", "sizes"),
    ("GET", rf"/users/{_U}/measurements/size/(?P<part>\w+)", "size_history"),
    ("GET", rf"/users/{_U}/measurements/{_D}", "measurement"),
    ("PUT", rf"/users/{_U}/measurements/{_D}", "set_measurement"),
    ("GET", rf"/users/{_U}", "profile"),
    ("GET", rf"/users/{_U}/subscriptions", "subscriptions"),
    ("GET", rf"/water/{_U}/{_D}", "get_water"),
    ("PUT", rf"/water/{_U}/{_D}", "set_water"),
    ("GET", r"/search/activity/(?P<phrase>[^/]+)", "search_activity"),
    ("GET", rf"/intermittent-fasting/{_U}/summary", "fasting_summary"),
    ("GET", rf"/habits/{_U}", "habits"),
    ("GET", rf"/users/{_U}/habits", "user_habits"),
    ("GET", rf"/habits/{_U}/good-days", "good_days"),
]


class DemoClient(FitatuApiClient):
    """A :class:`FitatuApiClient` over a fresh :class:`DemoBackend`: nothing is sent anywhere."""

    def __init__(self) -> None:
        self.backend = DemoBackend()
        super().__init__(FitatuAuthContext(bearer_token="demo", fitatu_user_id=self.backend.user_id), persist_tokens=False)

    def request(self, method: str, path: str, *, params: dict[str, Any] | None = None, json_data: Any | None = None, **_: Any) -> Any:
        return self.backend.request(method.upper(), path, params, json_data)
