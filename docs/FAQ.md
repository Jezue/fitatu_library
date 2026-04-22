# FAQ

## Does this package use browser automation?

No. The package is intentionally API-only.

## Is this an official Fitatu SDK?

No. It is a reverse-engineered client library shaped to behave like a normal Python package.

## Should I start with `FitatuApiClient` or `FitatuLibrary`?

Use `FitatuApiClient` if you want direct, explicit control and exceptions.

Use `FitatuLibrary` if you want convenience helpers built from `session_data`.

## Is every endpoint equally stable?

No. The package contains stable, stable-with-caution, and experimental areas. See [STABILITY_MATRIX.md](STABILITY_MATRIX.md).

## Does refresh-token rotation work?

Yes. `refresh_access_token()` tries the known refresh payload variants and stores
a rotated refresh token when Fitatu returns one. Export the updated session context
after a successful refresh if your application persists tokens externally.

## Can I read day macros without a dedicated summary endpoint?

Yes. Use `FitatuLibrary.get_day_macros_via_api()` for totals and optional meal
breakdown, or `FitatuLibrary.get_day_summary_via_api()` for normalized meals and
item rows. Both are derived from the planner day snapshot.

## Can I manage user-created products?

Partially. The library exposes product creation, deletion, user-food search,
product proposals, `rawIngredients` proposals, nutrition matching, and explicit
duplicate cleanup helpers. Dedicated `/product-ingredients/` endpoint families
are still outside the current scope.

## Can I move or replace a planner item?

There are helpers for this: `planner.move_day_item()` and
`planner.replace_day_item_with_custom_item()`. Both are live-tested (2026-04-22)
and include cross-meal fallback search. Their return payloads carry
`experimental=True` for traceability.

## What file should I read first?

Usually:

1. `README.md`
2. `docs/GETTING_STARTED.md`
3. `docs/API_OVERVIEW.md`
4. `docs/COOKBOOK.md`

## How should I quickly sanity-check my session payload?

Run:

```bash
python demo.py
```

or call:

```python
client.describe_auth_state()
client.management_report()
```

## Why does item removal return `cleanupMode=soft_deleted` instead of removing the item completely?

Because there is currently no working planner hard-delete path on the live cluster.

The historical `DELETE /diet-plan/{uid}/day/{date}/{meal}/{itemId}` route is not a
practical deletion method on the current cluster, so the library uses snapshot and
soft-delete style cleanup instead:

- Reduces the item's `measureQuantity` to `0.01` (minimal quantity)
- Optionally sets `deletedAt` and/or `visible=false`
- Item may remain in the payload, but become **functionally deleted** from the library's perspective

Live verification on `2026-04-19` confirmed that hard delete did not succeed, while
snapshot/soft-delete flows were the only working planner cleanup mechanisms.

If you need **complete physical deletion**, this is currently not possible via the API.

## Can I delete multiple items at once?

You must delete items individually. There is no batch delete endpoint. See the COOKBOOK
for an example of iterating through all items in a day and deleting them one by one.

## Why does `remove_day_item` return `item_not_found` right after I added an item?

The Fitatu backend sync is **eventually consistent** — after a successful `add_*` call,
the item may not appear in the next `get_day` snapshot immediately. Wait at least **2 seconds**
between adding and removing the same item:

```python
import time

result = lib.add_product_to_day_meal_via_api(...)
item_id = result["result"]["addedItem"]["planDayDietItemId"]

time.sleep(2)  # wait for backend sync to propagate

remove = lib.remove_day_item_via_api(..., item_id=item_id)
```

This also applies to `update_day_item_via_api` called immediately after an add.
