# Cookbook

## Search for foods

```python
from fitatu_api import FitatuApiClient, FitatuAuthContext

auth = FitatuAuthContext.from_session_data(session_data)
client = FitatuApiClient(auth=auth)

foods = client.search_food("banan", limit=5)
for item in foods:
    print(item["name"])
```

## Read planner snapshot for a day

```python
from datetime import date

day_plan = client.get_day_plan("123", date.today())
print(day_plan["dietPlan"].keys())
```

## Read full day macros and normalized day summary

```python
from datetime import date
from fitatu_api import FitatuLibrary

lib = FitatuLibrary(session_data=session_data)

macros = lib.get_day_macros_via_api(
    target_date=date.today(),
    include_meal_breakdown=True,
)
summary = lib.get_day_summary_via_api(target_date=date.today())

print(macros["result"]["totals"]["energy"])
print(macros["result"]["meals"]["breakfast"]["protein"])
print(summary["result"]["meals"][0]["items"])
```

## Add a product to a meal through the facade

```python
from datetime import date
from fitatu_api import FitatuLibrary

lib = FitatuLibrary(session_data=session_data)
result = lib.add_product_to_day_meal_via_api(
    target_date=date.today(),
    meal_key="breakfast",
    product_id=123,
    measure_id=1,
    measure_quantity=2,
)
print(result["status"])
```

> **Timing note:** The Fitatu backend sync is eventually consistent. If you need to
> update or remove the item you just added, wait at least **2 seconds** first —
> otherwise `get_day` may return a stale snapshot that does not yet contain the new item.

## Create a richer user product

```python
from fitatu_api import FitatuApiClient, FitatuAuthContext

auth = FitatuAuthContext.from_session_data(session_data)
client = FitatuApiClient(auth=auth)

product = client.create_product(
    name="Meal prep chicken bowl",
    brand="custom",
    energy=640,
    protein=44,
    fat=18,
    saturated_fat=4,
    carbohydrate=72,
    fiber=6,
    sugars=8,
    salt=2.1,
)

client.set_product_raw_ingredients(
    product["id"],
    ["rice", "chicken breast", "tomato sauce"],
)
```

## Search user foods and find a nutrition match

```python
from datetime import date
from fitatu_api import FitatuApiClient, FitatuAuthContext

auth = FitatuAuthContext.from_session_data(session_data)
client = FitatuApiClient(auth=auth)

match = client.find_matching_user_product(
    user_id="123",
    phrase="Meal prep chicken bowl",
    day=date.today(),
    nutrition={
        "energy": 640,
        "protein": 44,
        "fat": 18,
        "carbohydrate": 72,
    },
    brand="custom",
    tolerance=0.01,
)

if match:
    print(match["id"])
```

## Clean up duplicate user products

The cleanup helper deliberately requires `brand` or a custom `predicate`. This
keeps broad phrase searches from deleting unrelated user foods.

```python
from datetime import date

result = client.cleanup_duplicate_user_products(
    user_id="123",
    phrase="Meal prep chicken bowl",
    day=date.today(),
    brand="custom",
    keep_product_id="product-id-to-keep",
)

print(result["deleted"])
print(result["kept"])
```

For custom matching logic:

```python
result = client.cleanup_duplicate_user_products(
    user_id="123",
    phrase="Meal prep",
    day=date.today(),
    predicate=lambda item: item.get("source") == "meal-prep-import",
)
```

## Remove a single planner item

### Via FitatuLibrary (recommended)

```python
from datetime import date
from fitatu_api import FitatuLibrary

lib = FitatuLibrary(session_data=session_data)
result = lib.remove_day_item_via_api(
    target_date=date.today(),
    meal_key="breakfast",
    item_id="plan-day-diet-item-id",
)
print(result["status"])  # "ok" if removed, "error" if failed
```

### Via FitatuApiClient (lower-level)

```python
from datetime import date
from fitatu_api import FitatuApiClient, FitatuAuthContext

auth = FitatuAuthContext.from_session_data(session_data)
client = FitatuApiClient(auth=auth)

result = client.planner.remove_day_item(
    user_id="your-user-id",
    day=date(2026, 4, 19),
    meal_key="breakfast",
    item_id="6c164eda-3b76-11f1-b495-e36dcdcf7b7b",
)

print(result.get("ok"))  # True if removed
print(result.get("cleanupMode"))  # "soft_deleted" or "removed"
```

### How it works - The Current Delete Chain

The library uses a snapshot/soft-delete deletion strategy on the current cluster:

1. Try snapshot-based removal of the item from the day payload.
2. If the backend keeps the item, try `deletedAt`-style soft-delete semantics.
3. If needed, try more aggressive soft-delete semantics.

### Why soft-delete instead of full removal?

The Fitatu backend cluster treats items with `measureQuantity=0.01` as effectively
deleted for calorie/macro calculations, even though the item record persists. This
is the only viable deletion family on the current live cluster, because there is no
working planner hard-delete route in practice.

### Example output from soft-delete

```python
{
    "ok": True,
    "cleanupMode": "soft_deleted",
    "beforeCount": 5,
    "afterCount": 5,  # Count unchanged, but items have qty=0.01
    "removedId": "6c164eda-3b76-11f1-b495-e36dcdcf7b7b",
    "removedIdAbsentAfterSync": False,  # Item still in snapshot
    "countDecreased": False,
    "softDeleteQuantity": 0.01,
    "markInvisible": True,
    "removed": {...}  # Original item object before soft-delete
}
```

### Deleting all items from a day

```python
from datetime import date
from fitatu_api import FitatuApiClient, FitatuAuthContext

auth = FitatuAuthContext.from_session_data(session_data)
client = FitatuApiClient(auth=auth)
user_id = "your-user-id"
target_date = date(2026, 4, 19)

# Get all items
day = client.planner.get_day(user_id, target_date)
diet_plan = day.get("dietPlan", {})

# Delete from each meal
for meal_key in ["breakfast", "second_breakfast", "lunch", "dinner", "snack", "supper"]:
    items = diet_plan.get(meal_key, {}).get("items", [])
    for item in items:
        item_id = item.get("planDayDietItemId")
        result = client.planner.remove_day_item(user_id, target_date, meal_key, item_id)
        print(f"Deleted {item.get('name')}: {result.get('ok')}")
```

## Deleting CUSTOM_ITEM vs PRODUCT items

**Important:** Some items cannot be deleted. Learn the differences:

```python
from datetime import date
from fitatu_api import FitatuApiClient, FitatuAuthContext

auth = FitatuAuthContext.from_session_data(session_data)
client = FitatuApiClient(auth=auth)
user_id = "your-user-id"
target_date = date(2026, 4, 19)

# Get all items
day = client.planner.get_day(user_id, target_date)
diet_plan = day.get("dietPlan", {})

deleted_count = 0
skipped_count = 0
failed_count = 0

for meal_key in ["breakfast", "second_breakfast", "lunch", "dinner", "snack", "supper"]:
    items = diet_plan.get(meal_key, {}).get("items", [])

    for item in items:
        item_id = item.get("planDayDietItemId")
        item_name = item.get("name")
        item_type = item.get("foodType")  # "PRODUCT" or "CUSTOM_ITEM"
        source = item.get("source")  # "API", "USER", etc.

        # Skip API-sourced CUSTOM_ITEMs (backend-protected)
        if item_type == "CUSTOM_ITEM" and source == "API":
            print(f"⏭️  SKIPPED (API-protected): {item_name}")
            skipped_count += 1
            continue

        # Try to delete
        result = client.planner.remove_day_item(user_id, target_date, meal_key, item_id)

        if result.get("ok"):
            print(f"✅ DELETED: {item_name}")
            deleted_count += 1
        else:
            # Check cleanup mode to understand why it failed
            cleanup = result.get("cleanupMode")
            print(f"❌ FAILED: {item_name} (cleanup mode: {cleanup})")
            failed_count += 1

print(f"\nSummary: {deleted_count} deleted, {skipped_count} skipped, {failed_count} failed")
```

### Understanding the response

**For PRODUCT items or user-created CUSTOM_ITEMs (successful):**
```python
{
    "ok": True,
    "cleanupMode": "soft_deleted",  # Item's quantity reduced to 0.01
    "countDecreased": False,  # Item still in list, but functionally gone
    "removed": {...}  # Original item before deletion
}
```

**For API-sourced CUSTOM_ITEMs (backend-protected):**
```python
{
    "ok": False,  # Deletion failed
    "cleanupMode": "none",  # No cleanup applied
    "removed": {
        "foodType": "CUSTOM_ITEM",
        "source": "API",  # This is why it's protected
        ...
    }
}
```

### Why are some CUSTOM_ITEMs undeletable?

CUSTOM_ITEMs with `source="API"` are **intentionally protected** because they are managed by
external systems (integrations with fitness apps, wearables, etc.). The backend prevents deletion
to maintain sync state with those systems.

**Solution:** These items must be managed through the system that created them, not directly
via the Fitatu API.

## Delete an item added from search (quick recipe)

```python
from datetime import date
from fitatu_api import FitatuLibrary

lib = FitatuLibrary(session_data=session_data)
target_day = date(2026, 4, 19)

# Add from search
add_result = lib.add_search_result_to_day_meal_via_api(
    target_date=target_day,
    meal_key="dinner",
    phrase="baton",
    index=2,
    measure_unit="sztuka",
    measure_amount=1,
    strict_measure=True,
)

item_id = ((add_result.get("result") or {}).get("addedItem") or {}).get("planDayDietItemId")

# Remove by planDayDietItemId
remove_result = lib.remove_day_item_via_api(
    target_date=target_day,
    meal_key="dinner",
    item_id=item_id,
    use_aggressive_soft_delete=True,
)

print(remove_result["status"])
print((remove_result.get("result") or {}).get("cleanupMode"))
```

See also: `docs/DELETE_SEARCH_ITEMS.md` for the full flow and verification checklist.

## Move a planner item to another meal or day

Builds a single sync payload that marks the old row as deleted and adds a copied
row in the new location. Live-tested (2026-04-22). Searches all meal buckets if
the item is not found in the specified meal.

```python
from datetime import date, timedelta

result = client.planner.move_day_item(
    user_id="123",
    from_day=date.today(),
    from_meal_type="breakfast",
    item_id="plan-day-diet-item-id",
    to_day=date.today() + timedelta(days=1),
    to_meal_type="dinner",
)

print(result["experimental"])  # True
print(result["ok"])             # True on success
```

## Replace a planner item with a custom row

Keeps the change in one day sync: delete marker for the old item plus a custom
nutrition row. Live-tested (2026-04-22). Searches all meal buckets if the item
is not found in the specified meal.

```python
from datetime import date

result = client.planner.replace_day_item_with_custom_item(
    user_id="123",
    day=date.today(),
    meal_type="dinner",
    item_id="plan-day-diet-item-id",
    name="Custom dinner row",
    calories=500,
    protein_g=35,
    fat_g=12,
    carbs_g=60,
)

print(result["experimental"])  # True
print(result["ok"])             # True on success
```

## Export session context for reuse

Pass `include_tokens=True` when the exported dict will be used to reconstruct a
new `FitatuLibrary` instance. Without tokens the re-imported session cannot make
authenticated API calls.

```python
# Reconstruct a session in another process / script
exported = lib.export_session_context(include_tokens=True)
lib2 = FitatuLibrary(session_data=exported)

# include_tokens=False (default) — safe for logging / display only
summary = lib.export_session_context(include_tokens=False)
print(summary["fitatu_user_id"])
```

## Build an operational report

```python
report = client.management_report()
print(report["lifecycle_state"])
print(report["operational_event_count"])
```

## Probe stable endpoints

```python
from datetime import date

checks = client.probe_known_endpoints("123", date.today())
for check in checks:
    print(check["path"], check["ok"])
```

## Read the session state before doing work

```python
state = client.describe_auth_state()
print(state["lifecycle_state"])
print(state["fitatu_user_id"])
```

## Read settings for today and build a quick summary

```python
from datetime import date

settings = client.get_user_settings("123", day=date.today())
summary = {
    "goal": settings.get("goal"),
    "kcal": settings.get("kcal"),
    "dietType": settings.get("dietType"),
}
print(summary)
```

## Use the demo as a smoke-check

```bash
python demo.py
```

This is a convenient way to verify that session parsing, planner reads, search, catalog access, and diagnostics are all still behaving sensibly.
