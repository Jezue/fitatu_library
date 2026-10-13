# Changelog

All notable changes to this project will be documented in this file.

The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and this project
uses [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

The planner is rebuilt on what the day sync endpoint actually does, now pinned by recorded real
responses in the test suite.

### Changed

- planner writes send only the rows they change. Adds send the new row, updates send the changed
  row in full. Nothing else on the day is re-sent, so rows edited on the phone in the meantime
  are no longer overwritten with stale values
- every removal helper (`remove_day_item`, `remove_day_item_with_strategy`,
  `soft_remove_day_item_via_snapshot`, `remove_day_item_via_snapshot`, `rollback_added_item`)
  sends the row with `deletedAt` and checks that it is gone. The old per-kind chain (omission,
  quantity 0.01, `visible: false`, retries) is gone, and its options are accepted and ignored
- `remove_day_items_by_kind()` deletes all matching rows in one sync instead of one by one
- planner writes use `synchronous=true`, so there is no need to wait between an add and an
  update/remove, and the internal read-after-write retries with `time.sleep()` are gone
- removal results are simpler: `beforeCount`, `afterCount`, `countDecreased`,
  `softDeleteQuantity`, `markInvisible`, `useDeletedAt`, `deletedAt`, `classification` and
  `attempts[].result` are no longer returned (`attempts` has a single `deleted_at` step)
- `add_recipe_to_day_meal()` no longer returns `recipeDetailsHydration`, and `addedItem` is the
  row as sent (`recipeId`, no name or nutrition); read the day to see what the server filled in.
  Add results no longer put `updatedAt` on `addedItem`
- `move_day_item()` / `replace_day_item_with_custom_item()` results no longer carry the
  `experimental` / `liveTested` flags
- `planner.py` shrank from 2266 to about 1460 lines

### Deprecated

- `add_recipe_to_day_meal(hydrate_from_recipe_details=...)`: a row with just `recipeId` comes
  back with name and nutrition, so the ~120 lines of recipe "hydration" were removed

### Added

- `tests/fixtures/api_recording.json`: real API responses (scrubbed), plus
  `scripts/record_fixtures.py` to refresh them. `tests/fake_server.py` is built from them, and
  `tests/test_recorded_contract.py` replays the recording to keep the fake honest
- `python -m fitatu_api login` / `fitatu-api login`: log in with e-mail and password and save
  `session_data.json` (0600) for `FitatuLibrary`, `example.py` and `demo.py`
- docs on getting a session, including copying it from the web app for Apple/Facebook accounts

### Fixed

- new planner rows got their `planDayDietItemId` from `uuid.uuid1()`, which embeds the machine's
  MAC address, so every row sent to Fitatu carried it. Row ids now use a random node
- `FitatuApiClient.login(..., token_store_path=...)` returned a client with the old tokens from
  the token store instead of the fresh ones, and did not save the fresh pair
- `demo.py` pointed at a `fitatu_login.py` script that does not exist

### Removed

- `docs/FRONTEND_VS_API_DELETION.md` (its premise, that the API cannot delete rows the app can,
  was wrong) and `docs/DELETE_SEARCH_ITEMS.md` (folded into a much shorter `DELETE_GUIDE.md`)

## [0.4.0] - 2026-09-21

Based on a decompile of the Fitatu Android app 4.17.1 (Flutter, Dart AOT) and live checks of
every changed request. The full list of routes the app uses is in
`docs/APP_4_17_1_API_CATALOG.md`.

### Fixed

- planner writes no longer untick "eaten" on every item of the day; a row synced without the
  flag gets `eaten=false` and the compacted payload dropped it (the `eaten=` arguments of the add
  and update helpers never reached the server for the same reason)
- `sync_days()` raises `FitatuPlannerSyncError` when the server rejects a day. The endpoint
  answers 202 in that case and only reports `errorMessage` per day, so failed writes used to be
  reported as successes
- `remove_day_item_via_snapshot()` no longer reports `ok=True` when the row is still there
  (whole-day "before" count was compared with a single-meal "after" count)
- removal tries `deletedAt` first: leaving a row out of the payload never deletes it
- `soft_remove_day_item_via_snapshot()` checks the whole day after the reload; a row living in
  another meal used to come back as "removed"
- `rollback_added_item()` deletes with a `deletedAt` marker instead of re-syncing the day
  without the row (which never deletes), so it no longer always falls back to "neutralised"
- `FitatuLibrary` planner helpers now honour client kwargs such as `base_url`; non-Polish
  accounts were silently sent to `pl-pl`
- the token store file is written with owner-only permissions (0600)
- `user_settings.get_new()` calls `/settings-new/{day}`; the dateless GET was the cause of the
  405, not the account type
- live test suite: the default test day was yesterday, and its fixture clears the test meal,
  so running it wiped real snack entries. It now defaults to a day a year ahead
- `demo.py` called `get_recipes_catalog(params=...)`, which does not accept params
- README/COOKBOOK used `cleanup["deleted"]`; the keys are `deletedCount` / `deletedProductIds`

### Added

- `client.measurements`: weight and body sizes per day, weight history and chart, body-part
  history, size summary. `set_day()` merges with the stored record and refuses to relabel stored
  values with a different unit
- `client.summary`: range nutrition and activity totals, per-nutrient day series, top products,
  diary export (csv/xls/pdf/txt); `request(..., raw=True)` for binary responses
- `client.foods`: favourite foods (read/toggle) and recently used foods
- `activities.search()`, `activities.get_recent()`, `planner.get_day_revisions()`
- facade: `get_daily_targets_via_api`, `log_weight_via_api`, `get_weight_history_via_api`,
  `get_range_summary_via_api`, `export_diary_via_api`, `get_favorite_foods_via_api`,
  `get_recent_foods_via_api`
- typed errors: `FitatuAuthError` (401), `FitatuRateLimitError` (429), `FitatuConnectionError`
  (no HTTP response), `FitatuPlannerSyncError` (rejected day), all subclassing `FitatuApiError`;
  `FitatuApiError.codename`. `login()` raises them too, so a network failure during login is
  now `FitatuConnectionError` instead of a raw `requests` exception
- README section on which headers are required and how the API host is chosen
- `docs/APP_4_17_1_API_CATALOG.md` with every route of the 4.17.1 app

### Changed

- `search_food()` sends `accessType[]` like the app
- `activities.get_catalog()` uses `/activities` and accepts `page`, `limit`, `locale`, `bmr`, `sex`
- `get_product_details()` tries `/products/{id}` then `/recipes/{id}`; the `/v2` and `/v3`
  variants do not exist
- day sync payloads no longer send `toiletItems` (the field is `toilet`, and omitted keys keep
  their stored value)

### Deprecated

- `planner.get_product_for_meal()`, `quick_add_form()`, `send_changes()`, `add_day_items()` and
  `remove_activity_day_item()`: their routes do not exist (mostly app screen routes picked up
  from the binary). `quick_add_form_with_fallback()` now goes straight to search + add.

### Removed

- unused hard-delete helpers in `PlannerModule`

## [0.3.0] - 2026-04-21

Third alpha release focused on broader Fitatu workflow coverage, test coverage, water write support, and production readiness.

### Added

- `WaterModule.set_day()` and `WaterModule.add_intake()` — write water consumption for a day
- refresh-token rotation support and refresh payload fallbacks for `refresh_token`, `refreshToken`, and `token`
- extended product creation fields: `saturated_fat`, `salt`, and `measures`
- product helpers for user-food search, product deletion, product proposals, `rawIngredients`, nutrition matching, and duplicate cleanup
- full day macro aggregation through `FitatuLibrary.get_day_macros_via_api()`
- normalized day summaries through `FitatuLibrary.get_day_summary_via_api()`
- optional synchronous planner sync via `sync_days(..., synchronous=True)` and `sync_single_day(..., synchronous=True)`
- experimental, unit-tested planner helpers for `move_day_item()` and `replace_day_item_with_custom_item()`
- `tests/test_planner.py` — 136 unit tests for `PlannerModule` (static helpers, add/update/remove flows, measure resolution, recipe hydration); coverage from ~18% to 78%
- `tests/test_facade.py` — 36 unit tests for `FitatuLibrary` facade (delegation, error wrapping, missing user id guard)
- expanded unit coverage for refresh fallbacks, product helpers, day summaries, synchronous sync, and experimental planner move/replace payloads
- `tests/test_live_all.py` — live integration test suite covering the previously validated endpoint set with add/verify/remove cycles
- `py.typed` PEP 561 marker for downstream type checking support
- Lifecycle constants (`FITATU_LIFECYCLE_HEALTHY` etc.) now exported from the top-level package
- Logging added to `PlannerModule` (add, sync, remove operations)

### Changed

- `FitatuApiError.__repr__` now shows `status_code` and `message` for easier debugging
- README expanded with endpoint/helper tables, derived summary docs, and experimental planner sync notes
- `docs/API_OVERVIEW.md`, `docs/COOKBOOK.md`, `docs/STABILITY_MATRIX.md`, `docs/ARCHITECTURE.md`, `docs/GETTING_STARTED.md`, and `docs/FAQ.md` updated for the current public API surface
- `.gitignore` updated to exclude operational scripts, APK decompilation artifacts, and generated reports

### Removed

- Old operational scripts (`add_all_measures_day19.py`, `clear_days_via_library.py`, `live_additions_no_delete.py`)
- Duplicate section in `docs/DELETE_GUIDE.md`
- `tests/test_recipe_live.py` (superseded by `test_live_all.py`)

## [0.2.0] - 2026-04-18

Second alpha release focused on making the package feel more like a mature public client library.

### Added

- stronger public API contract coverage for low-level and high-level convenience methods
- richer README examples for recipes, catalog reads, planner writes, and operational state
- release guide in `docs/RELEASING.md`
- generic package badges in the README for Python support, status, linting, and typing
- maintainer tooling via `.editorconfig` and `.pre-commit-config.yaml`
- CI artifact uploads for coverage and built distributions

### Changed

- documentation now presents the package more explicitly as a publishable library
- release process is documented as a repeatable maintainer workflow
- package metadata now points to the dedicated `fitatu_api` repository path

## [0.1.0] - 2026-04-18

Initial packaged repository extracted from the workspace integration code.

### Added

- installable `src`-layout Python package
- public exports for low-level and high-level APIs
- auth/session helpers and token store
- low-level HTTP client with retry and refresh handling
- planner, settings, recipes, activities, resources, and CMS modules
- SQLite-backed operational event store
- compatibility import paths for legacy module names
- initial test suite for public API and resilience behavior
- project metadata, MIT license, README, and contributor docs
