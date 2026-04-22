# Deleting Items Added From Search

## Scope

This guide describes how to remove planner items that were added from the search flow.

It covers:

- how to identify the item to delete
- which API helper to call
- what fallback chain is used under the hood
- how to verify removal
- known limitations

## What Is a Search Item in Practice

When you add food from search, the library adds a planner entry that is typically a `PRODUCT` item.

Important identifiers:

- `addedItem.planDayDietItemId`: primary item id used for deletion
- `meal_key`: meal bucket used during add (must match when deleting)

## Recommended Flow (Facade)

Use `FitatuLibrary` for add + remove in one consistent flow.

```python
from datetime import date
from fitatu_api import FitatuLibrary

target_day = date(2026, 4, 19)
meal_key = "breakfast"

lib = FitatuLibrary(session_data=session_data)

# 1) Add from search
add_result = lib.add_search_result_to_day_meal_via_api(
    target_date=target_day,
    meal_key=meal_key,
    phrase="mleko",
    index=0,
    measure_unit="ml",
    measure_amount=250,
    strict_measure=True,
)

item_id = ((add_result.get("result") or {}).get("addedItem") or {}).get("planDayDietItemId")

# 2) Remove exactly that item
remove_result = lib.remove_day_item_via_api(
    target_date=target_day,
    meal_key=meal_key,
    item_id=item_id,
    use_aggressive_soft_delete=True,
)

print(remove_result.get("status"))
print((remove_result.get("result") or {}).get("cleanupMode"))
```

## What remove_day_item_via_api Does Internally

The delete helper uses a failover chain:

1. Hard DELETE endpoint variants.
2. Snapshot real-delete by omitting the item and syncing the day.
3. Frontend-compatible soft-delete (`deletedAt` + minimal quantity).
4. Optional aggressive fallback (`measureQuantity=0.0`).

For search-added `PRODUCT` items, real delete or soft-delete fallback usually succeeds.

## Verification Checklist

After deletion:

1. Reload the day snapshot.
2. Check that the target `planDayDietItemId` is absent or soft-deleted.
3. For full-cleanup scripts, verify that visible items count is zero.

Example verification logic:

```python
plan = client.get_day_plan(user_id, target_day)
visible = []
for meal, data in (plan.get("dietPlan") or {}).items():
    if not isinstance(data, dict):
        continue
    for item in data.get("items") or []:
        if item.get("visible", True) and float(item.get("measureQuantity") or 0) > 0.01:
            visible.append((meal, item.get("planDayDietItemId"), item.get("name")))

print("visible items:", len(visible))
```

## Real Test Notes (2026-04-19)

Live tests validated add+remove for search products using three unit types:

- `mleko` in `ml` (250)
- `ryz` in `g` (120)
- `baton` in `sztuka` (1)

All three add operations succeeded with `measureResolution.strategy = direct_unit_match`.

Cleanup removal of inserted test entries also succeeded.

## Known Limitations

Some backend-protected `CUSTOM_ITEM` entries with `source="API"` may not be permanently removable.

This is not specific to the search flow, but to backend protection rules.

Search-added `PRODUCT` items are generally deletable with the flow above.
