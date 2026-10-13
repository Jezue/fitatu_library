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

What the library does with that:

- an add sends only the new row; an update re-sends the changed row in full, so the fields you
  do not touch (including `eaten`) keep their values;
- a removal re-sends the row with `deletedAt`;
- updates, moves, replacements, removals and activity writes read the day again afterwards and
  report the outcome in `ok`;
- `sync_days()` is marked safe to repeat (it upserts by id), so the client retries it on a
  network error, 429 or 5xx like a read.

## Ids: `item_id`, `row_id`, `rowId`

| Name | Where | Meaning |
|---|---|---|
| `planDayDietItemId` | raw day payload | The id of a food row. |
| `uuid` | raw `activityPlan` entry | The id of a logged activity. |
| `item_id` | argument of update, move, replace, remove | A row that is already on the day: its `planDayDietItemId` (an activity's `uuid` for `remove_activity`). |
| `row_id` | optional argument of every add | The id to give a new row. Pass your own UUID v1 to make the add safe to repeat. |
| `rowId` | results of the adds, `move_day_item`, `replace_day_item_with_custom_item`, `add_activity` and `remove_activity` | The id of the row the call wrote (for a move or replace, the new row). |
| `itemId`, `removedId` | results of `update_day_item` (`itemId`), `remove_day_item` and `rollback_added_item` (`removedId`), `remove_day_items_by_kind` (`attempts[].itemId`) | The id of the row the call changed or deleted. These results have no `rowId`. |
| `row_id` | day summaries (`FitatuTools.get_day`, `FitatuLibrary.get_day_summary_via_api`) | The row's `planDayDietItemId` (an activity's `uuid` under `activities`). |

A `productId` is accepted as `item_id` only when exactly one row of the given `meal_type` has
it. With the same product logged twice the call raises `FitatuValidationError` listing the
candidates, so pass the `planDayDietItemId`.

A `planDayDietItemId` is found in any meal; `meal_type` only has to be a meal the day has.

Raw payloads use the API's camelCase keys; summaries built by the library use snake_case.

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
| `add_search_result_to_day_meal(..., phrase, index=0, measure_quantity=1, measure_amount=None, measure_unit=None, ...)` | Hit `index` of a food search: a PRODUCT hit by `productId`, a RECIPE hit by `recipeId`. Other hit types raise. |

Every add reads the day first (the meal must exist, else `FitatuNotFoundError`), sends one row
and returns:

```python
{
    "ok": True,                  # the sync accepted the row (a rejection raises)
    "rowId": "...",              # the new row's planDayDietItemId
    "meal": "breakfast",
    "productId": 24881865,       # or "recipeId" and "foodType", or "name" for a custom row
    "addedItem": {...},          # the row as sent (no name or nutrition for catalog rows)
    "syncResponse": [{"date": "...", "revision": "...", "errorMessage": None}],
}
```

The unit and search variants add `measureResolution` (the measure chosen and how), the search
variant adds `searchItem`.

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
(`item_id=added["rowId"]`) and only that row; for a row that never landed it returns the
"not found" removal result below.

## Updates

`update_day_item(user_id, day, *, meal_type, item_id, measure_quantity=None, measure_id=None,
eaten=None, name=None, source=None, patch=None)` changes the given fields and re-sends the row
in full. `patch` sets other row fields in camelCase; it may not set `planDayDietItemId`,
`deletedAt`, `foodType`, `productId` or `recipeId` (a different food is a removal plus an add).
Quantities must be greater than 0.

The result has `ok` (the re-read row shows every change), `meal` (where the row is),
`requestedMeal`, `itemId`, `productId`, `before`, `after`, `updates`, `validation` (per field,
whether the re-read matches) and `syncResponse`. A row that is not on the day raises
`FitatuNotFoundError`; its message lists the day's rows.

## Moves and replacements

`move_day_item(user_id, from_day, *, from_meal_type, item_id, to_day=None, to_meal_type=None)`
deletes the row and adds a copy with a new id, in one sync (two days in one payload when the
day changes). The target meal is checked on the target day before anything is sent. The result
has `ok` (the copy is there and the original is gone), `rowId` (the copy), `fromDate`, `toDate`,
`fromMeal`, `toMeal`, `removedId`, `addedItem`, `deleteMarker`, `syncPayload`, `syncResponse`.

`replace_day_item_with_custom_item(user_id, day, *, meal_type, item_id, name, calories,
protein_g, fat_g, carbs_g, source="API")` swaps a row for a custom row in one sync; the new row
keeps the old one's `eaten`. The result
has `ok`, `rowId` (the new row), `date`, `meal`, `removedId`, `replacementItem`,
`deleteMarker`, `syncPayload`, `syncResponse`.

Both payloads are unit-tested against the fake server; neither is in the recording.

## Removals

`remove_day_item(user_id, day, *, meal_type, item_id)` sends the row with `deletedAt` and reads
the day again:

```python
{
    "ok": True,                       # the row is gone from the re-read day
    "meal": "breakfast",              # the meal the row was in
    "removedId": "...",
    "removedIdAbsentAfterSync": True,
    "cleanupMode": "removed",         # "none" when not deleted
    "removed": {...},                 # the row as it was
    "syncResponse": [...],
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
    "removedId": None,
    "removedIdAbsentAfterSync": False,
    "cleanupMode": "none",
    "removed": None,
    "syncResponse": None,
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
  `max_items`) is listed in `missingRowIds` and makes `ok` False;
- `max_items` caps the number of targets;
- `dry_run=True` sends nothing and returns `{ok, dryRun: True, requestedKind, targetedCount,
  missingRowIds, meal, targets}`.

Otherwise the result is `{ok, requestedKind, targetedCount, removedCount, failedCount,
missingRowIds, meal, attempts, syncResponse}`, with one `attempts` entry per target:
`{seq, meal, itemId, requestedKind, resolvedKind, ok, cleanupMode}`.

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
print([row["itemId"] for row in preview["targets"]])
```

## Activities

```python
hits = client.activities.search("bieganie", limit=5)  # activityId, name, energy, measuresDescription
logged = client.planner.add_activity(
    user_id, date.today(), activity_id=hits[0]["activityId"], minutes=30, energy=300
)
client.planner.remove_activity(user_id, date.today(), item_id=logged["rowId"])
```

`add_activity(user_id, day, *, activity_id, minutes, energy, row_id=None)` logs minutes of a
catalog activity. The server stores `energy` (kcal) exactly as sent and does not compute it;
the catalog's `energy` is an estimate for the effort in `measuresDescription`, and
`activities.search(phrase, bmr=..., sex=...)` returns values computed for the user. `row_id`
becomes the activity's `uuid` and works as for food rows. The result is `{ok, rowId,
addedActivity, syncResponse}`, `ok` from a re-read.

`remove_activity(user_id, day, *, item_id)` re-sends the activity in the write shape with
`deletedAt`: `{ok, rowId, removed, syncResponse}`, or for an id that is not an activity of that
day `{ok: False, rowId, reason: "item_not_found", message, removed: None, syncResponse: None}`.
Only the first simple measure is carried over; an activity logged with advanced measures only
would be rejected (`FitatuPlannerSyncError`).

## Errors

| Error | When |
|---|---|
| `FitatuValidationError` | A bad argument (date, meal name, quantity, `item_kind`, scope, `patch` key, a `row_id` that is not a UUID v1 or belongs to another row, an ambiguous `productId`), raised before anything is sent. |
| `FitatuNotFoundError` | The meal is not on the day; the row is not on the day (update, move, replace, classify); no search hit at `index`. The message lists what the day has. Removals return `reason: "item_not_found"` instead. |
| `FitatuPlannerSyncError` | The sync answered 202 but rejected a day; nothing was saved for it. `exc.results` has the per-day list. |

Other API errors are described in the
[README](https://github.com/Jezue/fitatu_library/blob/main/README.md).
