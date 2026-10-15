"""Copy a catering menu into the Fitatu diary; running it again never logs a dish twice.

The menu is a JSON list or a CSV file with one dish per entry: date (YYYY-MM-DD), meal (a meal
key such as dinner, or the meal's name in your Fitatu, such as Obiad), name, and either
energy_kcal (optionally protein_g, fat_g, carbohydrate_g; all for the whole portion) to log it
as a custom row, or grams with a barcode or a search phrase to log a catalog product: the
barcode's product, else the first product the search finds. A search nearly always finds one, even
for a typo, so check the product names --dry-run prints. When no product is found (a barcode not in
the catalog and no search), energy_kcal logs the dish as a custom row instead. Other keys are ignored.

Each dish gets a row id made from its date, its name and how many dishes of that name come
before it that day, so a second run finds the first run's rows and skips them. A row already in
Fitatu is never changed: to correct one, delete it in the app and run again. Dishes are logged
as planned, not eaten.

    python catering_sync.py menu.csv --dry-run   # what would be logged; writes nothing
    python catering_sync.py menu.csv

The session comes from example.init_api(), as in demo.py.
"""

from __future__ import annotations

import argparse
import csv
import io
import json
import uuid
from collections import Counter
from datetime import date
from pathlib import Path
from typing import Any

from example import init_api
from fitatu_api import (
    FitatuApiError,
    FitatuAuthError,
    FitatuNotFoundError,
    FitatuPlannerSyncError,
    FitatuTools,
    FitatuValidationError,
)

# Never change these: the row ids follow from them, and new ids would log every dish again.
NAMESPACE = uuid.uuid5(uuid.NAMESPACE_URL, "https://github.com/Jezue/fitatu_library/catering_sync.py")
TEXTS = ("date", "meal", "name", "barcode", "search")
NUMBERS = ("energy_kcal", "protein_g", "fat_g", "carbohydrate_g", "grams")


def row_id(day: str, name: str, occurrence: int) -> str:
    """The dish's row id: a UUID v1 (the only version Fitatu takes) made from a hash, not the clock."""
    digest = uuid.uuid5(NAMESPACE, f"{day}|{name}|{occurrence}")
    return str(uuid.UUID(int=digest.int | 1 << 40, version=1))  # the multicast bit: a node that is no MAC address


def _text(entry: dict[str, Any], key: str) -> str:
    value = entry.get(key)
    return "" if value is None else str(value).strip()


def read_menu(path: Path) -> list[dict[str, Any]]:
    """The menu's dishes, checked, each with its row_id; ValueError names every bad entry."""
    text = path.read_text(encoding="utf-8-sig")
    if path.suffix.lower() == ".csv":
        # A spreadsheet set to Polish saves with semicolons and decimal commas.
        raw: Any = list(csv.DictReader(io.StringIO(text), delimiter=";" if ";" in text.partition("\n")[0] else ","))
    else:
        raw = json.loads(text)
    if not isinstance(raw, list):
        raise ValueError("the menu must be a list of dishes")
    dishes, problems, seen = [], [], Counter[tuple[str, str]]()
    for number, entry in enumerate(raw, 1):
        try:
            dish: dict[str, Any] = {key: _text(entry, key) for key in TEXTS}
            if None in entry:  # csv.DictReader keeps the values past the last column under None
                raise ValueError("more values than columns (a decimal comma in a comma-separated file? quote it or use ;)")
            dish["date"] = date.fromisoformat(dish["date"]).isoformat()
            for key in NUMBERS:
                if _text(entry, key):
                    dish[key] = float(_text(entry, key).replace(",", "."))
                    if not 0 <= dish[key] < float("inf"):  # NaN too
                        raise ValueError(f"{key} must be a number, 0 or more")
            code = dish["barcode"]
            if code and not (code.isascii() and code.isdigit() and 8 <= len(code) <= 14):  # find_by_barcode's rule
                raise ValueError("barcode must be the 8 to 14 digits under the bars")
            if not dish["meal"] or not dish["name"]:
                raise ValueError("meal and name are required")
            if "energy_kcal" not in dish and not (dish.get("grams") and (dish["barcode"] or dish["search"])):
                raise ValueError("give energy_kcal, or grams with a barcode or search")
        except (AttributeError, ValueError) as exc:  # AttributeError: an entry that is not an object
            problems.append(f"dish {number}: {exc}")
            continue
        same = (dish["date"], dish["name"])
        dish["row_id"] = row_id(*same, seen[same])
        seen[same] += 1
        dishes.append(dish)
    if problems:
        raise ValueError("; ".join(problems))
    return dishes


def find_product(tools: FitatuTools, dish: dict[str, Any]) -> dict[str, Any] | None:
    """The catalog product of a dish with grams: by its barcode, else the first product the search finds."""
    if not dish.get("grams"):
        return None
    if dish["barcode"]:
        try:
            return dict(tools.find_by_barcode(dish["barcode"]))
        except FitatuNotFoundError:
            pass
    hits = tools.search_food(dish["search"], limit=5) if dish["search"] else []
    return next((dict(hit) for hit in hits if hit["type"] == "PRODUCT"), None)


def sync(tools: FitatuTools, dishes: list[dict[str, Any]], *, dry_run: bool) -> int:
    """Log the dishes whose row is not in Fitatu yet, printing a line per dish; returns how many failed."""
    # shortcut: a dish deleted in the app is logged again by a later run, as the day hides deleted rows;
    # keep a file of the synced row ids if that matters.
    failed = 0
    for day in sorted({dish["date"] for dish in dishes}):
        meals = tools.get_day(day)["meals"]
        logged = {item["row_id"] for meal in meals for item in meal["items"]}
        keys = {name.casefold(): meal["meal_key"] for meal in meals for name in (meal["meal_key"], meal["meal_name"])}
        for dish in (dish for dish in dishes if dish["date"] == day):
            label = f"{day} {dish['meal']}: {dish['name']}"
            if dish["row_id"] in logged:
                print(f"{label}: already in Fitatu")
                continue
            try:
                meal = keys.get(dish["meal"].casefold())
                if meal is None:
                    listed = ", ".join(f"{m['meal_key']} ({m['meal_name']})" for m in meals)
                    raise FitatuValidationError(f"the day has no meal {dish['meal']!r}; its meals: {listed}")
                product = find_product(tools, dish)
                if product is None and "energy_kcal" not in dish:
                    raise FitatuNotFoundError("no catalog product found, and no energy_kcal to log it as a custom row")
                what = f"{product['name']}, {dish['grams']:g} g" if product else f"custom, {dish['energy_kcal']:g} kcal"
                if not dry_run:
                    where = {"date": day, "meal": meal, "row_id": dish["row_id"]}
                    if product:
                        tools.add_food(**where, food_id=product["food_id"], food_type="PRODUCT", quantity=dish["grams"], unit="g")
                    else:
                        macros = {key: dish[key] for key in ("protein_g", "fat_g", "carbohydrate_g") if key in dish}
                        tools.add_custom_item(**where, name=dish["name"], energy_kcal=dish["energy_kcal"], **macros)
                print(f"{label}: {'would add' if dry_run else 'added'} ({what})")
            except (FitatuValidationError, FitatuNotFoundError, FitatuPlannerSyncError) as exc:  # this dish's fault: go on with the next
                failed += 1
                print(f"{label}: not added: {exc}")
    return failed


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Copy a catering menu into the Fitatu diary, never twice.")
    parser.add_argument("menu", type=Path, help="the menu: a .json list or a .csv file")
    parser.add_argument("--dry-run", action="store_true", help="show what would be logged and write nothing")
    args = parser.parse_args(argv)
    try:
        dishes = read_menu(args.menu)
    except UnicodeDecodeError:
        print(f"Cannot read {args.menu}: save it as UTF-8 (in Excel: CSV UTF-8)")
        return 2
    except (OSError, ValueError) as exc:  # json.JSONDecodeError is a ValueError
        print(f"Cannot read {args.menu}: {exc}")
        return 2
    try:
        with init_api() as client:
            failed = sync(FitatuTools(client), dishes, dry_run=args.dry_run)
    except FitatuAuthError as exc:
        print(f"The Fitatu session stopped working: {exc}. Run `fitatu-api login`, then this again: dishes already logged are skipped.")
        return 1
    except FitatuApiError as exc:  # a rate limit, maintenance, a lost connection, a server error
        print(f"Fitatu is not available right now: {exc}. Run it again later: dishes already logged are skipped.")
        return 1
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
