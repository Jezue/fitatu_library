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

Yes: `planner.move_day_item()` and `planner.replace_day_item_with_custom_item()`. Each sends
one sync that deletes the old row and adds the new one, and both find the row even if it sits
in a different meal than the one you pass.

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

## How does item removal work if there is no DELETE endpoint?

The planner has no DELETE route. A row is removed by syncing it with `deletedAt` set, which is
exactly what the mobile app does; the day GET stops returning it and `remove_day_item()`
reports `cleanupMode="removed"`.

Leaving a row out of the sync payload does not delete it (the endpoint upserts by
`planDayDietItemId`). See [DELETE_GUIDE.md](DELETE_GUIDE.md).

## Can I delete multiple items at once?

Yes: `planner.remove_day_items_by_kind(user_id, day, item_kind="auto", meal_key=...)` deletes
every matching row in one sync.

## Do I need to wait after adding an item before changing it?

No. Planner writes are sent with `synchronous=true`, so the next read already sees them.
Older versions recommended `time.sleep(2)` between an add and an update/remove.
