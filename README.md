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
- 🔍 food search
- 📅 planner day reads and writes
- 🥗 products, user foods, product proposals, and recipes
- 💧 water and activity tracking
- 📊 derived day macros and normalized day summaries
- 🔬 lightweight operational diagnostics

## 🎯 Why This Package Exists

Fitatu has no public API and no officially supported way to integrate with external tools. This
library exists to fill that gap.

The use cases it was built for:

- connecting Fitatu to AI agents (e.g. an LLM that can read or modify your meal plan)
- home automation flows (log meals from a smart device, fill a planner from a meal-prep script)
- personal data pipelines (export planner snapshots, analyse nutrition data offline)
- any scripted workflow that needs to talk to Fitatu programmatically

The API surface was discovered by static analysis of the Flutter AOT binary (`libapp.so`) and
verified against live endpoints. Because Fitatu can change their API at any time without notice,
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
| Experimental planner sync | Move item between day/meal and replace item with a custom row. |
| Tracking/settings | Water, activity catalog, user settings, diet-plan settings, CMS GraphQL. |

### Covered endpoint families

Discovered by static analysis of the Flutter AOT binary (`libapp.so`), related integrations,
and live endpoint checks where available. Experimental rows are called out separately.

| Endpoint | Method | Library call | Notes |
|---|---|---|---|
| `/search/food/` | GET | `client.search_food()` | |
| `/search/food/user/{id}` | GET | `client.search_user_food()` | User-created foods; exposed through `FitatuLibrary.search_user_food_via_api()`. |
| `/products` | POST | `client.create_product()` | |
| `/products/{id}` | DELETE | `client.delete_product()` | Product delete helper. |
| `/products/{id}/proposals` | POST | `client.set_product_proposal()`, `client.set_product_raw_ingredients()` | Used for proposal-backed product fields such as `rawIngredients`. |
| `/product/{id}/{meal}/{day}` | GET | `client.get_product_details()` | |
| `/recipes-catalog` | GET | `client.get_recipes_catalog()` | |
| `/recipes-catalog/category/{id}` | GET | `client.get_recipes_catalog_category()` | `id` must be a string slug, not an integer |
| `/recipes/{id}` | GET | `client.get_recipe()` | |
| `/resources/food-tags/recipe` | GET | `client.get_food_tags_recipe()` | |
| `/users/{id}` | GET | `client.get_user()` | |
| `/users/{id}/settings/{day}` | GET | `client.get_user_settings_for_day()` | |
| `/users/{id}/settings-new` | GET | `client.get_user_settings_new()` | Returns 405 on some account types |
| `/users/{id}/firebaseToken` | GET | `client.user_settings.get_firebase_token()` | |
| `/water/{id}/{day}` | GET | `WaterModule.get_day()` | |
| `/water/{id}/{day}` | PUT | `WaterModule.set_day()`, `WaterModule.add_intake()` | |
| `/activities/` | GET | `ActivitiesModule.get_catalog()` | |
| `/diet-plan/{id}/settings` | GET | `DietPlanModule.get_settings()` | |
| `/diet-plan/{id}/settings/preferences/meal-schema` | GET | `DietPlanModule.get_meal_schema()` | |
| `/diet-and-activity-plan/{id}/day/{day}` | GET/POST | `planner.*` | Full day snapshot sync. |
| `/v2/diet-plan/{id}/days` | GET/POST | `planner.*` | Preferred planner sync path; supports optional `synchronous=true`. |
| `/v2/diet-plan/{id}/day/{day}/{meal}/{item}` | DELETE | `planner.remove_day_item()` | Hard delete with soft-delete fallback |
| `/v2/diet-plan/{id}/day-items/{day}` | GET | `planner.list_day_items_for_removal()` | |
| `/token/refresh` | POST | `client.refresh_access_token()` | Tries known refresh-token payload variants and stores rotated refresh tokens. |
| CMS GraphQL | POST | `CmsModule.graphql()` | |

Derived helpers without a dedicated direct endpoint:

| Helper | Source | Notes |
|---|---|---|
| `FitatuLibrary.get_day_macros_via_api()` | Planner day snapshot | Aggregates `energy`, `protein`, `fat`, `carbohydrate`, `fiber`, `sugars`, and `salt`; optional meal breakdown. |
| `FitatuLibrary.get_day_summary_via_api()` | Planner day snapshot | Returns normalized totals, meals, and item rows for one day. |
| `client.find_matching_user_product()` | User-food search + tolerance helper | Matches nutrition values with configurable tolerance. |
| `client.cleanup_duplicate_user_products()` | User-food search + product delete | Requires `brand` or a custom predicate so broad cleanup is explicit. |
| `planner.move_day_item()` | Snapshot sync | Live-tested (2026-04-22). Cross-meal fallback included. |
| `planner.replace_day_item_with_custom_item()` | Snapshot sync | Live-tested (2026-04-22). Cross-meal fallback included. |

### Not covered (found in binary, not implemented)

These endpoint families exist in the app but are outside the current scope of the library:

| Family | Endpoints | Description |
|---|---|---|
| Body measurements | `~/measurements/chart/weight`, `~/measurements/size/...` | Weight tracking, size measurements |
| Fitness integrations | `~/external-apps/garmin/`, `/strava/`, `/polar-flow/`, `/huawei/healthkit/` | Third-party fitness app sync |
| AI features | `~/photo-predictor/predict`, `~/voice-ai/predict/...`, `~/label-scanner/session/...`, `~/recipe-predictor/predict`, `~/category-to-products/predict` | Photo/voice/label AI estimation |
| AI recipes | `~/recipes-ai/`, `~/recipes-and-user-action/` | AI-generated recipe suggestions |
| Intermittent fasting | `~/intermittent-fasting/` | Fasting schedule management |
| Habits | `~/habits/` | Habit tracking |
| Gamification | `~/game/config`, `~/game/quests`, `~/game/state` | Quests and achievements |
| Food proposals | `~/food-proposal/plan/`, `~/food-proposal/plans`, `~/search/food-proposal` | Meal plan suggestions |
| Food sharing | `~/foodshares/` | Shared meal entries |
| User resolutions | `~/resolutions`, `~/user/resolutions` | Goals and resolutions |
| Billing | `~/billing/mobile/info`, `/restore/google`, `/restore/ios`, `~/sales/purchase/list/v2` | In-app purchases |
| Consents | `~/consents/VOICE_AI_AUDIO_PROCESSING/...` | GDPR/AI consent management |
| Rating | `~/rating/` | Content rating |
| Product ingredients endpoint family | `~/product-ingredients/` | Dedicated ingredient breakdown endpoints. Product `rawIngredients` proposals are covered through `/products/{id}/proposals`. |
| Auth variants | `~/login`, `~/login-facebook` | Alternative login flows |

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
print(cleanup["deleted"])
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
import time

# The backend sync is eventually consistent — wait ~2s after an add
# before removing or updating the same item, or the snapshot may be stale.
time.sleep(2)

delete_result = lib.remove_day_item_via_api(
    target_date=date.today(),
    meal_key="breakfast",
    item_id="item-or-product-id",
)
print(delete_result["status"])
```

Typed removal (different strategy per item kind):

```python
smart_delete = lib.remove_day_item_with_strategy_via_api(
    target_date=date.today(),
    meal_key="breakfast",
    item_id="item-or-product-id",
    item_kind="auto",  # auto | normal_item | custom_add_item | custom_recipe_item
    max_soft_delete_retries=2,
)
print(smart_delete["result"]["resolvedKind"])
print(smart_delete["result"]["attempts"])
```

The planner delete helper first tries the direct DELETE route and, if the backend
responds with 404, falls back to syncing the whole day without that item.

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

print(move_result["result"]["liveTested"])  # True
print(replace_result["result"]["experimental"])  # True
```

Operational state and export:

```python
state = client.describe_auth_state()
session_snapshot = client.auth.to_session_data()
print(state["lifecycle_state"])
print(session_snapshot["fitatu_user_id"])
```

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
- `FitatuApiError`
- `FitatuTokenStore`

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

## 📚 Documentation Map

- [docs/GETTING_STARTED.md](docs/GETTING_STARTED.md): installation and first request
- [docs/API_OVERVIEW.md](docs/API_OVERVIEW.md): package entry points and error model
- [docs/COOKBOOK.md](docs/COOKBOOK.md): practical usage patterns
- [docs/DELETE_GUIDE.md](docs/DELETE_GUIDE.md): detailed guide to item deletion and soft-delete behaviour
- [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md): code layout and design choices
- [docs/STABILITY_MATRIX.md](docs/STABILITY_MATRIX.md): stable vs experimental areas
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
│   ├── service_modules.py   # UserSettings, DietPlan, Water, Activities, Resources, Cms, Auth
│   ├── facade.py            # FitatuLibrary (high-level facade)
│   ├── constants.py         # lifecycle state constants
│   ├── exceptions.py        # FitatuApiError
│   ├── operational_store.py # FitatuOperationalStore (SQLite diagnostics)
│   ├── _validation.py       # input validation helpers
│   ├── api_client.py        # re-export shim
│   └── modules.py           # re-export shim
├── tests/
│   ├── conftest.py          # shared fixtures and markers
│   ├── test_client.py       # unit tests (mocked)
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
- **Experimental:** multi-route fallbacks, some planner write helpers discovered from
  web/mobile traffic

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
- increase endpoint coverage: body measurements, activity sync, AI features

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
