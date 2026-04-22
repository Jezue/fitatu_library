# Planner Item Deletion Guide

## Overview

Planner deletion on the live cluster is snapshot/soft-delete based. There is no working
planner hard-delete route in practice.

## The Problem

The historical hard-delete endpoint is unavailable:
```
DELETE /diet-plan/{userId}/day/{dateISO}/{mealKey}/{itemId}
```

Returns: **404 Not Found** on current cluster

This endpoint exists in traffic patterns and older assumptions, but it is not a
practical deletion method on the current live cluster.

## The Solution: Snapshot Remove and Soft-Delete

The library uses **snapshot sync** and **soft-delete-style cleanup**:

### Step-by-step flow

1. **Fetch the current day snapshot**
   ```
   GET /diet-plan/{userId}/day/{dateISO}
   ```

2. **Locate the item in its meal bucket**
   - Search by `planDayDietItemId` (primary match)
   - Fallback to `productId` if needed

3. **Modify the item in memory**
   - Either omit the item from the synced snapshot
   - Or apply `deletedAt` / low-quantity soft-delete semantics
   - Keep other fields intact as needed by the backend

4. **Sync the modified snapshot back**
   ```
   POST /diet-plan/{userId}/days
   ```
   - Sends the entire day structure with modified item
   - Backend processes the "upsert" operation

5. **Verify deletion by re-fetching**
   ```
   GET /diet-plan/{userId}/day/{dateISO}
   ```
   - Confirm the item was modified (qty=0.01)
   - Check if item is functionally gone

## Backend Behavior

### What works (soft-delete)

- `measureQuantity` reduction to 0.01 is **respected**
- Item no longer counts toward daily nutrition totals
- Item is **functionally deleted** from a user perspective
- Calorie/macro calculations exclude the item

### What doesn't work

- `visible=false` is **ignored** by backend
- Item record remains in the database with qty=0.01
- Item still appears in day snapshots (but with minimal quantity)
- There is no working planner hard-delete path to rely on

## Return Value

When you call `remove_day_item()`:

```python
result = client.planner.remove_day_item(
    user_id="your-user-id",
    day=date(2026, 4, 19),
    meal_key="breakfast",
    item_id="uuid..."
)
```

You get:

```python
{
    "ok": True,  # Success indicator
    "cleanupMode": "soft_deleted",  # Method used on the current cluster
    "beforeCount": 5,  # Items in meal before deletion
    "afterCount": 5,  # Items in meal after (unchanged for soft-delete)
    "removedId": "6c164eda-3b76-11f1-b495-e36dcdcf7b7b",
    "removedIdAbsentAfterSync": False,  # Item not gone, just soft-deleted
    "countDecreased": False,  # Count didn't change (item still in list)
    "softDeleteQuantity": 0.01,
    "markInvisible": True,
    "removed": {
        # Original item object before deletion
        "planDayDietItemId": "6c164eda-3b76-11f1-b495-e36dcdcf7b7b",
        "name": "Banan",
        "measureQuantity": 1.0,  # Original quantity
        "foodType": "PRODUCT",
        # ... other fields
    },
    "syncResponse": [...]  # Raw API response from sync
}
```

## Smart Failover: Aggressive Soft-Delete

**New in v0.2.1**: The library now implements a two-tier soft-delete strategy for items
that fail normal soft-deletion (particularly API-created CUSTOM_ITEMs):

### Why API-Created Items Fail Normal Deletion

When you try to delete an API-created CUSTOM_ITEM (source="API"), the backend:

1. **Accepts** your snapshot sync (ok=true temporarily)
2. **Reconstructs** the item after sync completes
3. Returns the item with full quantity intact

This is **intentional** - backend protects API-sourced items to maintain sync state
with external integrations. Items created via API need backend persistence.

### The Failover Chain

1. **Try hard DELETE endpoint**
   ```
   DELETE /diet-plan/{userId}/day/{dateISO}/{mealKey}/{itemId}
   ```
   - Returns 404 (endpoint unavailable on current cluster)

2. **Try soft-delete via snapshot** (standard, qty=0.01)
   ```
   POST /diet-plan/{userId}/days
   Item: {measureQuantity: 0.01, visible: false}
   ```
   - Works for PRODUCT items and user-created CUSTOM_ITEMs
   - Fails for API-created CUSTOM_ITEMs (backend reconstructs)

3. **Try aggressive soft-delete via snapshot** (qty=0.0)
   ```
   POST /diet-plan/{userId}/days
   Item: {measureQuantity: 0.0, visible: false}
   ```
   - Extreme soft-delete for edge cases
   - Still doesn't delete API-items (backend protection)
   - Useful for debugging or when qty=0.01 fails

### Key Finding: Backend Protection is Intentional

| Item Type | Hard DELETE | Soft-Delete | Why Protected? |
|-----------|-----------|------------|----------------|
| PRODUCT | ❌ 404 | ✅ Works | N/A |
| CUSTOM_ITEM (user) | ❌ 404 | ✅ Works | User owns it |
| CUSTOM_ITEM (API) | ❌ 404 | ❌ Backend reconstructs | External integration dependency |

**API-created items are protected for a reason:**
- They're created by integrated systems (health devices, fitness apps, etc.)
- Backend owns the sync state
- Frontend can soft-delete them visually (local state)
- API cannot delete them permanently (would break sync)

### Enabling aggressive soft-delete fallback

By default, aggressive soft-delete is **enabled**:

```python
result = client.planner.remove_day_item(
    user_id, day, meal_key, item_id,
    use_aggressive_soft_delete=True  # ← enabled by default
)
```

To disable and stop at normal soft-delete:

```python
result = client.planner.remove_day_item(
    user_id, day, meal_key, item_id,
    use_aggressive_soft_delete=False  # Only try qty=0.01
)
```

### Response patterns

**Normal soft-delete success (qty=0.01):**
```python
{
    "ok": True,
    "cleanupMode": "soft_deleted",
    "beforeCount": 5,
    "afterCount": 5,  # Count unchanged
    "softDeleteQuantity": 0.01,
    "countDecreased": False,
}
```

**Aggressive soft-delete fallback (qty=0.0):**
```python
{
    "ok": True,
    "cleanupMode": "soft_deleted",
    "softDeleteQuantity": 0.0,
    "aggressive_soft_delete_used": True,  # Indicates fallback was used
}
```

**API-created CUSTOM_ITEM failure (cannot delete):**
```python
{
    "ok": False,
    "cleanupMode": "none",
    "countDecreased": False,
    "removedIdAbsentAfterSync": False,
    # Item still present - backend protection
}
```

## When to use which method

### Use `remove_day_item()` (recommended)

```python
result = client.planner.remove_day_item(user_id, day, meal_key, item_id)
if result.get("ok"):
    print("Item soft-deleted successfully")
```

- Automatic 2-tier soft-delete fallover
- Works on all clusters
- Handles edge cases with aggressive qty=0
- For API-items: soft-deletes them (they won't disappear but qty=0)
- Recommended for production use

### Use `soft_remove_day_item_via_snapshot()` directly

```python
# Normal soft-delete (qty=0.01)
result = client.planner.soft_remove_day_item_via_snapshot(user_id, day, meal_key, item_id)

# Aggressive soft-delete (qty=0.0)
result = client.planner.soft_remove_day_item_via_snapshot(
    user_id, day, meal_key, item_id,
    soft_delete_quantity=0.0,
    mark_invisible=True
)
```

- Explicit soft-delete with configurable quantity
- Useful for testing or when you specifically want soft-delete
- Respects `soft_delete_quantity` and `mark_invisible` parameters
- For API-items: still soft-deletes them (backend protects full deletion)

## Full Example: Clear an Entire Day

```python
from datetime import date
from fitatu_api import FitatuApiClient, FitatuAuthContext

# Setup
session_data = {...}  # Your session payload
auth = FitatuAuthContext.from_session_data(session_data)
client = FitatuApiClient(auth=auth)
user_id = "your-user-id"
target_date = date(2026, 4, 19)

# Get the day
day = client.planner.get_day(user_id, target_date)
diet_plan = day.get("dietPlan", {})

# Track deletions
deleted = []
failed = []

# Delete from each meal
for meal_key in ["breakfast", "second_breakfast", "lunch", "dinner", "snack", "supper"]:
    items = diet_plan.get(meal_key, {}).get("items", [])
    for item in items:
        item_id = item.get("planDayDietItemId")
        item_name = item.get("name")

        result = client.planner.remove_day_item(
            user_id, target_date, meal_key, item_id
        )

        if result.get("ok"):
            deleted.append(f"{meal_key}: {item_name}")
        else:
            failed.append(f"{meal_key}: {item_name}")

print(f"Deleted {len(deleted)} items")
print(f"Failed: {len(failed)} items")

# Verify the day is now empty (or functionally empty)
final_day = client.planner.get_day(user_id, target_date)
final_plan = final_day.get("dietPlan", {})

visible_items = 0
for meal_key in ["breakfast", "second_breakfast", "lunch", "dinner", "snack", "supper"]:
    items = final_plan.get(meal_key, {}).get("items", [])
    for item in items:
        # Check if item is actually visible (not soft-deleted)
        if item.get("measureQuantity", 1) > 0.01:
            visible_items += 1

if visible_items == 0:
    print("✅ Day is functionally empty")
else:
    print(f"⚠️ {visible_items} visible items remain")
```

## Implementation Details

### Why snapshot sync works as a fallback

1. **Atomic operation**: All changes in one snapshot sync are processed together
2. **Flexible**: Can modify, add, or remove any field in the snapshot
3. **Reliable**: Works consistently across all API clusters
4. **Efficient**: Leverages existing planner sync infrastructure
5. **Reversible**: Items are soft-deleted, not permanently erased

### The snapshot structure

```python
{
    "dietPlan": {
        "breakfast": {
            "items": [
                {
                    "planDayDietItemId": "uuid...",
                    "foodType": "PRODUCT",
                    "measureId": "1",
                    "measureQuantity": 0.01,  # Soft-deleted
                    "ingredientsServing": 1,
                    "source": "API",
                    "productId": "123",
                    "updatedAt": "2026-04-19 14:30:00"
                },
                # ... more items
            ]
        },
        # ... other meals
    },
    "toiletItems": [],
    "note": null,
    "tagsIds": []
}
```

## Comparison: Hard vs Soft Delete

| Aspect | Hard Delete | Soft Delete |
|--------|-------------|------------|
| **Endpoint** | `DELETE /diet-plan/...` | `POST /diet-plan/days` (snapshot sync) |
| **Status** | 404 Not Found (unavailable) | ✅ Works reliably |
| **Item removal** | Complete deletion | Quantity reduced to 0.01 |
| **Record persistence** | Removes record | Keeps record with minimal qty |
| **Nutrition impact** | Item gone from calculations | ✅ Excluded from totals |
| **User visibility** | ✅ Item invisible | ✅ Item invisible (qty=0.01) |
| **Reversibility** | N/A | Could restore by increasing qty |
| **Recommendation** | N/A (unavailable) | ✅ Use this |

## CUSTOM_ITEM vs PRODUCT Item Deletion

**Important:** CUSTOM_ITEM deletion behaves differently from regular PRODUCT items.

### Key Differences

| Aspect | PRODUCT Items | CUSTOM_ITEM Items |
|--------|---------------|-------------------|
| **source** | Varies (API, USER, etc.) | Usually `source="API"` |
| **Soft-delete** | ✅ Works reliably | ⚠️ Depends on source |
| **User-created items** | ✅ Can delete | ✅ Can delete |
| **API-created items** | ✅ Can delete | ❌ Backend-protected |
| **Backend rejection** | Rare | Common for `source="API"` |
| **Cleanup mode** | `"soft_deleted"` | `"none"` (if rejected) |
| **ok flag** | `true` | `false` (if protected) |

### Why CUSTOM_ITEM Protection?

CUSTOM_ITEMs with `source="API"` (created via API integration or external system) are **intentionally protected** to:
- Maintain data integrity with integrated apps/systems
- Prevent accidental removal of items managed externally
- Preserve sync state between Fitatu and partner systems

### Example: Detection and Handling

```python
from fitatu_api import FitatuApiClient, FitatuAuthContext
from datetime import date

session_data = {...}
auth = FitatuAuthContext.from_session_data(session_data)
client = FitatuApiClient(auth=auth)

user_id = "your-user-id"
target_date = date(2026, 4, 19)

# Get day plan
day = client.planner.get_day(user_id, target_date)
diet_plan = day.get("dietPlan", {})

# Iterate through items
for meal_key, meal_data in diet_plan.items():
    if isinstance(meal_data, dict):
        items = meal_data.get("items", [])
        for item in items:
            item_type = item.get("foodType")  # "PRODUCT" or "CUSTOM_ITEM"
            source = item.get("source")  # "API", "USER", etc.
            item_id = item.get("planDayDietItemId")
            item_name = item.get("name")

            # Check if item is deletable
            if item_type == "CUSTOM_ITEM" and source == "API":
                print(f"⚠️ PROTECTED: {item_name} (source=API, cannot delete)")
                continue

            # Safe to delete
            print(f"✅ DELETABLE: {item_name}")
            result = client.planner.remove_day_item(
                user_id, target_date, meal_key, item_id
            )

            if result.get("ok"):
                print(f"   Deleted: {item_name}")
            else:
                print(f"   Failed: {result.get('cleanupMode')}")
```

### Backend Response Differences

**PRODUCT deletion (successful):**
```python
{
    "ok": True,
    "cleanupMode": "soft_deleted",
    "countDecreased": False,
    "removed": {...}
}
```

**CUSTOM_ITEM deletion (API-protected, fails):**
```python
{
    "ok": False,
    "cleanupMode": "none",
    "countDecreased": False,
    "markInvisible": True,
    "removed": {
        "foodType": "CUSTOM_ITEM",
        "source": "API",
        ...
    }
}
```

### Practical Guidelines

**When deleting items programmatically:**

1. **Always check the response**
   ```python
   result = client.planner.remove_day_item(...)
   if not result.get("ok"):
       if result.get("cleanupMode") == "none":
           print("Item is backend-protected (likely API-sourced)")
   ```

2. **Filter before deletion**
   ```python
   # Skip protected items
   if item.get("source") == "API" and item.get("foodType") == "CUSTOM_ITEM":
       continue  # Skip this item
   ```

3. **Expect different counts**
   - PRODUCT items: `beforeCount` might decrease after deletion
   - CUSTOM_ITEM items: Count stays same (backend rejects soft-delete)

4. **User-created CUSTOM_ITEMs are deletable**
   - These will have no `source` or `source != "API"`
   - They will respond with `ok=true` and `cleanupMode="soft_deleted"`

### Troubleshooting CUSTOM_ITEM Deletion

**Problem:** CUSTOM_ITEM won't delete

```python
result = client.planner.remove_day_item(user_id, date, "lunch", item_id)
if not result.get("ok"):
    print(result.get("cleanupMode"))  # "none"?
```

**Solution:**

Check if the item is API-protected:
```python
item = result.get("removed", {})
if item.get("source") == "API" and item.get("foodType") == "CUSTOM_ITEM":
    print("This item was created via API and cannot be deleted programmatically.")
    print("It must be managed through the external system that created it.")
else:
    print("Item should be deletable. Try again or check the item ID.")
```

## Troubleshooting

### Item not being deleted

```python
result = client.planner.remove_day_item(user_id, day, meal_key, item_id)
print(result.get("ok"))  # False?
print(result.get("cleanupMode"))  # "none"?
```

**Possible causes:**
- Item ID is incorrect (use `planDayDietItemId`, not `productId`)
- Item not found in that meal (verify with `get_day()` first)
- Network error during sync (check the `syncResponse`)
- Multiple items with similar names (match by exact ID)

**Solution:**
```python
# Always verify the item exists first
day = client.planner.get_day(user_id, target_date)
items = day["dietPlan"]["breakfast"]["items"]
target = [i for i in items if i["planDayDietItemId"] == item_id]
if target:
    # Item exists, safe to delete
    result = client.planner.remove_day_item(user_id, target_date, "breakfast", item_id)
```

### Soft-deleted item still visible

If the item appears with `measureQuantity=0.01` but still counts toward nutrition:

- This is **expected behavior** — soft-delete keeps the record but marks it as 0.01 qty
- The item is **functionally deleted** for user interface purposes
- Nutrition calculations should **exclude** it (but some clients might not)
- This is the only viable deletion method on the current cluster

### Want to verify soft-deletion worked

```python
# Before deletion
before = client.planner.get_day(user_id, target_date)
before_items = before["dietPlan"]["breakfast"]["items"]
before_count = len(before_items)

# Delete
result = client.planner.remove_day_item(user_id, target_date, "breakfast", item_id)

# After deletion
after = client.planner.get_day(user_id, target_date)
after_items = after["dietPlan"]["breakfast"]["items"]
after_count = len(after_items)

# Verify
print(f"Before: {before_count}, After: {after_count}")  # Counts unchanged (soft-delete)
for item in after_items:
    if item["planDayDietItemId"] == item_id:
        print(f"Qty: {item['measureQuantity']}")  # Should be 0.01
```

## Related Reading

- [COOKBOOK.md](COOKBOOK.md) - Practical examples
- [ARCHITECTURE.md](ARCHITECTURE.md) - Implementation details
- [FAQ.md](FAQ.md) - Soft-delete Q&A
- [STABILITY_MATRIX.md](STABILITY_MATRIX.md) - Endpoint stability notes
- [FRONTEND_VS_API_DELETION.md](FRONTEND_VS_API_DELETION.md) - Why the app UI can delete but the API cannot
