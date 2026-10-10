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

Planner writes are sent with `synchronous=true`, so you can update or remove the row you just
added right away.

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

print(result["deletedCount"], result["deletedProductIds"])
print(result["keptProductId"])
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

## Remove planner items

```python
from datetime import date

lib = FitatuLibrary(session_data=session_data)
result = lib.remove_day_item_via_api(target_date=date.today(), meal_key="breakfast", item_id="plan-day-diet-item-id")
print(result["status"])  # "ok", "partial" (not confirmed) or "error"

# lower level, plus clearing a whole meal in one sync
client.planner.remove_day_item(user_id, date.today(), "breakfast", "plan-day-diet-item-id")
client.planner.remove_day_items_by_kind(user_id, date.today(), item_kind="auto", meal_key="snack")
```

Rows are deleted the way the app does it: re-sent with `deletedAt`. See
[DELETE_GUIDE.md](DELETE_GUIDE.md) for what that means and what the results look like.

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

print(result["ok"])  # True on success
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

print(result["ok"])  # True on success
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
