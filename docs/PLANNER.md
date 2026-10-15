# Planner: the food diary

`client.planner` (`PlannerModule`) reads and writes diary days: food rows per meal and logged
activities. This page describes what the server does with a write, which id is which, and what
each write returns. The agent tools in [AGENTS.md](AGENTS.md) are built on these calls.

## The day contract

One endpoint writes everything: `POST /diet-plan/{userId}/days?synchronous=true`. Its body maps
dates (`YYYY-MM-DD`) to day payloads. Everything below was recorded against the live API and is
replayed by the offline tests (`tests/fixtures/api_recording.json`, see
[tests/fixtures/README.md](../tests/fixtures/README.md)):

- Rows are upserted by `planDayDietItemId`, which must be a UUID version 1. A new row with any
  other id makes the server reject the whole day.
- Rows that are not in the payload are kept. Leaving a row out (or sending a meal with
  `"items": []`) never deletes anything, so a write only needs the rows it changes. A payload
  with only `activities` keeps the day's food rows too.
- A row is replaced as a whole. Fields you do not send fall back to the server's defaults: a
  row re-sent without `eaten` comes back with `eaten: false`.
- A row sent with `deletedAt` (`"YYYY-MM-DD HH:MM:SS"`) disappears from the day GET. There is no
  DELETE route for diary rows.
- A row id belongs to one day: sent again into another meal it moves the row there, and the
  same id on another day is another row. Sending the id of a deleted row brings the row back.
  Any UUID of version 1 is taken, also one made from a hash with no real time in it, and a meal
  lists its rows in the order they were added. An id without dashes is not refused but saved
  under a new id the server makes, so the library always sends the dashed lower-case form
  (checked live 2026-10-15, not recorded).
- Product and recipe rows need only `productId` / `recipeId` plus the measure; the server fills
  in name and nutrition.
- The answer is HTTP 202 with `[{"date", "revision", "errorMessage"}]`, one entry per day. A
  non-null `errorMessage` means nothing was saved for that day. `sync_days()` raises
  `FitatuPlannerSyncError` for it, with the parsed list in `exc.results`.
- With `synchronous=true` (the library always sends it) the next day GET already shows the
  write, so there is no need to wait between an add and an update.
- Activities travel in the same sync under `activities`, in a write shape: `uuid`,
  `planDayActivityType` (1 for a catalog activity), `activityId`, `energy` (kcal, stored as
  sent), `simpleMeasure` (`{"measureId": 1, "value": minutes}`), `advancedMeasures`,
  `deletedAt`. The day GET lists them under `activityPlan` in a longer read shape; sending that
  read shape back is rejected field by field (202 with `errorMessage`). An activity is deleted by
  sending it in the write shape with `deletedAt`.
- The day's note travels in the same sync as `note` (text, null to delete it) with `tagsIds`,
  the ids of its tags. A payload without them, such as one with only `dietPlan` or only
  `activities`, or a row deletion, keeps the note, its tags and the day's water (checked live
  2026-10-14, not recorded).

What the library does with that:

- an add sends only the new row; an update re-sends the changed row in full, so the fields you
  do not touch (including `eaten`) keep their values;
- a removal re-sends the row with `deletedAt`;
- updates, moves, copies, replacements, removals and activity writes read the day again
  afterwards and report the outcome in `ok`;
- `sync_days()` is marked safe to repeat (it upserts by id), so the client retries it on a
  network error, 429 or 5xx like a read.

## Ids: `item_id` and `row_id`

| Name | Where | Meaning |
|---|---|---|
| `planDayDietItemId` | raw day payload | The id of a food row. |
| `uuid` | raw `activityPlan` entry | The id of a logged activity. |
| `item_id` | argument of update, move, replace, remove | A row that is already on the day: its `planDayDietItemId` (an activity's `uuid` for `remove_activity`). |
| `row_id` | optional argument of every add and of `copy_meal` | The id to give a new row (for `add_foods_to_day_meal` and a copy, the id the rows' ids are derived from). Pass your own UUID v1 to make the call safe to repeat. |
| `row_id` | results of the adds, `move_day_item`, `replace_day_item_with_custom_item`, `add_activity` and `remove_activity` | The id of the row the call wrote (for a move or replace, the new row). |
| `row_ids` | results of `add_foods_to_day_meal` and `copy_meal` | The ids of the rows it wrote, derived from its `row_id`. |
| `item_id`, `removed_id` | results of `update_day_item` (`item_id`), `remove_day_item` and `rollback_added_item` (`removed_id`), `remove_day_items_by_kind` (`attempts[].item_id`, `targets[].item_id`) | The id of the row the call changed or deleted. These results have no `row_id`. |
| `row_id` | day summaries (`FitatuTools.get_day`, the deprecated `FitatuLibrary.get_day_summary_via_api`) | The row's `planDayDietItemId` (an activity's `uuid` under `activities`). |

A `productId` is accepted as `item_id` only when exactly one row of the given `meal_type` has
it. With the same product logged twice the call raises `FitatuValidationError` listing the
candidates, so pass the `planDayDietItemId`.

A `planDayDietItemId` is found in any meal; `meal_type` only has to be a meal the day has.

Every result and summary the library builds has snake_case keys. The Fitatu payloads inside them
(the rows sent or read, such as `added_item`, `before` or `removed`, and `sync_response`) are passed
through as the API sends them, with camelCase keys (`planDayDietItemId`, `measureQuantity`).

## Reading a day

```python
from datetime import date

from fitatu_api import FitatuApiClient

with FitatuApiClient.from_session() as client:
    user_id = client.auth.fitatu_user_id
    day = client.planner.get_day(user_id, date.today())  # or "YYYY-MM-DD"
    for meal_key, meal in day["dietPlan"].items():
        for row in meal["items"]:
            print(meal_key, row["planDayDietItemId"], row["name"], row["measureQuantity"], row["eaten"])
    for activity in day["activityPlan"]:
        print(activity["uuid"], activity["activityName"], activity["energy"])
```

Also: `get_meal()` and `list_meal_items()` (one meal), `find_meal_item(meal_type=...,
query=...)` (the first row of a meal whose name contains the text),
`list_day_items_for_removal()` (every row with its removal kind) and `get_day_revisions()`
(revision ids, newest first; cheap change detection).

## Adds

| Call | Adds |
|---|---|
| `add_product_to_day_meal(user_id, day, *, meal_type, product_id, measure_id, measure_quantity=1, eaten=False, source="API", row_id=None)` | A catalog product in one of its measures. |
| `add_product_to_day_meal_with_unit(..., product_id, amount, unit, strict_measure=True, ...)` | A product by unit (`g`, `ml`, `sztuka`, `porcja`, ...), converted to one of its measures. |
| `add_recipe_to_day_meal(..., recipe_id, food_type="RECIPE", measure_id=39, measure_quantity=1, ...)` | A recipe (39 is the portion measure). |
| `add_custom_item_to_day_meal(..., name, calories, protein_g, fat_g, carbs_g, eaten=False, source="API", row_id=None)` | A row with a name and the nutrition of the whole amount (kcal and grams). |
| `add_search_result_to_day_meal(..., phrase, index=0, measure_quantity=1, measure_amount=None, measure_unit=None, ...)` | Hit `index` of a food search: a PRODUCT hit by `productId`, a RECIPE hit by `recipeId`. Other hit types raise, and so does a hit that names no measure when no `measure_unit` is given (the sync would take any measure id). |
| `add_foods_to_day_meal(user_id, day, *, meal_type, items, eaten=False, source="API", row_id=None)` | Several products and recipes in one sync. Each item holds the arguments of the product add (`product_id`, `measure_id`, `measure_quantity`) or, with a `recipe_id`, of the recipe add; `eaten` applies to every row. |

Every add reads the day first (the meal must exist, else `FitatuNotFoundError`), sends one row
and returns:

```python
{
    "ok": True,                  # the sync accepted the row (a rejection raises)
    "row_id": "...",             # the new row's planDayDietItemId
    "meal": "breakfast",
    "product_id": 24881865,      # or "recipe_id" and "food_type", or "name" for a custom row
    "added_item": {...},         # the row as sent (raw, camelCase; no name or nutrition for catalog rows)
    "sync_response": [{"date": "...", "revision": "...", "errorMessage": None}],  # raw
}
```

The unit and search variants add `measure_resolution` (the measure chosen and how: `measure_id`,
`measure_name`, `measure_quantity`, `requested_amount`, `requested_unit`, `strategy`,
`strict_measure`, `warnings`), the search variant adds `search_item` (`name`, `type`, `food_id`,
`id`, `measure_id`). `add_foods_to_day_meal` returns `row_ids` and `added_items` (in the
order of `items`) in place of `row_id` and `added_item`. It checks every item, the meal and the ids
before anything is sent, and an item's error names its index (`items[1]: ...`). Its `row_id`
seeds the rows' ids as in `copy_meal` below, so a repeat with the same `row_id` and items writes
the same rows again.

### Making an add safe to repeat

After a timeout you cannot tell whether the add was saved. Pick the row id yourself and send
the same call again: the server upserts by id, so the second call updates the row instead of
adding another one.

```python
import secrets
import uuid

from fitatu_api import FitatuConnectionError

# A UUID v1 with a random node instead of this machine's MAC address.
row_id = str(uuid.uuid1(node=secrets.randbits(48) | 1 << 40))
add = dict(meal_type="snack", name="Kanapka z serem", calories=320, protein_g=14, fat_g=15, carbs_g=30, row_id=row_id)
try:
    added = client.planner.add_custom_item_to_day_meal(user_id, date.today(), **add)
except FitatuConnectionError:
    added = client.planner.add_custom_item_to_day_meal(user_id, date.today(), **add)
```

A reused `row_id` is only taken as a retry when the stored row logs exactly what the add would
send: the same meal and food type, and the same food, measure, quantity and `eaten` (a custom
row: the same name, nutrition and `eaten`). Anything else raises `FitatuValidationError` naming
the row instead of overwriting it. When that happens to a repeated add, the first attempt was
saved and the row has changed since (e.g. marked eaten on the phone), so do not add it again.
Never pass a row id read from the day to an add; to change a row, use `update_day_item()`.

`rollback_added_item(user_id, day, *, meal_type, item_id)` deletes the row an add returned
(`item_id=added["row_id"]`) and only that row; for a row that never landed it returns the
"not found" removal result below.

### Importing a catering menu

`catering_sync.py`, a script in the repository (not in the package), copies a catering diet's
menu into the diary and can be run again without logging a dish twice. The menu is a JSON list,
or a CSV file saved as UTF-8 (in Excel: CSV UTF-8; comma or semicolon separated, decimal point or
comma), with one dish per entry:

| Key | Meaning |
|---|---|
| `date` | The day the dish is eaten, `YYYY-MM-DD`. Required. |
| `meal` | A meal key (`breakfast`, `second_breakfast`, `lunch`, `dinner`, `snack`, `supper`) or the name of the meal in your Fitatu, in any case (`Obiad`). Required. |
| `name` | The dish as the provider names it. Required. |
| `energy_kcal`, `protein_g`, `fat_g`, `carbohydrate_g` | The whole portion, logged as a custom row. A macro left out counts as 0 g. |
| `grams` with `barcode` or `search` | Logs a catalog product: the one with that barcode, else the first product the search finds. A search nearly always finds one, even for a typo or a vague phrase, so check the product names `--dry-run` prints. With `energy_kcal` too, a dish whose barcode is not in the catalog and that has no `search` is logged as a custom row. |

A dish needs `energy_kcal`, or `grams` with a `barcode` or `search`. Other keys are ignored.
Every entry is checked before anything is read from Fitatu.

```bash
python catering_sync.py menu.json --dry-run   # what it would log; writes nothing
python catering_sync.py menu.json
```

Each dish's `row_id` is a UUID v1 made from a hash of its date, its name and how many dishes of
that name come before it that day. The script reads each day once and skips the dishes whose row
is already there, so a run that stopped half-way (a timeout, a rate limit) is finished by running
it again. It never changes or removes a row: one marked eaten on the phone stays so, a dish
renamed in a later menu is a new row next to the old one, and a dish deleted in the app comes
back when the same menu is run again (see the day contract). Dishes are logged as planned. A
dish that cannot be logged (a meal the day does not have, a product that is not found) is
reported, the others are logged, and the exit code is 1. Custom rows keep no grams: Fitatu
stores one as a portion.

#### Writing an adapter for a catering provider

An adapter turns the provider's export into this list; it does not talk to Fitatu. Providers
(Dietly, Kuchnia Vikinga, Maczfit) give each delivered dish's name and meal and its kcal,
protein, fat and carbohydrate for the whole portion, which make a custom row:

```python
import json

# A made-up export: [{"day": "2026-10-20", "dishes": [{"meal": "Obiad", "title": "...",
#                     "kcal": 650, "protein": 40, "fat": 20, "carbs": 70}]}]
with open("export.json", encoding="utf-8") as file:
    export = json.load(file)
menu = [
    {"date": day["day"], "meal": dish["meal"], "name": dish["title"], "energy_kcal": dish["kcal"],
     "protein_g": dish["protein"], "fat_g": dish["fat"], "carbohydrate_g": dish["carbs"]}
    for day in export
    for dish in day["dishes"]
]
with open("menu.json", "w", encoding="utf-8") as file:
    json.dump(menu, file, ensure_ascii=False, indent=1)
```

- Keep the dish name as the provider gives it: the row id follows from it, so a name that
  changes between runs logs the dish again.
- Pass the provider's meal names when your meals in Fitatu have the same names (`get_day` lists
  them as `meal_name`; an account checked on 2026-10-15 had Śniadanie, II śniadanie, Lunch,
  Obiad, Przekąska and Kolacja) and map the others to a meal key, such as `Podwieczorek` to
  `snack`. Providers do not agree on which slot their Obiad is, so let your own meal names decide.
- Leave out the dishes that are not delivered (paused or skipped days).
- Write barcodes as text, so a leading 0 stays, and as printed under the bars: a 13-digit code
  padded to 14 digits with a 0 is not found.

## Updates

`update_day_item(user_id, day, *, meal_type, item_id, measure_quantity=None, measure_id=None,
eaten=None, name=None, source=None, patch=None)` changes the given fields and re-sends the row
in full. `patch` sets other row fields in camelCase; it may not set `planDayDietItemId`,
`deletedAt`, `foodType`, `productId` or `recipeId` (a different food is a removal plus an add).
Quantities must be greater than 0.

The result has `ok` (the re-read row shows every change), `meal` (where the row is),
`requested_meal`, `item_id`, `product_id`, `before`, `after` (the raw row before and after),
`updates` (the row fields sent, by their API names, e.g. `measureQuantity`), `validation` (per
such field, whether the re-read matches) and `sync_response`. A row that is not on the day raises
`FitatuNotFoundError`; its message lists the day's rows.

## Moves, copies and replacements

`move_day_item(user_id, from_day, *, from_meal_type, item_id, to_day=None, to_meal_type=None)`
deletes the row and adds a copy with a new id, in one sync (two days in one payload when the
day changes). The target meal is checked on the target day before anything is sent. The result
has `ok` (the copy is there and the original is gone), `row_id` (the copy), `operation`,
`from_date`, `to_date`, `from_meal`, `to_meal`, `removed_id`, `added_item`, `delete_marker`,
`sync_payload`, `sync_response`.

`replace_day_item_with_custom_item(user_id, day, *, meal_type, item_id, name, calories,
protein_g, fat_g, carbs_g, source="API")` swaps a row for a custom row in one sync; the new row
keeps the old one's `eaten`. The result
has `ok`, `row_id` (the new row), `operation`, `date`, `meal`, `removed_id`, `replacement_item`,
`delete_marker`, `sync_payload`, `sync_response`.

`copy_meal(user_id, from_day, *, from_meal_type, to_day, to_meal_type=None, item_ids=None,
eaten=None, row_id=None)` copies the rows of a meal (or only those in `item_ids`) to another
meal and/or day in one sync; `to_meal_type` defaults to the source meal, and the target must
differ from the source. Each copy is rebuilt in the write shape of an add: a product or recipe
from its food, measure and quantity, a custom row from its name and nutrition. Rows of any
other type (e.g. `RECIPE_AI`) are not copied and come back in `skipped`. A copy keeps its row's
`eaten` unless `eaten` is given. `row_id` seeds the copies' ids: the first copy gets it, and each
next one differs from it only in the UUID's clock sequence, so a repeat with the same `row_id`
writes the same rows again. As for the adds, an id that another row of the target day already
has raises `FitatuValidationError` before anything is sent. The result has `ok` (a re-read shows
every copy), `row_ids`, `operation`, `from_date`, `to_date`, `from_meal`, `to_meal`, `added_items`,
`skipped` and `sync_response`.

These payloads are unit-tested against the fake server; none is in the recording.

## Removals

`remove_day_item(user_id, day, *, meal_type, item_id)` sends the row with `deletedAt` and reads
the day again:

```python
{
    "ok": True,                       # the row is gone from the re-read day
    "meal": "breakfast",              # the meal the row was in
    "removed_id": "...",
    "removed_absent_after_sync": True,
    "cleanup_mode": "removed",        # "none" when not deleted
    "removed": {...},                 # the row as it was (raw)
    "sync_response": [...],
}
```

Deleting a row that is not on the day (any more) is not an error, so a repeated delete is safe.
The result is then:

```python
{
    "ok": False,
    "reason": "item_not_found",
    "message": "item not found: '...'; rows on this day: <id> (<meal>, '<name>', measureQuantity=1); ...",
    "meal": "breakfast",
    "removed_id": None,
    "removed_absent_after_sync": False,
    "cleanup_mode": "none",
    "removed": None,
    "sync_response": None,
}
```

`ok` False without `reason` means the sync went through but the row is still there.

### Removing many rows

`remove_day_items_by_kind(user_id, day, *, item_kind, meal_type=None, all_meals=False,
row_ids=None, max_items=None, dry_run=False)` deletes every matching row in one sync. The guards:

- the scope is explicit: pass `meal_type` for one meal or `all_meals=True` for the whole day.
  Neither, or both, raises `FitatuValidationError` before the day is read;
- `row_ids` limits the targets to those `planDayDietItemId` values, e.g. the rows you added;
  every requested id that is not a target (not on the day, of another kind or meal, or past
  `max_items`) is listed in `missing_row_ids` and makes `ok` False;
- `max_items` caps the number of targets;
- `dry_run=True` sends nothing and returns `{ok, dry_run: True, requested_kind, targeted_count,
  missing_row_ids, meal, targets}`.

Otherwise the result is `{ok, requested_kind, targeted_count, removed_count, failed_count,
missing_row_ids, meal, targets, attempts, sync_response}`, with one `attempts` entry per target:
`{seq, meal, item_id, requested_kind, resolved_kind, ok, cleanup_mode}`. `targets` are the rows as
`list_day_items_for_removal()` gives them: `{meal, meal_name, item_id, product_id, food_type, source,
resolved_kind, reason, item}`, `item` being the row as the day GET returned it.
`classify_day_item_for_removal()` returns `{found, requested_item_id, resolved_kind, reason, meal,
item}`.

| `item_kind` | Rows |
|---|---|
| `auto` | every row |
| `normal_item` | `PRODUCT` rows, rows with a `productId`, and anything not listed below (recipes included) |
| `custom_add_item` | `CUSTOM_ITEM` rows that are not `custom_recipe_item` |
| `custom_recipe_item` | `CUSTOM_ITEM` rows with `source="API"` and a quantity of 2 or less |

The kind is only a filter: every kind is deleted the same way. `classify_day_item_for_removal()`
and `list_day_items_for_removal()` show how a row is classified.

```python
preview = client.planner.remove_day_items_by_kind(
    user_id, date.today(), item_kind="auto", meal_type="snack", dry_run=True
)
print([row["item_id"] for row in preview["targets"]])
```

## Activities

```python
hits = client.activities.search("bieganie", limit=5)  # activityId, name, energy, measuresDescription
logged = client.planner.add_activity(
    user_id, date.today(), activity_id=hits[0]["activityId"], minutes=30, energy=300
)
client.planner.remove_activity(user_id, date.today(), item_id=logged["row_id"])
```

`add_activity(user_id, day, *, activity_id, minutes, energy, row_id=None)` logs minutes of a
catalog activity. The server stores `energy` (kcal) exactly as sent and does not compute it;
the catalog's `energy` is an estimate for the effort in `measuresDescription`, and
`activities.search(phrase, bmr=..., sex=...)` returns values computed for the user. `row_id`
becomes the activity's `uuid` and works as for food rows. The result is `{ok, row_id,
added_activity, sync_response}`, `ok` from a re-read.

`remove_activity(user_id, day, *, item_id)` re-sends the activity in the write shape with
`deletedAt`: `{ok, row_id, removed, sync_response}`, or for an id that is not an activity of that
day `{ok: False, row_id, reason: "item_not_found", message, removed: None, sync_response: None}`.
Only the first simple measure is carried over; an activity logged with advanced measures only
would be rejected (`FitatuPlannerSyncError`).

## The day's note

`set_note(user_id, day, *, note)` replaces the note (`None` or `""` deletes it), sending the tags the
day already has: `{ok, note, previous, sync_response}`, `ok` from a re-read and `previous` the note
before. `get_day()` returns the note as `note` and its tags as `tagsIds`.

## Errors

| Error | When |
|---|---|
| `FitatuValidationError` | A bad argument (date, meal name, quantity, `item_kind`, scope, `patch` key, a `row_id` that is not a UUID v1 or belongs to another row, an ambiguous `productId`), raised before anything is sent. |
| `FitatuNotFoundError` | The meal is not on the day; the row is not on the day (update, move, replace, classify, copy); a meal to copy has no rows; no search hit at `index`. The message lists what the day has. Removals return `reason: "item_not_found"` instead. |
| `FitatuPlannerSyncError` | The sync answered 202 but rejected a day; nothing was saved for it. `exc.results` has the per-day list. |

Other API errors are described in the
[README](https://github.com/Jezue/fitatu_library/blob/main/README.md).
