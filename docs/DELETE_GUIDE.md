# Deleting Planner Items

## How deletion works

There is no DELETE route for planner rows. A row is deleted by sending it again through the
day sync with `deletedAt` set, which is exactly what the mobile app does:

```text
POST /diet-plan/{userId}/days?synchronous=true
{"2026-10-13": {"dietPlan": {"breakfast": {"items": [{...the row..., "deletedAt": "2026-10-13 12:00:00"}]}}}}
```

After that, `GET /diet-and-activity-plan/{userId}/day/{date}` no longer returns the row.

Things that do **not** delete a row (all checked against the live API, see
`tests/fixtures/api_recording.json`):

- leaving it out of the sync payload: the endpoint upserts by `planDayDietItemId` and keeps
  every row that is not in the payload;
- shrinking `measureQuantity` or sending `visible: false`: the row stays;
- `DELETE /diet-plan/{userId}/day/{date}/{meal}/{itemId}` and similar routes: they do not exist.

Rows created through the API (`source="API"`), including custom rows, are deleted the same
way as everything else. Older versions of these docs said such rows were "protected"; that
came from quantity-only deletes, which never worked for any row.

## Delete one row

```python
from datetime import date

result = client.planner.remove_day_item(user_id, date(2026, 10, 13), "breakfast", item_id)
if not result["ok"]:
    print("not deleted:", result.get("reason") or result["cleanupMode"])
```

`item_id` is the row's `planDayDietItemId` (a `productId` also works). The row is looked up in
the given meal first and then in the other meals. The result:

```python
{
    "ok": True,                       # the row is gone from the reloaded day
    "cleanupMode": "removed",         # "none" when the row was not found or is still there
    "meal": "breakfast",              # the meal the row was actually in
    "removedId": "6c164eda-3b76-11f1-b495-e36dcdcf7b7b",
    "removedIdAbsentAfterSync": True,
    "removed": {...},                 # the row as it was before deletion
    "syncResponse": [{"date": "2026-10-13", "revision": "...", "errorMessage": None}],
}
```

Through the facade:

```python
result = lib.remove_day_item_via_api(target_date=date(2026, 10, 13), meal_key="breakfast", item_id=item_id)
# result["status"] is "ok", "partial" (not confirmed) or "error"
```

`remove_day_item_with_strategy()`, `soft_remove_day_item_via_snapshot()`,
`remove_day_item_via_snapshot()` and `rollback_added_item()` all end up in the same
`deletedAt` path. Their old options (`use_aggressive_soft_delete`, `soft_delete_quantity`,
`mark_invisible`, `neutralize_on_failure`, ...) are accepted and ignored.

## Delete many rows

```python
result = client.planner.remove_day_items_by_kind(user_id, day, item_kind="auto", meal_key="snack")
print(result["removedCount"], "of", result["targetedCount"])
```

All matching rows go out in one sync. `item_kind` selects which rows to delete:

| Kind | Rows |
|---|---|
| `normal_item` | `PRODUCT` rows and anything with a `productId` (recipes count here too) |
| `custom_add_item` | `CUSTOM_ITEM` rows that look manually added |
| `custom_recipe_item` | `CUSTOM_ITEM` rows with `source="API"` and a serving-like quantity (≤ 2) |
| `auto` | every row |

`client.planner.list_day_items_for_removal(user_id, day)` shows how each row is classified.

## Deleting something you just added

```python
added = client.planner.add_search_result_to_day_meal(user_id, day, meal_type="breakfast", phrase="banan")
row_id = added["addedItem"]["planDayDietItemId"]
client.planner.remove_day_item(user_id, day, "breakfast", row_id)
```

Writes are sent with `synchronous=true`, so the row is visible to the next GET and there is no
need to sleep between adding and deleting.

## Errors

- `FitatuPlannerSyncError`: the server answered 202 but rejected the day (`exc.results` holds
  the per-day `errorMessage`). Nothing was saved for that day.
- `FitatuApiError` with "meal not found in day payload": the meal key does not exist that day.
