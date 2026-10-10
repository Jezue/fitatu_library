# Fitatu Library

![Python](https://img.shields.io/badge/python-3.11%2B-blue)
![Status](https://img.shields.io/badge/status-alpha-orange)
![License](https://img.shields.io/badge/license-MIT-green)
![Lint](https://img.shields.io/badge/lint-ruff-brightgreen)
![Types](https://img.shields.io/badge/types-mypy-informational)
![CI](https://github.com/Jezue/fitatu_library/actions/workflows/ci.yml/badge.svg)

> **Disclaimer:** This is an **unofficial**, **community-built** API client.
> It is not affiliated with, endorsed by, or connected to Fitatu Sp. z o.o. in any way.
> The API was reverse-engineered from the Fitatu mobile app for personal automation purposes.
> Use at your own risk and ensure compliance with Fitatu's Terms of Service.
> The MIT licence here covers the library code only — it does not grant any rights over the
> Fitatu service, its API, or its data.

`fitatu-api` is an unofficial Python client for the Fitatu nutrition API, built specifically
for **AI agents and automation pipelines**. It was created by reverse-engineering the Fitatu
mobile app's HTTP traffic so that Fitatu can be connected to AI assistants, home automation
systems, and personal tooling without writing fragile one-off scripts each time.

The project is intentionally API-only — no browser automation, no scraping, no GUI. It is
designed to serve as a clean, typed integration layer that AI agents and scripts can depend on.

Covered areas:

- 🔐 session management and token refresh
- 🔍 food search, favourite and recently used foods
- 📅 planner day reads and writes
- 🥗 products, user foods, product proposals, and recipes
- 💧 water and activity tracking
- ⚖️ body weight and body measurements
- 📊 day macros, server-side range summaries, goals/targets, and diary export (csv/xls/pdf/txt)
- 🔬 lightweight operational diagnostics

## 🎯 Why This Package Exists

Fitatu has no public API and no officially supported way to integrate with external tools. This
library exists to fill that gap.

The use cases it was built for:

- connecting Fitatu to AI agents (e.g. an LLM that can read or modify your meal plan)
- home automation flows (log meals from a smart device, fill a planner from a meal-prep script)
- personal data pipelines (export planner snapshots, analyse nutrition data offline)
- any scripted workflow that needs to talk to Fitatu programmatically

The API surface was discovered by decompiling the Flutter AOT binary (`libapp.so`, app 4.17.1)
and verified against live endpoints. The full list of routes the app uses is in
[docs/APP_4_17_1_API_CATALOG.md](docs/APP_4_17_1_API_CATALOG.md). Because Fitatu can change their API at any time without notice,
endpoints are grouped by stability in [docs/STABILITY_MATRIX.md](docs/STABILITY_MATRIX.md).

Goals:

- a stable low-level HTTP client that feels like a real library
- a small high-level facade suitable for use inside AI agent tools
- explicit auth/session handling with token refresh and retry logic
- tests and CI suitable for a publishable package
- clean separation between library code and operational experiments

## 👀 At a Glance

- installable Python package with `src` layout
- low-level client plus higher-level facade
- session parsing from stored payloads
- token refresh and retry handling
- planner reads and writes
- food, recipe, product, water, activity, and settings helpers
- operational diagnostics and SQLite-backed event capture
- input validation with descriptive error messages
- docs, tests, demo, build pipeline, and maintainer tooling

## ⚖️ License

The library source code is released under the **MIT licence**.

MIT covers the code in this repository. It does not cover the Fitatu API, its responses, or any
data returned by the service. Those remain the property of Fitatu Sp. z o.o.

## 📡 API Coverage Snapshot

Main packaged entry points:

| Entry point | Use when |
|---|---|
| `FitatuApiClient` | You want low-level endpoint access, explicit exceptions, and direct module methods. |
| `FitatuLibrary` | You want a higher-level facade built from `session_data` with `{"status": ...}` style results. |
| `FitatuAuthContext` | You already have stored tokens/session data and need a reusable auth object. |

Current helper areas:

| Area | Included |
|---|---|
| Auth/session | Session import/export, auth state snapshots, token refresh, refresh-token rotation. |
| Search/catalog | Public food search, user-food search, recipes, recipe catalog, food tag resources. |
| Products | Product creation, extended nutrition fields, measures, deletion, proposals, `rawIngredients`. |
| Planner | Day reads, full-day sync, add/update/remove helpers, optional synchronous sync. |
| Derived summaries | Full macro totals and normalized meal/item day summaries from planner snapshots. |
| Move/replace | Move a row to another meal/day, or replace it with a custom row, in one sync. |
| Tracking/settings | Water, activity catalog and search, goals/targets (`settings-new`), legacy settings reads, CMS GraphQL. |
| Measurements | Weight and body sizes per day, weight history and chart, body-part history, size summary. |
| Summaries/export | Range nutrition and activity totals, per-nutrient day series, top products, diary export. |
| Foods | Favourite foods (read/toggle), recently used foods per meal, own recent foods. |

### Covered endpoint families

Taken from the decompiled 4.17.1 app and checked against live endpoints. Paths are relative
to the API host (`https://pl-pl.fitatu.com/api` for Polish accounts, see [Headers and hosts](#-headers-and-hosts)).

| Endpoint | Method | Library call | Notes |
|---|---|---|---|
| `/login` | POST | `FitatuApiClient.login()` | `_username` / `_password`. |
| `/token/refresh` | POST | `client.refresh_access_token()` | Body `{refresh_token}`; rotated refresh tokens are stored. |
| `/search/new/food` | GET | `client.search_food()` | List filters use PHP-style keys (`accessType[]`). |
| `/search/food/user/{id}` | GET | `client.search_user_food()` | User-created foods. |
| `/search/food/{id}/last-used` | GET | `client.foods.get_recent()` | `?meal=breakfast` etc. |
| `/search/food/user/{id}/last-used` | GET | `client.foods.get_recent_own()` | |
| `/users/{id}/favorite/foods` | GET | `client.foods.get_favorites()` | |
| `/users/{id}/favorite/food/{PRODUCT\|RECIPE}/{foodId}` | PUT | `client.foods.set_favorite()` | Processed asynchronously (202). |
| `/products` | POST | `client.create_product()` | |
| `/products/{id}` | GET / DELETE | `client.get_product_details()`, `client.delete_product()` | Falls back to `/recipes/{id}` for recipe ids. |
| `/products/{id}/proposals` | POST | `client.set_product_proposal()`, `client.set_product_raw_ingredients()` | |
| `/recipes` | POST | `client.create_recipe()` | |
| `/recipes/{id}` | GET | `client.get_recipe()` | The app uses `/recipes-and-user-action/{id}/{userId}`, same payload. |
| `/recipes-catalog` | GET | `client.get_recipes_catalog()` | |
| `/recipes-catalog/category/{id}` | GET | `client.get_recipes_catalog_category()` | `id` is a string slug. |
| `/resources/food-tags/recipe` | GET | `client.get_food_tags_recipe()` | |
| `/users/{id}` | GET / PATCH | `client.get_user()`, `user_settings.update_profile()` | |
| `/users/{id}/settings-new/{day}` | GET | `user_settings.get_new()` | Goals, macro targets, BMR/TMR, meal schema, water settings. |
| `/users/{id}/settings-new` | PATCH | `user_settings.update_new()` | PATCH only; a GET without the date answers 405. |
| `/diet-and-activity-plan/{id}/day/{day}` | GET | `planner.get_day()` | Diet, activities, water, toilet, note for one day. |
| `/diet-plan/{id}/days` | POST | `planner.sync_days()` and all planner writes | Answers 202 even when a day is rejected; see below. |
| `/diet-plan/{id}/days/{day}/revisions` | GET | `planner.get_day_revisions()` | Cheap change detection. |
| `/v2/diet-plan/{id}/summary/custom` | GET | `client.summary.get_nutrition()` | Range totals per nutrient. |
| `/v2/diet-plan/{id}/summary/{nutrient}/custom` | GET | `client.summary.get_nutrient_by_day()` | |
| `/diet-plan/{id}/summary-product/{nutrient}/custom` | GET | `client.summary.get_top_products()` | |
| `/activity-plan/{id}/summary/custom` | GET | `client.summary.get_activities()` | |
| `/diet-plan/{id}/export` | GET | `client.summary.export_diary()` | `format=csv\|xls\|pdf\|txt`; 204 when the range is empty. |
| `/users/{id}/measurements/{day}` | GET / PUT / DELETE | `client.measurements.get_day()` / `set_day()` / `delete_day()` | |
| `/users/{id}/measurements/summary/weight` | GET | `client.measurements.get_weight_history()` | |
| `/users/{id}/measurements/chart/weight` | GET | `client.measurements.get_weight_chart()` | |
| `/users/{id}/measurements/size/{bodyPart}` | GET | `client.measurements.get_body_part_history()` | |
| `/users/{id}/measurements/summary/size` | GET | `client.measurements.get_size_summary()` | |
| `/activities` | GET | `client.activities.get_catalog()` | `bmr`/`sex` make the server compute energy for the user. |
| `/search/activity/{phrase}` | GET | `client.activities.search()` | |
| `/search/activity/{id}/last-used` | GET | `client.activities.get_recent()` | |

Legacy/web routes the library still uses. They work, but the 4.17.1 mobile app no longer calls them:

| Endpoint | Method | Library call | Notes |
|---|---|---|---|
| `/water/{id}/{day}` | GET / PUT | `client.water.*` | Same storage as `water` in the planner day; GET also returns the daily target. |
| `/users/{id}/settings`, `/users/{id}/settings/{day}` | GET | `user_settings.get()`, `get_for_day()` | Prefer `settings-new/{day}`. |
| `/diet-plan/{id}/settings[...]` | GET | `client.diet_plan.*` | |
| `/users/{id}/firebaseToken` | GET | `user_settings.get_firebase_token()` | |
| `https://www.fitatu.com/cms/api/graphql` | POST | `client.cms.graphql()` | Website CMS, not part of the app API. |

How planner writes behave (all confirmed live):

- `POST /diet-plan/{id}/days` upserts rows by `planDayDietItemId` (must be a UUID v1). Rows left
  out of the payload are kept, so omission never deletes anything.
- A row is deleted by sending it with `deletedAt`; the day GET then hides it.
- Keys missing from the day payload (`water`, `toilet`, `activities`) keep their stored values.
- A row is replaced as a whole (a row sent without `eaten` gets `eaten=false`), so the library
  sends only the rows it changes, and a changed row in full.
- Product and recipe rows need only `productId`/`recipeId`; the server fills name and nutrition.
- The response is `[{date, revision, errorMessage}]` with HTTP 202. A non-null `errorMessage`
  means the day was not saved; `sync_days()` raises `FitatuPlannerSyncError` for it.

Derived helpers without a dedicated direct endpoint:

| Helper | Source | Notes |
|---|---|---|
| `FitatuLibrary.get_day_macros_via_api()` | Planner day snapshot | Aggregates `energy`, `protein`, `fat`, `carbohydrate`, `fiber`, `sugars`, and `salt`; optional meal breakdown. |
| `FitatuLibrary.get_day_summary_via_api()` | Planner day snapshot | Returns normalized totals, meals, and item rows for one day. |
| `FitatuLibrary.get_daily_targets_via_api()` | `settings-new/{day}` | Energy, macros in grams and percent, BMR/TMR. |
| `client.find_matching_user_product()` | User-food search + tolerance helper | Matches nutrition values with configurable tolerance. |
| `client.cleanup_duplicate_user_products()` | User-food search + product delete | Requires `brand` or a custom predicate so broad cleanup is explicit. |
| `planner.move_day_item()` | Snapshot sync | Live-tested (2026-04-22). Cross-meal fallback included. |
| `planner.replace_day_item_with_custom_item()` | Snapshot sync | Live-tested (2026-04-22). Cross-meal fallback included. |

### Not covered yet

Present in the 4.17.1 app, not wrapped by the library. Paths and payloads for all of them are in
[docs/APP_4_17_1_API_CATALOG.md](docs/APP_4_17_1_API_CATALOG.md).

| Family | Endpoints | Description |
|---|---|---|
| Activities in the planner | `activities[]` in `POST /diet-plan/{id}/days`, `/users/{id}/activities/{activityId}` | Logging workouts into a day. |
| Text / photo / voice AI | `/text-ai/predict…`, `/photo-predictor/…`, `/voice-ai/predict/…`, `/recipes-ai` | Meal estimation; premium and quota-limited, needs the `AI_PROCESSING` consent. |
| Consents | `/v2/consents`, `/consents/{type}/versions/latest`, `/consents/{type}/accept`, `/revoke` | GDPR/AI consents. |
| Label scanner | `/label-scanner/session/…` | Nutrition-label OCR sessions. |
| Intermittent fasting | `/intermittent-fasting/{id}/periods`, `/summary`, `/types` | |
| Habits | `/habits/{id}`, `/users/{id}/habits[/{habitId}]`, `/habits/{id}/good-days` | |
| Menu plans | `/food-proposal/plans`, `/food-proposal/plan/{id}`, `/search/food-proposal` | |
| Food sharing | `/foodshares`, `/foodshares/{hash}` | Shareable meal links. |
| Gamification | `/game/user/profile`, `/game/quests`, `/game/active-days`, `/game/reward-boxes/…`, `/game/store/…` | |
| New-year resolutions | `/user/resolutions`, `/resolutions/…` | |
| Connected apps | `/external-apps/{garmin\|strava\|polar-flow\|huawei/healthkit}/{id}/authorization[/status]` | Third-party sync status/unlink. |
| Product/recipe writes | `PUT/PATCH /products/{id}`, `/products/{id}/barcodes`, `/action/report`, `PUT/DELETE /recipes/{id}` | |
| Reviews | `/reviews/{type}[/{id}]`, `/rating/{type}/{id}` | |
| Billing | `/billing/mobile/info`, `/billing/{id}/callback`, `/billing/restore/{google\|ios}` | In-app purchases. |
| Account | `/users` (sign-up), `/login-demo`, `/login-facebook`, `/users/action/reset-password/send-email`, `/users/{id}/notification-tokens` | |

## 📦 Installation

```bash
pip install fitatu-api
```

If you want the exact local workflow used in this repository:

```bash
pip install -e ".[dev]"
ruff check .
mypy src
pytest -q
python -m build
```

For local development:

```bash
python -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
pytest
```

## 🚀 Quick Start

Get a session first:

```bash
python -m fitatu_api login      # prompts for e-mail and password, writes session_data.json
```

Accounts created with Apple or Facebook have no password; copy the session from the web app
instead, see [docs/GETTING_STARTED.md](docs/GETTING_STARTED.md#2-get-a-session).

```python
from fitatu_api import FitatuApiClient, FitatuAuthContext

session_data = {
    "bearer_token": "...",
    "refresh_token": "...",
    "fitatu_user_id": "123",
}

auth = FitatuAuthContext.from_session_data(session_data)
client = FitatuApiClient(auth=auth)

foods = client.search_food("banan", limit=5)
print(foods[0]["name"])
```

Recipe and catalog helpers:

```python
recipe = client.get_recipe(42)
catalog = client.get_recipes_catalog()
print(recipe["id"])
for category in catalog.get("categories", []):
    print(category["translatedName"], "→", len(category.get("recipes", [])), "recipes")
```

Create or manage a user product:

```python
product = client.create_product(
    name="Meal prep chicken bowl",
    brand="custom",
    energy=640,
    protein=44,
    fat=18,
    saturated_fat=4,
    carbohydrate=72,
    salt=2.1,
)

client.set_product_raw_ingredients(
    product["id"],
    ["rice", "chicken breast", "tomato sauce"],
)
```

Search user foods and clean up duplicates explicitly:

```python
from datetime import date

match = client.find_matching_user_product(
    user_id="123",
    phrase="Meal prep chicken bowl",
    day=date.today(),
    nutrition={"energy": 640, "protein": 44, "fat": 18, "carbohydrate": 72},
    brand="custom",
    tolerance=0.01,
)

cleanup = client.cleanup_duplicate_user_products(
    user_id="123",
    phrase="Meal prep chicken bowl",
    day=date.today(),
    brand="custom",
    keep_product_id=match["id"] if match else None,
)
print(cleanup["deletedCount"], cleanup["deletedProductIds"])
```

Planner facade:

```python
from datetime import date

from fitatu_api import FitatuLibrary

lib = FitatuLibrary(session_data=session_data)
result = lib.add_product_to_day_meal_via_api(
    target_date=date.today(),
    meal_key="breakfast",
    product_id=123,
    measure_id=1,
    measure_quantity=2,
)
print(result["status"])
```

Remove a planner item:

```python
delete_result = lib.remove_day_item_via_api(
    target_date=date.today(),
    meal_key="breakfast",
    item_id="item-or-product-id",
)
print(delete_result["status"])
```

Clear a whole meal (or only one kind of row) in one sync:

```python
lib.remove_day_items_by_kind_via_api(
    target_date=date.today(),
    item_kind="auto",  # auto | normal_item | custom_add_item | custom_recipe_item
    meal_key="snack",
)
```

There is no DELETE route for planner rows. The helpers re-sync the day with `deletedAt` set on the
row (exactly what the app does) and confirm the row is gone after a reload.

Read derived day totals and a normalized day summary:

```python
macros = lib.get_day_macros_via_api(
    target_date=date.today(),
    include_meal_breakdown=True,
)
summary = lib.get_day_summary_via_api(target_date=date.today())

print(macros["result"]["totals"]["energy"])
print(summary["result"]["meals"][0]["items"])
```

Planner item move/replace helpers (live-tested 2026-04-22):

```python
move_result = lib.move_day_item_via_api(
    from_date=date.today(),
    from_meal_key="breakfast",
    item_id="plan-day-diet-item-id",
    to_meal_key="dinner",
)

replace_result = lib.replace_day_item_with_custom_item_via_api(
    target_date=date.today(),
    meal_key="dinner",
    item_id="plan-day-diet-item-id",
    name="Custom dinner row",
    calories=500,
    protein_g=35,
    fat_g=12,
    carbs_g=60,
)

print(move_result["result"]["ok"], replace_result["result"]["ok"])
```

Goals, weight and range summaries:

```python
from datetime import date, timedelta

targets = lib.get_daily_targets_via_api()
print(targets["result"]["energy"], targets["result"]["protein"])

lib.log_weight_via_api(weight=72.4)  # today, keeps sizes already saved for the day
history = client.measurements.get_weight_history("123", from_date=date.today() - timedelta(days=90))

week = client.summary.get_nutrition("123", date.today() - timedelta(days=6), date.today())
print(week["energy"]["current"])

lib.export_diary_via_api(
    from_date=date.today() - timedelta(days=30),
    to_date=date.today(),
    fmt="csv",
    path="diary.csv",
)
```

Favourites and "the usual":

```python
usual_breakfast = client.foods.get_recent("123", "breakfast")[:5]
favourites = client.foods.get_favorites("123")
```

Errors:

```python
from fitatu_api import FitatuApiError, FitatuAuthError, FitatuPlannerSyncError

try:
    client.planner.sync_single_day("123", date.today(), payload, synchronous=True)
except FitatuPlannerSyncError as exc:
    print(exc.results)        # [{"date": ..., "revision": None, "errorMessage": "..."}]
except FitatuAuthError:
    ...                       # 401 even after a refresh: log in again
except FitatuApiError as exc:
    print(exc.status_code, exc.codename)
```

Operational state and export:

```python
state = client.describe_auth_state()
session_snapshot = client.auth.to_session_data()
print(state["lifecycle_state"])
print(session_snapshot["fitatu_user_id"])
```

## 🔌 Headers and hosts

Checked live against `GET /users/{id}/settings-new/{day}` and search, removing one header at a time:

| Header | Required | What it does |
|---|---|---|
| `API-Key: FITATU-MOBILE-APP` | **yes** (403 without it) | Static client id, the same for web and mobile. |
| `API-Secret: PYRXtfs88UDJMuCCrNpLV` | **yes** (403 without it) | Static, shipped in the app. Not a per-user secret. |
| `Authorization: Bearer <jwt>` | **yes** for user routes (401) | From `/login`; the user id is the JWT `id` claim. |
| `APP-SearchLocale` | no, but changes results | Search language; the `locale` query param has the same effect. |
| `APP-Locale`, `APP-StorageLocale` | no | UI language and the user's data locale. |
| `APP-Timezone` | no | e.g. `Europe/Warsaw`. |
| `APP-OS`, `APP-Version`, `APP-UUID`, `API-Cluster` | no | Client identification only. The library sends `WEB` / `4.13.1` / `pl-pl0`; the Android app sends `ANDROID` / `4.17.1` / `dart-<locale><userId>`. Both are accepted. |
| `X-Firebase-AppCheck` | no (today) | The app has App Check wired into every request but it is disabled by remote config. If Fitatu ever enforces it, unofficial clients will stop working. |

There is no request signing (no HMAC, nonce or timestamps).

Hosts: the app talks to `https://<storageLocale>.fitatu.com/api`, where the prefix is the user's
`storageLocale` lowercased with a dash (`pl_PL` → `pl-pl`, `de_DE` → `de-de`, default `en-gb`). The
library defaults to `https://pl-pl.fitatu.com/api`; pass `base_url=` for other locales. The app sends
search to `https://fitatu.com/api`, but the search routes answer the same on the main host.

## 🛠️ Included Scripts

- `example.py`: smallest possible getting-started script
- `demo.py`: broader interactive demo organised into showcase-style categories for auth,
  planner, search, recipes, diagnostics, and session export

Run them from the repo root after preparing a local `session_data.json` file:

```bash
python example.py
python demo.py
```

## 🎬 Demo Preview

```text
Fitatu Library Demo
===================
User ID: 123
Lifecycle: healthy

Categories
----------
[1] Session & Auth
[2] Planner & Settings
[3] Search & Catalog
[4] Diagnostics & Export
[q] Quit
```

Interactive features:

- category-based navigation with guided tour mode
- planner, settings, search, catalog, and diagnostics walkthroughs
- session export preview for reuse in scripts
- endpoint probe summary for quick sanity checks

## 📖 Public API

Low-level:

- `FitatuApiClient`
- `FitatuAuthContext`
- `FitatuTokenStore`

Errors (all subclass `FitatuApiError`):

- `FitatuApiError` (with `status_code`, `body`, `codename`)
- `FitatuAuthError`
- `FitatuRateLimitError`
- `FitatuConnectionError`
- `FitatuPlannerSyncError` (with `results`)

High-level:

- `FitatuLibrary`

Operational tooling:

- `FitatuOperationalStore`
- `FitatuOperationalEvent`

Modules:

- `PlannerModule`
- `UserSettingsModule`
- `DietPlanModule`
- `WaterModule`
- `ActivitiesModule`
- `ResourcesModule`
- `CmsModule`
- `AuthModule`
- `MeasurementsModule`
- `SummaryModule`
- `FoodsModule`

## 📚 Documentation Map

- [docs/GETTING_STARTED.md](docs/GETTING_STARTED.md): installation and first request
- [docs/API_OVERVIEW.md](docs/API_OVERVIEW.md): package entry points and error model
- [docs/COOKBOOK.md](docs/COOKBOOK.md): practical usage patterns
- [docs/DELETE_GUIDE.md](docs/DELETE_GUIDE.md): how planner rows are deleted (and what does not delete them)
- [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md): code layout and design choices
- [docs/STABILITY_MATRIX.md](docs/STABILITY_MATRIX.md): stable vs experimental areas
- [docs/APP_4_17_1_API_CATALOG.md](docs/APP_4_17_1_API_CATALOG.md): every route used by the 4.17.1 app, with payload keys
- [docs/FAQ.md](docs/FAQ.md): short answers for common questions
- [docs/RELEASING.md](docs/RELEASING.md): release workflow for maintainers

## 📁 Project Layout

```text
fitatu_api/
├── src/fitatu_api/
│   ├── __init__.py          # public exports
│   ├── auth.py              # FitatuAuthContext, FitatuTokenStore
│   ├── client.py            # FitatuApiClient (low-level HTTP)
│   ├── planner.py           # PlannerModule (day reads/writes)
│   ├── service_modules.py   # UserSettings, Water, Activities, Measurements, Summary, Foods, ...
│   ├── facade.py            # FitatuLibrary (high-level facade)
│   ├── constants.py         # lifecycle state constants
│   ├── exceptions.py        # FitatuApiError and subclasses
│   ├── operational_store.py # FitatuOperationalStore (SQLite diagnostics)
│   ├── _validation.py       # input validation helpers
│   ├── api_client.py        # re-export shim
│   └── modules.py           # re-export shim
├── tests/
│   ├── conftest.py          # shared fixtures and markers
│   ├── test_client.py       # unit tests (mocked)
│   ├── test_planner.py      # planner unit tests
│   ├── test_facade.py       # facade unit tests
│   ├── test_service_modules.py  # measurements, summary, foods, activities
│   └── test_live_all.py     # live integration tests
├── docs/
├── demo.py
├── example.py
├── pyproject.toml
└── README.md
```

## ⚠️ Stability

This package is based on a reverse-engineered, undocumented API. Fitatu can change endpoints
at any time without notice.

- **Stable:** auth/session helpers, recipe/product helpers, planner day sync flow
- **New in 0.4.0:** measurements, summaries/export, favourites — request shapes checked live,
  less mileage than the rest
- **Deprecated:** helpers built on routes that do not exist (they emit `DeprecationWarning`)

See [docs/STABILITY_MATRIX.md](docs/STABILITY_MATRIX.md) for the full breakdown.

## ✅ Quality Gates

- `src` layout with typed package metadata
- public API contract tests
- linting with `ruff`
- static type checks with `mypy` (`disallow_untyped_defs = true`)
- input validation with `ValueError` on bad arguments
- pre-commit hooks for maintainers
- wheel and sdist builds validated in CI/local build flow

Local verification:

```bash
ruff check .
mypy src
pytest -q
python -m build
```

Or via Hatch scripts:

```bash
hatch run lint
hatch run test
hatch run typecheck
```

## 💻 Compatibility

- Python `3.11+`
- Designed for direct HTTP use with Fitatu session data
- Tested as an installable package via editable installs in local development

## 🗺️ Roadmap

- expand AI agent tooling: typed tool-call wrappers for LLM function-calling
- add more response-shape contract tests for newly discovered endpoints
- add GitHub release automation once the public repository layout is finalised
- expand docs with cookbook-style automation and AI agent scenarios
- increase endpoint coverage: activities in the planner, text/photo AI estimation, fasting, habits
- `FitatuApiClient.from_token_store(path)`: resume a session from saved tokens without a password,
  like `Garmin.login(tokenstore)` in python-garminconnect
- optional mobile header profile (`APP-OS: ANDROID`) and base URL derived from the user's storage locale

## 👨‍💻 Development

```bash
pip install -e ".[dev]"
ruff check .
mypy src
pytest --cov=fitatu_api
python -m build
```

Useful docs:

- [CHANGELOG.md](CHANGELOG.md)
- [docs/GETTING_STARTED.md](docs/GETTING_STARTED.md)
- [docs/API_OVERVIEW.md](docs/API_OVERVIEW.md)
- [docs/COOKBOOK.md](docs/COOKBOOK.md)
- [docs/DELETE_GUIDE.md](docs/DELETE_GUIDE.md)
- [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md)
- [docs/STABILITY_MATRIX.md](docs/STABILITY_MATRIX.md)
- [docs/FAQ.md](docs/FAQ.md)
- [docs/RELEASING.md](docs/RELEASING.md)

## 🐛 Found a Bug?

Open an issue on [GitHub Issues](https://github.com/Jezue/fitatu_library/issues). Please include:

- Python version and OS
- the call that failed and the full traceback
- whether the issue reproduces with a fresh token

PRs are welcome too — just make sure `ruff check .`, `mypy src`, and `pytest -q` all pass locally
before opening one.

## ⭐ If This Library Helped You

Building and maintaining an unofficial API client takes time — reverse-engineering endpoints,
keeping up with app updates, and writing proper tests and docs on top of it all.

If `fitatu-api` saved you time or made your project possible, a GitHub star goes a long way.
It helps other people find the project and tells me the work is worth continuing.

[Leave a star on GitHub →](https://github.com/Jezue/fitatu_library)

Thank you.

## 🔨 Maintainer Tooling

```bash
pre-commit install
pre-commit run --all-files
```

This repository also includes:

- `.editorconfig` for consistent editor defaults
- `.pre-commit-config.yaml` for local quality hooks
