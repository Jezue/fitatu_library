# Changelog

All notable changes to this project will be documented in this file.

The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and this project
uses [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [0.5.0] - Unreleased

The release that makes the library usable from an agent. The planner is rebuilt on what the day
sync endpoint really does, pinned by recorded API responses; sessions live in one file that the
client keeps up to date; every failure is a typed, explained error; and `FitatuTools` exposes
the diary as tools. No version has been published to PyPI yet, so unused and misleading surface
was removed instead of deprecated. See "Upgrading from 0.4" at the end of this section.

### Added

- `FitatuTools` (`fitatu_api.agent`): 19 diary tools with JSON Schema inputs, descriptions for
  language models and `readOnlyHint` / `destructiveHint` / `idempotentHint` annotations;
  `TOOLS`, `anthropic_tools()` and `openai_tools()`. `call()` checks the arguments before
  anything is sent and never raises; an add without a `row_id` gets one, and when the add fails
  in a way that may still have saved it (network, 5xx, 429, session, maintenance) the hint
  names it so the add can be repeated safely
- `describe_error()`: any exception as a JSON-safe dict with `error_type`, `retryable` and a
  `hint`, used by `FitatuTools` and `FitatuLibrary`
- the session file: `fitatu-api login` / `python -m fitatu_api login` (reads `FITATU_EMAIL` /
  `FITATU_PASSWORD`, `-o`, `--base-url`) writes `~/.fitatu/session.json` (or `$FITATU_SESSION`,
  alias `$FITATU_SESSION_PATH`), and `FitatuApiClient.from_session()`,
  `FitatuLibrary.from_session()` and `FitatuTools.from_session()` load it and write refreshed
  tokens back. The session carries the region host and the account's locales (schema 1.1);
  `login()` reads them from the profile
- `fitatu-api import-session` for accounts without a password (Apple or Facebook sign-in): it
  reads the tokens copied from the web app's `localStorage.user` (or a session file) on stdin,
  checks both with Fitatu by refreshing the bearer once, fills in the locales, timezone and host
  like `login()` and writes only those fields to the session file.
  `FitatuAuthContext.from_session_data()` reads that `localStorage.user` object directly
- the bearer token is refreshed 60 seconds before it expires, one refresh at a time, and a pair
  another process saved to the session file is picked up; `bearer_expires_at` in
  `describe_auth_state()`
- `FitatuValidationError` (also a `ValueError`), `FitatuNotFoundError` and
  `FitatuMaintenanceError` (Fitatu's `technical-break` header); `FitatuRateLimitError.retry_after`.
  Error messages say `METHOD /path -> status CODENAME: server message`
- planner: an optional `row_id` (UUID v1) on every add, so a repeated add updates the row
  instead of logging it twice; `rowId` in the results of adds, moves, replacements and
  activity writes; `add_activity()` and `remove_activity()` for workouts;
  `remove_day_items_by_kind(all_meals=, row_ids=, dry_run=)` with `missingRowIds`
- dates as `YYYY-MM-DD` strings in every method that takes a day
- `request(idempotent=True)`; the client keeps one pooled HTTP session, and `FitatuLibrary` and
  `FitatuTools` are context managers like the client
- `cleanup_duplicate_user_products(dry_run=True, max_deletions=10)`
- day summaries report `eaten_totals`, the day's activities, each row's food type and its `row_id`
- `FitatuLibrary.search_food_via_api()`; `export_diary_via_api(overwrite=)`
- `tests/fixtures/api_recording.json` (real, scrubbed API responses) with
  `scripts/record_fixtures.py`, a fake server built from it and a test that replays it
- docs: `docs/AGENTS.md`, `docs/PLANNER.md`, `docs/API_REFERENCE.md`; the README covers the
  session file, headers, retries, logging and testing
- `example.py` loads the session and logs in when it is missing or dead, like
  python-garminconnect's example; `demo.py` is an interactive menu over the agent tools that
  asks before writes; `hatch run all` runs lint, tests and the build

### Changed

- planner writes send only the rows they change: an add sends the new row, an update re-sends
  the changed row in full, a removal re-sends the row with `deletedAt`. Rows edited on the phone
  in the meantime are no longer overwritten. Updates, moves, replacements, removals and
  activity writes read the day again and report the outcome in `ok`
- `sync_days()` / `sync_single_day()` default to `synchronous=True` and are retried like reads
- planner parameters: a meal is always the keyword `meal_type`, an existing row `item_id`, a new
  row `row_id`; every argument after `(user_id, day)` is keyword-only
- a `productId` names a row only when exactly one row of the meal has it; otherwise the call
  raises and lists the candidates
- `remove_day_items_by_kind()` needs `meal_type` or `all_meals=True`
- `foods.get_recent(user_id, meal_type)` (was `meal`) normalises the meal like the planner, so
  `"Second Breakfast"` asks for `second_breakfast`
- `update_day_item(patch=)` refuses `planDayDietItemId`, `deletedAt`, `foodType`, `productId`
  and `recipeId`; quantities must be above 0 and custom-row nutrition 0 or more
- `resolve_product_measure()` raises for a unit other than g/ml that the product has no measure
  for, whatever `strict_measure` says
- `get_product_details()` reads `/products/{id}` only and no longer falls back to
  `/recipes/{id}`: product and recipe ids overlap, so that could return an unrelated recipe. A
  recipe id now gives a 404 or the unrelated product with that number; use `get_recipe()`
- removal results are simpler; a missing row returns `ok: False` with
  `reason: "item_not_found"` and a listing of the day's rows; update, move, replace and
  classify raise `FitatuNotFoundError` for it
- `add_recipe_to_day_meal()` sends the row without name or nutrition (the server fills them in
  from `recipeId`) and no longer fetches recipe details first; `addedItem` is the row as sent
- `FitatuLibrary` shares one client across calls, takes `base_url`, `timeout_seconds` and
  `session_path` in the constructor, has explicit keyword-only parameters (`meal_type`,
  `item_id`, `row_id` and `weight_unit` as in the planner and modules; a day is `target_date`,
  a range `from_date` / `to_date`, and errors name those), and returns one envelope: `status` `ok`, `partial` (the result has `ok` false,
  e.g. a write the re-read did not confirm) or `error` with `describe_error()` fields
- `export_diary_via_api()` returns csv/txt as text and pdf/xls as base64, writes files 0600 and
  refuses to overwrite; an empty range is `ok` with `result` `None`
- `get_daily_targets_via_api()`: `date` is the day asked for; the settings' own date (when those
  goals took effect) is `settings_since`
- the API host defaults to the session's `base_url`, else the storage locale's host
  (`de_DE` -> `https://de-de.fitatu.com/api`)
- login and token refresh go through `request()` with the same headers as every call; the
  client identity is one constant web-app profile that is not stored in sessions; refresh sends
  only `{"refresh_token"}`, and a 400/401 from it stops automatic refresh until the client gets
  a different refresh token
- a `Retry-After` of up to 30 s is honoured; a longer one raises at once
- `water.set_day()`, `measurements.set_day()`, `measurements.delete_day()` and
  `foods.set_favorite()` are retried like reads; writes return `{}` for an empty reply, and a
  read with an unexpected shape raises instead of returning `{"raw": ...}`
- `measurements.set_day()` normalises and checks units and values, and `None` means "not given";
  `measurements.delete_day()` treats a 404 as already deleted
- `activities.get_catalog()` and `search()` send the session's search locale by default
- request logs moved to DEBUG
- live tests use the session file, refuse a day on or before today, delete exactly the rows
  each test added, and run account writes only with `FITATU_LIVE_RUN_WRITE_ACCOUNT=1` (it
  replaces `FITATU_LIVE_RUN_WRITE_PRODUCT` / `FITATU_LIVE_RUN_WRITE_RECIPE`;
  `FITATU_LIVE_USER_ID` is no longer read)

### Deprecated

- `user_settings.get()`, `user_settings.get_for_day()` and every `DietPlanModule` method: use
  `user_settings.get_new(user_id, day)`

### Removed

- the operational store (`FitatuOperationalStore`, `FitatuOperationalEvent`), lifecycle states
  (`lifecycle_state`, `FITATU_LIFECYCLE_*`), `management_report()`, `reauthenticate()` and
  `FitatuLibrary.reauthenticate_session()`
- the import shims `fitatu_api.api_client`, `fitatu_api.modules` and `fitatu_api.fitatu_api`
- the client's one-line forwards (`get_user`, `get_user_settings*`, `get_diet_plan_settings`,
  `get_day_plan`, `get_food_tags_recipe`, `get_water`, `get_activities_catalog`),
  `AuthModule` / `client.auth_api`, `probe_known_endpoints()` and `request_first_success()`
- `CmsModule` / `client.cms`, `user_settings.update_system_info()` and `get_firebase_token()`
- planner helpers for routes that do not exist (`get_product_for_meal`, `quick_add_form`,
  `send_changes`, `add_day_items`, `remove_activity_day_item`) and
  `quick_add_form_with_fallback()`, which logged the first search hit and ignored the kcal
- `remove_day_item_via_snapshot()`, `soft_remove_day_item_via_snapshot()`,
  `remove_day_item_with_strategy()` and `remove_day_item_with_strategy_via_api()`, and the
  options that were accepted and ignored (`delete_all_related_meals`,
  `use_aggressive_soft_delete`, `max_soft_delete_retries`, `hydrate_from_recipe_details`,
  `neutralize_on_failure`, `neutralized_quantity`, `mark_invisible`, `synchronous` on move and
  replace); passing one is now a `TypeError`
- `FitatuLibrary`: `headless`, the per-call client options and `user_id` arguments,
  `search_food()`, and the raw response body in error results (also in the duplicate cleanup's
  `errors`)
- the `FITATU_API_KEY` / `FITATU_API_SECRET` overrides; client identity keys in older session
  files are ignored
- docs: `GETTING_STARTED.md`, `COOKBOOK.md`, `FAQ.md`, `ARCHITECTURE.md`, `API_OVERVIEW.md`,
  `STABILITY_MATRIX.md`, `DELETE_GUIDE.md`, `FRONTEND_VS_API_DELETION.md` and
  `DELETE_SEARCH_ITEMS.md`, replaced by the README and the three guides above

### Fixed

- `login()` reported a wrong e-mail or password as a generic API error: Fitatu answers it with
  422, not 401. It is now a `FitatuAuthError` ("the e-mail or password is wrong",
  `error_type` `auth`) whose hint says that accounts created with Apple or Facebook have no
  password and should use `fitatu-api import-session`; `fitatu-api login` prints that hint
- new planner rows carried this machine's MAC address in their `uuid1` id; ids now use a random
  node
- `FitatuLibrary` built a new client per call and threw away refreshed tokens
- `FitatuLibrary.search_food()` turned every error into "no results" and forced `pl_PL`
- `add_search_result_to_day_meal()` logged RECIPE hits as products; hits of other types are
  refused
- `rollback_added_item()` could delete the user's own row of the same product
- `move_day_item()` could move a row into a meal the target day does not have
- `login(..., token_store_path=)` returned the old tokens from the store and did not save the
  new pair
- the JWT payload was decoded as plain base64, which lost the user id for some tokens; sessions
  with only a token now get the user id from it
- a token store path with `~` created `./~/`, and a refresh replaced the whole session file with
  the token pair
- a refresh that failed with a 5xx, 429 or network error was reported as an expired session
- `water.add_intake()` failed on a day with no water logged
- `measurements.set_day()` could erase a stored day (non-object reply, `None` for a body part)
  and accepted `"kg"`, negative numbers and NaN
- `None` or wrong-type arguments escaped as `AttributeError` / `TypeError` instead of
  validation errors
- `page` / `limit` of the paged reads, `unit_capacity`, the product, recipe and measure ids of
  planner adds, `eaten` / `favorite` flags such as `"no"`, and `date` keys in `sync_days()` were
  sent unchecked; they now raise `FitatuValidationError` first
- the live tests' recipe teardown asked for the wrong row kind and left its row on the account;
  teardowns now remove the rows they added by row id
- `add_product_to_day_meal_via_api()` ignored `measure_amount` with a `measure_id`
- `replace_day_item_with_custom_item()` turned an eaten row into a planned one
- `get_weight_history_via_api()` stopped after 50 pages (2,050 entries) without saying so; it
  now reads until the last page

### Security

- API key, secret and bearer token only go to `https` hosts under `fitatu.com` (checked with
  the URL parser `requests` uses, userinfo refused), and `Authorization` only to URLs under the
  API host or `https://fitatu.com/api`. Redirects are not followed (a 3xx is an error), so a
  redirected login or refresh cannot carry the password or refresh token to another host
- ids in URL paths must be numeric; nutrient names and search phrases cannot leave their path
  segment
- log values under keys that look secret are redacted, `repr()` of the auth context hides
  tokens and the API secret, the e-mail is not logged, and a token that is not a valid header
  value is not quoted in errors
- the session file and token store are written 0600 through an atomic replace; diary exports
  are written 0600
- removed `get_firebase_token()` and `reauthenticate()`, which returned credentials, and the
  SQLite event store, a world-readable file that could end up holding tokens

### Upgrading from 0.4

- sessions: 0.4 had no session file of its own; its examples built the client from a
  `./session_data.json` made by hand. 0.5 keeps the session in `~/.fitatu/session.json` (or
  `$FITATU_SESSION`): create it with the new `fitatu-api login`, or set `FITATU_SESSION` to your
  existing `session_data.json` and load it with `FitatuApiClient.from_session()`, which then
  writes refreshed tokens back to it
- `FitatuLibrary(session_data, headless=...)` and per-call `user_id=` / `base_url=` ->
  `FitatuLibrary(session_data, base_url=, timeout_seconds=, session_path=)` or
  `FitatuLibrary.from_session()`
- `FitatuLibrary.search_food()` -> `search_food_via_api(phrase=, limit=, page=, locale=)`
- `log_weight_via_api(unit=)` -> `log_weight_via_api(weight_unit=)`
- facade `meal_key=` -> `meal_type=`; `from_meal_key` / `to_meal_key` -> `from_meal_type` /
  `to_meal_type`; `remove_day_item_with_strategy_via_api()` -> `remove_day_item_via_api()`
- facade error results: `body` is gone; read `error_type`, `retryable` and `hint`. Day summary
  rows: `plan_day_diet_item_id` -> `row_id`. An empty export: `status` `empty` -> `ok` with
  `result` `None`
- `client.get_day_plan()` -> `client.planner.get_day()`; `get_user()` ->
  `user_settings.get_profile()`; `get_user_settings*()` and `get_diet_plan_settings()` ->
  `user_settings.get_new(user_id, day)`; `get_water()` -> `water.get_day()`;
  `get_activities_catalog()` -> `activities.get_catalog()`; `get_food_tags_recipe()` ->
  `resources.get_food_tags_recipe()`; `client.auth_api.refresh()` ->
  `client.refresh_access_token()`; `request_first_success()` / `probe_known_endpoints()` ->
  `request()`
- `management_report()`, `lifecycle_state` -> `describe_auth_state()`;
  `reauthenticate()` -> run `fitatu-api login` and load the session again
- `remove_day_item(user_id, day, meal_key, item_id)` ->
  `remove_day_item(user_id, day, meal_type=..., item_id=...)`; the `*_via_snapshot` and
  `*_with_strategy` removals -> `remove_day_item()`
- `remove_day_items_by_kind(..., meal_key=)` -> `meal_type=` or `all_meals=True`
- `foods.get_recent(user_id, meal=)` -> `foods.get_recent(user_id, meal_type=)`
- `rollback_added_item(..., plan_day_diet_item_id=, product_id=)` ->
  `rollback_added_item(..., meal_type=, item_id=added["rowId"])`
- `classify_day_item_for_removal()` / `list_day_items_for_removal()`: `meal_key=` ->
  `meal_type=`; `get_meal()`, `list_meal_items()`, `find_meal_item()` take `meal_type=` as a
  keyword
- `sync_single_day(user_id, day, payload)` -> `sync_single_day(user_id, day, day_payload=payload)`
- `quick_add_form_with_fallback()` -> `add_custom_item_to_day_meal()`;
  `remove_activity_day_item()` -> `planner.remove_activity(user_id, day, item_id=...)`
- drop `hydrate_from_recipe_details=`, `synchronous=` on move/replace, and the ignored removal
  options
- `cleanup_duplicate_user_products()` is a dry run by default: pass `dry_run=False` to delete
- `client.cms`, `update_system_info()`, `get_firebase_token()`, `FitatuOperationalStore` and the
  `FITATU_API_KEY` / `FITATU_API_SECRET` variables are gone without a replacement
- `except ValueError` still catches argument errors (`FitatuValidationError` is a `ValueError`)

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
