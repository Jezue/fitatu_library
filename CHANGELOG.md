# Changelog

All notable changes to this project will be documented in this file.

The format is inspired by Keep a Changelog, with practical notes for a small Python library.

## [0.3.0] - 2026-04-21

Third alpha release focused on broader Fitatu workflow coverage, test coverage, water write support, and production readiness.

Added:

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

Changed:

- `FitatuApiError.__repr__` now shows `status_code` and `message` for easier debugging
- README expanded with endpoint/helper tables, derived summary docs, and experimental planner sync notes
- `docs/API_OVERVIEW.md`, `docs/COOKBOOK.md`, `docs/STABILITY_MATRIX.md`, `docs/ARCHITECTURE.md`, `docs/GETTING_STARTED.md`, and `docs/FAQ.md` updated for the current public API surface
- `.gitignore` updated to exclude operational scripts, APK decompilation artifacts, and generated reports

Removed:

- Old operational scripts (`add_all_measures_day19.py`, `clear_days_via_library.py`, `live_additions_no_delete.py`)
- Duplicate section in `docs/DELETE_GUIDE.md`
- `tests/test_recipe_live.py` (superseded by `test_live_all.py`)

## [0.2.0] - 2026-04-18

Second alpha release focused on making the package feel more like a mature public client library.

Added:

- stronger public API contract coverage for low-level and high-level convenience methods
- richer README examples for recipes, catalog reads, planner writes, and operational state
- release guide in `docs/RELEASING.md`
- generic package badges in the README for Python support, status, linting, and typing
- maintainer tooling via `.editorconfig` and `.pre-commit-config.yaml`
- CI artifact uploads for coverage and built distributions

Changed:

- documentation now presents the package more explicitly as a publishable library
- release process is documented as a repeatable maintainer workflow
- package metadata now points to the dedicated `fitatu_api` repository path

## [0.1.0] - 2026-04-18

Initial packaged repository extracted from the workspace integration code.

Added:

- installable `src`-layout Python package
- public exports for low-level and high-level APIs
- auth/session helpers and token store
- low-level HTTP client with retry and refresh handling
- planner, settings, recipes, activities, resources, and CMS modules
- SQLite-backed operational event store
- compatibility import paths for legacy module names
- initial test suite for public API and resilience behavior
- project metadata, MIT license, README, and contributor docs
