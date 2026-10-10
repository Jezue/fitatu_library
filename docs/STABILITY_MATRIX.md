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

There is no DELETE route for planner rows (the decompiled 4.17.1 app has none either).
The app deletes a row by syncing it with `deletedAt`, and the library does the same first.

Every removal helper sends the row with `deletedAt` in a synchronous sync and then checks the
row is gone from the reloaded day. See [DELETE_GUIDE.md](DELETE_GUIDE.md).

## New in 0.4.0 (request shapes checked live, less mileage)

- body measurements (`client.measurements`)
- range summaries and diary export (`client.summary`)
- favourite and recently used foods (`client.foods`)
- activity search and recent activities
- planner day revisions
- goals/targets via `settings-new/{day}`

## How this is tested

The planner write contract (upsert by id, omitted rows kept, rows replaced as a whole,
`deletedAt` hides a row, non-v1 ids reject the day) is pinned by real recorded responses in
`tests/fixtures/api_recording.json`. Unit tests run against a fake server built from that
recording, and a replay test fails if the fake drifts from it.

## Deprecated

These helpers call routes that do not exist and emit `DeprecationWarning`:
`get_product_for_meal`, `quick_add_form`, `send_changes`, `add_day_items`,
`remove_activity_day_item`. Most came from strings in `libapp.so` that are the app's own
screen routes (e.g. `/food/quick-add/form`, `/product/:productId/:mealType/:date`).

## Legacy / web routes

Working, but not used by the 4.17.1 mobile app: `/water/{id}/{day}`,
`/users/{id}/settings[/{day}]`, `/diet-plan/{id}/settings[...]`, `/users/{id}/firebaseToken`,
the website CMS GraphQL endpoint.

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
