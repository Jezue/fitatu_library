# API Overview

## Main entry points

### `FitatuApiClient`

Use this when you want direct endpoint access, explicit exceptions, and full
control over request/response handling.

Core responsibilities:

- request execution
- token refresh and retry policy
- refresh-token rotation when the backend returns a new refresh token
- operational reporting
- low-level helpers for common endpoint families

Default base URL behavior:

- when no explicit `base_url` override is provided, the client resolves to
  `https://fitatu.com/api`
- `www.fitatu.com/api` is normalized to `fitatu.com/api` because the `www` host
  can return landing HTML for API paths
- cluster endpoints remain available as explicit overrides, e.g.
  `https://pl-pl0.fitatu.com/api`

Attached modules:

| Module | Responsibility |
|---|---|
| `client.auth_api` | Refresh-token convenience wrapper. |
| `client.planner` | Planner day reads, sync, add/update/remove, and experimental move/replace helpers. |
| `client.user_settings` | User profile and settings endpoints. |
| `client.diet_plan` | Diet-plan settings and meal-schema reads. |
| `client.water` | Water reads and writes. |
| `client.activities` | Activity catalog reads. |
| `client.resources` | Static resource endpoints such as recipe food tags. |
| `client.cms` | CMS GraphQL requests. |

### `FitatuLibrary`

Use this when you want a higher-level facade built from a `session_data` payload.

This layer is useful for:

- easy construction from stored session data
- login bootstrap via `login_with_email_via_api(...)`
- planner convenience helpers
- simplified product/recipe flows
- user-food search, duplicate cleanup, and product proposal helpers
- day macro aggregation and normalized day summaries
- structured `{"status": "ok", "result": ...}` / `{"status": "error", ...}` results

Login note:

- the live login contract is `POST /login` with `_username` and `_password`
- the facade persists a reusable `session_data` payload
- `fitatu_user_id` can be recovered from the JWT if it is not present as a dedicated field in the response

## Endpoint and Helper Map

| Area | Methods | Endpoint/source | Status |
|---|---|---|---|
| Auth refresh | `client.refresh_access_token()`, `client.auth_api.refresh()` | `POST /token/refresh` | Stable. Tries known payload variants and stores rotated refresh tokens. |
| Food search | `client.search_food()`, `FitatuLibrary.search_food()` | `GET /search/food/` | Stable. |
| User-food search | `client.search_user_food()`, `FitatuLibrary.search_user_food_via_api()` | `GET /search/food/user/{id}` | Unit-tested, integration-derived. |
| Product create | `client.create_product()`, `FitatuLibrary.create_product_via_api()` | `POST /products` | Stable with caution. Supports extended nutrition fields. The `measures` param is not supported (causes 404). |
| Product delete | `client.delete_product()`, `FitatuLibrary.delete_product_via_api()` | `DELETE /products/{id}` | Unit-tested, integration-derived. |
| Product proposals | `client.set_product_proposal()`, `client.set_product_raw_ingredients()` | `POST /products/{id}/proposals` | Unit-tested, integration-derived. |
| User-product cleanup | `client.find_matching_user_product()`, `client.cleanup_duplicate_user_products()` | User-food search + product delete | Helper-level API. Requires explicit brand or predicate for cleanup. |
| Planner read | `client.get_day_plan()`, `client.planner.get_day()` | `/diet-and-activity-plan/{id}/day/{day}` | Stable with caution. |
| Planner sync | `client.planner.sync_days()`, `client.planner.sync_single_day()` | `/v2/diet-plan/{id}/days` | Stable with caution. Optional `synchronous=True` sends `synchronous=true`. |
| Planner add/update/remove | `client.planner.*`, `FitatuLibrary.*_via_api()` | Planner day snapshot and sync routes | Stable with caution. Removal uses snapshot/soft-delete fallback. |
| Planner move/replace | `client.planner.move_day_item()`, `client.planner.replace_day_item_with_custom_item()` | Planner day snapshot + synchronous sync | Live-tested (2026-04-22). Cross-meal fallback added. |
| Day macros | `FitatuLibrary.get_day_macros_via_api()` | Derived from planner day snapshot | Helper-level API. |
| Day summary | `FitatuLibrary.get_day_summary_via_api()` | Derived from planner day snapshot | Helper-level API. |
| Recipes/catalog | `client.get_recipe()`, `client.get_recipes_catalog()` | Recipe endpoints | Stable with caution. |
| Water/activity/resources | `client.water`, `client.activities`, `client.resources` | Service modules | Stable with caution. |
| CMS | `client.cms.graphql()` | CMS GraphQL | Stable with caution. |

## Product and User-Food Helpers

`create_product()` supports the basic macro fields and additional fields discovered
in related integrations:

- `saturated_fat` -> `saturatedFat`
- `salt`

Note: `measures` is intentionally **not** supported — including it in the payload
causes the backend to return 404. Use `set_product_raw_ingredients()` to annotate
a product after creation.

`set_product_raw_ingredients()` is a convenience wrapper over product proposals. A
list of ingredients is joined into a comma-separated string before being sent as
the `rawIngredients` proposal value.

`find_matching_user_product()` and `cleanup_duplicate_user_products()` combine
user-food search with nutrition matching. The cleanup helper intentionally requires
either `brand` or `predicate` so callers do not delete broad search results by
accident.

## Day Macros and Summary

The facade exposes two derived helpers that do not rely on a separate summary
endpoint:

| Helper | Returns |
|---|---|
| `get_day_macros_via_api(..., include_meal_breakdown=True)` | Totals for `energy`, `protein`, `fat`, `carbohydrate`, `fiber`, `sugars`, and `salt`, optionally split by meal. |
| `get_day_summary_via_api(...)` | User id, date, totals, meals, meal totals, and normalized item rows. |

These helpers are built from the planner day snapshot. They are useful when a
caller needs predictable data even if direct summary endpoint families differ
between Fitatu clusters.

## Planner Item Removal

Use `FitatuLibrary.remove_day_item_via_api()` when you want a structured,
high-level delete flow.

- planner hard `DELETE` is not treated as a reliable removal method on the current live cluster
- the preferred live strategy is marker-based day sync: `deletedAt` plus reduced `measureQuantity`
- delete targets should be planner row ids (`planDayDietItemId`), not `productId`
- the lower-level `client.planner.remove_day_item()` exposes the same behavior

This matches live observations from `2026-04-19`: hard delete did not succeed on
the validated planner row, omission-only sync did not persist, and marker-based
day sync did.

For the exact search-item removal flow, see `docs/DELETE_SEARCH_ITEMS.md`.

## Experimental Planner Sync Helpers

Two helpers exist for workflows observed in external integrations:

| Helper | Behavior | Caveat |
|---|---|---|
| `move_day_item()` | Marks the old row as deleted and adds a copied row to another meal/day. | Live-tested (2026-04-22). Searches all meal buckets if item not in specified meal. |
| `replace_day_item_with_custom_item()` | Marks the old row as deleted and adds a custom nutrition row in one sync payload. | Live-tested (2026-04-22). Searches all meal buckets if item not in specified meal. |

Both default to synchronous sync because these flows are easier to verify when the
backend applies the day payload immediately.

## Error Model

Low-level API methods raise `FitatuApiError`.

The high-level `FitatuLibrary` facade usually returns structured dictionaries such as:

```python
{"status": "ok", "result": ...}
```

or:

```python
{
  "status": "error",
  "operation": "...",
  "message": "...",
  "status_code": 503,
  "body": "..."
}
```

## Suggested Usage Style

If you want:

- explicit control and exceptions: start with `FitatuApiClient`
- convenience wrappers around `session_data`: start with `FitatuLibrary`
- quick inspection of stable and experimental areas: read `docs/STABILITY_MATRIX.md`
