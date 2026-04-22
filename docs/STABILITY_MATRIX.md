# Stability Matrix

This package is reverse-engineered, so not every endpoint family has the same confidence level.

## Stable

- session parsing via `FitatuAuthContext`
- token export/import helpers
- auth state snapshots
- token refresh flow, including refresh-token rotation when returned by the backend
- food search
- recipe creation helpers
- product creation helpers
- day macro aggregation and normalized day summaries derived from planner snapshots
- planner day reads
- management report generation
- water/activity/resource read helpers

## Stable with caution

- planner item updates
- add-product planner helpers
- add-search-result planner helpers
- planner item removal via soft-delete snapshot sync
- optional synchronous day sync
- user-food search
- product deletion
- product proposal helpers, including `rawIngredients`
- duplicate user-product cleanup with explicit brand or predicate filtering
- user settings reads
- recipe catalog reads
- endpoint probing

These areas are already useful and tested, but still depend on reverse-engineered routes that may shift over time.

### Planner Item Removal - Special Case

**Status:** Stable with caution (snapshot/soft-delete flow is reliable)

The historical hard-delete route (`DELETE /diet-plan/{uid}/day/{date}/{meal}/{itemId}`)
returns **404 Not Found** on the current API cluster and should be treated as
non-functional for planner deletion work.

**Implemented behavior:** The library uses snapshot removal and soft-delete style
cleanup:

1. Fetches the complete day snapshot
2. Applies snapshot removal and/or soft-delete markers
3. Syncs the modified snapshot back
4. Verifies removal by re-fetching the day

**Result:** Practical planner deletion often works through `removed`, `deleted_at`, or
`soft_deleted` outcomes after a day reload, not through true DELETE-route success.

**When to use:** Use `remove_day_item()` and treat snapshot/soft-delete as the real
planner deletion path on the current cluster.

## Experimental

- fallback route selection patterns
- planner write variants discovered from traffic captures
- endpoints that require trying multiple route candidates
- data export endpoint (`/settings/data-export`) availability across clusters
- fallback route selection patterns
- endpoints that require trying multiple route candidates
- data export endpoint (`/settings/data-export`) availability across clusters

`planner.move_day_item()` and `planner.replace_day_item_with_custom_item()` were
previously experimental and not live-tested. Both are now live-tested (2026-04-22)
and include cross-meal fallback search. Their return payloads still carry
`experimental=True` for traceability.

## Practical recommendation

If you want the most predictable parts of the package, start with:

- `FitatuAuthContext`
- `FitatuApiClient.search_food`
- `FitatuLibrary.get_day_macros_via_api`
- `FitatuLibrary.get_day_summary_via_api`
- `FitatuApiClient.get_day_plan`
- `FitatuApiClient.management_report`
- `FitatuLibrary.add_product_to_day_meal_via_api`

Then expand outward once your workflow is stable.
