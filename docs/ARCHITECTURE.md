# Architecture

## Package layers

### `src/fitatu_api/auth.py`

Auth and session parsing primitives:

- `FitatuAuthContext`
- `FitatuTokenStore`

This layer knows how to interpret stored session data and persist tokens.

### `src/fitatu_api/client.py`

The main low-level HTTP client:

- request execution
- retry policy
- token refresh with refresh-token rotation
- management reporting
- convenience wrappers for common stable endpoints
- product, user-food, proposal, duplicate-cleanup, and nutrition-match helpers

### `src/fitatu_api/planner.py`

Planner-specific read and write helpers. This is where the day sync and meal mutation logic lives.

**Key methods:**

- `get_day()` - fetch planner day snapshot
- `sync_single_day()` - send a day payload (writes send only the rows they change)
- `add_search_result_to_day_meal()` - add product from search
- `update_day_item()` - modify item quantity/details
- `remove_day_item()` - delete a row (re-sent with `deletedAt`, then checked)
- `remove_day_items_by_kind()` - delete many rows in one sync
- `get_day_revisions()` - revision ids for cheap change detection
- `sync_days(..., synchronous=True)` - opt into backend synchronous day sync
- `move_day_item()` - move helper, live-tested (2026-04-22), cross-meal fallback included
- `replace_day_item_with_custom_item()` - replace helper, live-tested (2026-04-22), cross-meal fallback included

#### Planner Item Deletion Flow

There is no DELETE route for planner rows (neither live nor in the decompiled 4.17.1 app).
`remove_day_item()` deletes the way the app does: it re-syncs the row with `deletedAt`.

**Current practical flow**
```
GET /diet-and-activity-plan/{userId}/day/{dateISO}
  → Classify the runtime item shape
  → Set deletedAt on the row (fallbacks: omission, then quantity 0 / visible false)
  → POST /diet-plan/{userId}/days
  → GET /diet-and-activity-plan/{userId}/day/{dateISO} and check the id is gone from every meal
```

#### Runtime removal kinds

Removal strategy is selected from the current payload shape, not from the original
creation intent.

- `normal_item`: `PRODUCT` rows and rows with `productId`
- `custom_add_item`: manual-like `CUSTOM_ITEM`
- `custom_recipe_item`: serving-like API `CUSTOM_ITEM`

Live observations from `2026-04-19`:

- a quick-add style `CUSTOM_ITEM` with `source="API"` and quantity `1` resolved to
  `custom_recipe_item`
- a row with `foodType="RECIPE"` resolved to `normal_item`

Return values are described in [DELETE_GUIDE.md](DELETE_GUIDE.md).

### `src/fitatu_api/service_modules.py`

Smaller read/write modules that sit next to planner logic:

- user settings
- diet plan settings
- water
- activities
- resources
- CMS GraphQL
- auth refresh helper

### `src/fitatu_api/facade.py`

Higher-level wrapper for users who prefer to start from a `session_data` payload and call convenience methods that return structured `ok/error` results.

Facade-specific convenience helpers also provide:

- full day macro aggregation
- normalized day summaries
- product proposal and raw-ingredients wrappers
- user-food search and duplicate cleanup wrappers
- planner move/replace wrappers

### `src/fitatu_api/operational_store.py`

SQLite-backed event storage used for lightweight diagnostics and lifecycle visibility.

## Design goals

- keep low-level request logic in one place
- keep planner logic separate from service helpers
- expose a public API that is still readable from the top-level package
- preserve backward-compatible import paths where possible
- keep operational tooling available without leaking it into every public method

## Public entry points

For most users:

- `FitatuApiClient`
- `FitatuLibrary`
- `FitatuAuthContext`
- `FitatuApiError`

For maintainers and debugging:

- `FitatuOperationalStore`
- `FitatuOperationalEvent`
- module-specific helpers exposed from the package root
