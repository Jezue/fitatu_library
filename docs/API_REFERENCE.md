# API reference

Everything below is importable from `fitatu_api` (the tool helpers from `fitatu_api.agent`).
There are three ways in:

- `FitatuApiClient`: the client. Every call returns the API's data (camelCase keys) or raises
  a typed error. Its modules (`client.planner`, `client.measurements`, ...) group the routes.
- `FitatuTools`: the agent tool layer over the client, JSON in and out; see
  [AGENTS.md](AGENTS.md).
- `FitatuLibrary`: a facade over the client whose `*_via_api` methods never raise. They return
  `{"status": "ok", "result": ...}`, `"partial"` when the call ran but its result has `ok`
  false (a write the re-read did not confirm, a row that was not there, a duplicate cleanup
  with errors), or `{"status": "error", "operation": ..., **describe_error(exc)}`. All calls
  share one client.

Conventions: `user_id` is the numeric Fitatu user id (`client.auth.fitatu_user_id`); days are
`date` objects or `YYYY-MM-DD` strings; in the planner, every argument after `(user_id, day)`
is keyword-only (the modules take some positionally, e.g. `water.set_day(user_id, day,
water_consumption_ml)`). Argument mistakes raise `FitatuValidationError` before a request is
sent. The planner's ids and result shapes are in [PLANNER.md](PLANNER.md).

Stability:

- **stable**: the signature is settled for 0.5.x, and where the call reaches the API, its
  request has been checked live: it is in the recorded responses in `tests/fixtures`, it runs
  in the live test suite, or (login) it is how every session is made. Local helpers and the
  error classes are stable when their interface is settled.
- **beta**: tested offline and expected to work, but the request has not been checked live,
  the route is one the current app no longer calls, or the interface is new and may still
  change (the agent tools and the activity writes).
- **experimental**: not yet checked against the API from this library.
- **deprecated**: emits `DeprecationWarning` and will be removed.

Fitatu can change its private API at any time, so "stable" describes this library, not the
service.

| Name | Stability | Description |
|---|---|---|
| **Client and session** | | |
| `FitatuApiClient.from_session(path=None, **kwargs)` | stable | Load the session file (see the README) and use it as the token store. |
| `FitatuApiClient.login(email, password, base_url=None, **kwargs)` | stable | Log in with e-mail and password, read the profile's locales, pick the region host. |
| `FitatuApiClient(auth, base_url=None, timeout_seconds=25, retry_max_attempts=3, retry_base_delay_seconds=0.5, token_store_path=None, persist_tokens=True)` | stable | Build a client from a `FitatuAuthContext`. |
| `client.close()`, `with FitatuApiClient...` | stable | Close the pooled HTTP connections. |
| `client.request(method, path, *, params, json_data, include_auth, allow_refresh_on_401, raw, idempotent)` | stable | One HTTP call with refresh, retries and error mapping; for routes the library does not wrap. |
| `client.refresh_access_token()` | stable | Get a new bearer token for the refresh token (Fitatu returns the same refresh token); returns `{"status", "state"}` without tokens. |
| `client.describe_auth_state()` | stable | Non-secret snapshot: user id, locales, host, token expiry, whether tokens are persisted. |
| `client.clear_auth(*, clear_token_store=True)` | stable | Forget the tokens and, by default, delete the token store file. |
| `FitatuAuthContext` | stable | Tokens, user id, locales, timezone and host of a session (secrets hidden from `repr`). |
| `FitatuAuthContext.from_session_data(data)` | stable | Read a session dict, the web app's `localStorage.user` object or a Playwright storage state. |
| `auth.to_session_data(*, include_tokens=False)` | stable | The session as a dict; tokens only when asked. |
| `auth.snapshot()`, `auth.bearer_expires_at` | stable | Non-secret state; the bearer token's expiry from its JWT. |
| `FitatuTokenStore(path)` | stable | JSON file the token pair is merged into (`load`, `save`, `clear`), written 0600. |
| `fitatu-api login [--email] [-o PATH] [--base-url URL]` | stable | Command line login that writes the session file. |
| `fitatu-api import-session [-o PATH] [--base-url URL]` | stable | Writes the session file from tokens copied out of the web app (JSON on stdin), for accounts without a password. |
| **Food catalog** | | |
| `client.search_food(phrase, *, locale=None, limit=5, page=1)` | stable | Food search (products and recipes); rows carry the id as `foodId`. |
| `client.get_product_details(product_id)` | stable | A product with its measures (`/products/{id}` only; recipes: `get_recipe`). |
| `client.get_recipe(recipe_id)` | stable | A recipe with ingredients and nutrition (numeric id only). |
| `client.get_recipes_catalog()` | stable | The recipe catalog home (categories with recipes). |
| `client.get_recipes_catalog_category(category_id)` | stable | One catalog category by id or slug. |
| `client.search_user_food(user_id, phrase, day, *, page=1, limit=50)` | beta | Search the user's own products and recipes. |
| `client.find_matching_user_product(user_id, phrase, day, *, nutrition, brand=None, ...)` | beta | The user's first product matching brand and nutrition within a tolerance, or `None`. |
| `client.nutrition_values_match()`, `client.product_nutrition_matches()` | stable | Local helpers for comparing nutrition values. |
| `client.normalize_recipe_items(items)` | stable | Local helper: ingredient dicts in the shape `create_recipe` sends. |
| `client.create_product(*, name, brand, energy, protein, fat, carbohydrate, ...)` | stable | Create a product in the Fitatu catalog (values per 100 g). |
| `client.create_recipe(*, name, items, ...)` | stable | Create a recipe from `{"foodId", "measureId", "measureQuantity"}` items. |
| `client.delete_product(product_id)` | beta | Delete a product the user created. |
| `client.set_product_proposal(product_id, *, property_name, property_value)` | beta | Propose a change to a catalog product; only `rawIngredients` is accepted. |
| `client.set_product_raw_ingredients(product_id, raw_ingredients)` | beta | Propose a product's ingredient list. |
| `client.cleanup_duplicate_user_products(user_id, phrase, day, *, brand=None, predicate=None, keep_product_id=None, dry_run=True, max_deletions=10, ...)` | beta | Delete duplicates of the user's own product; dry run by default. |
| `client.resources.get_food_tags_recipe()` | stable | Recipe tag resources. |
| **Planner (`client.planner`)** | | |
| `planner.get_day(user_id, day)` | stable | The day: `dietPlan` (meals with rows), `activityPlan`, water, toilet, note. |
| `planner.get_meal()`, `list_meal_items()`, `find_meal_item()` | stable | One meal, its rows, or its first row whose name contains a text. |
| `planner.get_day_revisions(user_id, day)` | stable | Revision ids of the day, newest first. |
| `planner.add_product_to_day_meal(...)` | stable | Add a product in one of its measures. |
| `planner.add_product_to_day_meal_with_unit(...)` | stable | Add a product by unit (`g`, `ml`, `sztuka`, ...), converted to one of its measures. |
| `planner.resolve_product_measure(*, product_id, requested_amount, requested_unit, ...)` | stable | The measure and quantity a unit converts to, with the strategy used. |
| `planner.add_recipe_to_day_meal(...)` | stable | Add a recipe. |
| `planner.add_custom_item_to_day_meal(...)` | stable | Add a row with a name, kcal and macros. |
| `planner.add_search_result_to_day_meal(...)` | stable | Add one food search hit (product or recipe). |
| `planner.update_day_item(...)` | stable | Change a row's quantity, measure, `eaten`, name or other fields; verified by a re-read. |
| `planner.remove_day_item(...)` | stable | Delete a row with `deletedAt`; verified by a re-read; a missing row is `reason: item_not_found`. |
| `planner.rollback_added_item(...)` | stable | Delete exactly the row an add returned. |
| `planner.remove_day_items_by_kind(...)` | stable | Delete many rows in one sync; explicit scope, `row_ids`, `max_items`, `dry_run`. |
| `planner.classify_day_item_for_removal()`, `list_day_items_for_removal()` | stable | The removal kind of one row, or of every row. |
| `planner.move_day_item(...)` | beta | Move a row to another meal and/or day in one sync. |
| `planner.replace_day_item_with_custom_item(...)` | beta | Replace a row with a custom row in one sync. |
| `planner.add_activity(...)`, `planner.remove_activity(...)` | beta | Log or delete a catalog activity (workout) in the day sync. |
| `planner.sync_days(user_id, days_payload, *, synchronous=True)` | stable | Raw day sync; raises `FitatuPlannerSyncError` for a rejected day. |
| `planner.sync_single_day(user_id, day, *, day_payload, synchronous=True)` | stable | Raw sync of one day. |
| `planner.normalize_meal_key(meal_type)` | stable | `"second breakfast"` to `second_breakfast`, and so on. |
| **Measurements (`client.measurements`)** | | |
| `measurements.get_day(user_id, day)` | stable | The day's weight and body sizes, or `None` when there are none. |
| `measurements.set_day(user_id, day, *, weight=None, fat_percentage=None, weight_unit=None, size_unit=None, merge=True, **body_parts)` | stable | Save weight and/or sizes, merged with what the day already has. |
| `measurements.delete_day(user_id, day)` | stable | Delete the whole record of a day; safe to repeat. |
| `measurements.get_weight_history()`, `get_weight_chart()` | stable | Paged weight entries; the weight chart with targets. |
| `measurements.get_body_part_history()`, `get_size_summary()` | stable | One body part over time; size changes across a range. |
| **Summary and export (`client.summary`)** | | |
| `summary.get_nutrition(user_id, from_date, to_date)` | stable | Range summary per nutrient (`current`, `min`, `max`); for one day `current` is its total, multi-day ranges are not checked yet. |
| `summary.get_nutrient_by_day(user_id, nutrient, from_date, to_date)` | stable | One nutrient day by day, with targets. |
| `summary.get_top_products(user_id, nutrient, from_date, to_date, *, page=1, limit=20)` | stable | Products that contributed most of a nutrient. |
| `summary.get_activities(user_id, from_date, to_date)` | stable | Burned energy and activity totals for a range. |
| `summary.export_diary(user_id, from_date, to_date, *, fmt="csv")` | stable | The diary as `csv`, `xls`, `pdf` or `txt` bytes; `None` for an empty range. |
| **Water, activities, foods** | | |
| `water.get_day(user_id, day)` | beta | Water drunk on a day (web route; the app sends water inside the day sync). |
| `water.set_day(user_id, day, water_consumption_ml)` | beta | Set the day's total in ml; safe to repeat. |
| `water.add_intake(user_id, day, amount_ml)` | beta | Add to the day's total (read, then set; not atomic). |
| `activities.search(phrase, *, page=1, limit=20, locale=None, bmr=None, sex=None)` | stable | Activity catalog search. |
| `activities.get_catalog(*, page, limit, locale, bmr, sex)` | stable | The activity catalog grouped by first letter. |
| `activities.get_recent(user_id, *, page=1, limit=20)` | stable | Recently logged activities. |
| `foods.get_recent(user_id, meal_type)` | stable | Foods recently logged in a meal. |
| `foods.get_recent_own(user_id, *, page=1, limit=50)` | stable | The user's own products and recipes, most recently used first. |
| `foods.get_favorites(user_id, *, phrase=None, page=1, limit=50)` | stable | Favourite products and recipes. |
| `foods.set_favorite(user_id, food_id, *, favorite=True, food_type="PRODUCT")` | experimental | Mark or unmark a favourite (processed asynchronously). |
| **Settings (`client.user_settings`, `client.diet_plan`)** | | |
| `user_settings.get_new(user_id, day=None)` | stable | Goals, macro targets, BMR/TMR, meal schema, water settings for a day. |
| `user_settings.get_profile(user_id)` | stable | The user's profile, including locales and timezone. |
| `user_settings.update_water_settings(user_id, *, unit_capacity)` | stable | Set the size of one water unit (a glass) in ml. |
| `user_settings.update_new(user_id, payload)`, `update_profile(user_id, payload)` | experimental | Raw PATCH of settings or of the profile. |
| `user_settings.get()`, `user_settings.get_for_day()` | deprecated | Legacy settings reads; use `get_new`. |
| `diet_plan.get_settings()`, `get_meal_schema()`, `get_default_meal_schema()` | deprecated | Legacy diet-plan settings; use `user_settings.get_new`. |
| **Agent tools** | | |
| `FitatuTools.from_session(path=None, **client_kwargs)`, `FitatuTools(client)` | beta | The tool layer over a client. |
| `FitatuTools.call(name, arguments)` | beta | Run a tool by name; never raises. |
| `FitatuTools.<tool>(...)` | beta | Each of the 19 tools in [AGENTS.md](AGENTS.md) as a method that raises. |
| `fitatu_api.agent.TOOLS`, `anthropic_tools()`, `openai_tools()` | beta | Tool definitions with annotations; copies in the Anthropic and OpenAI formats. |
| **Facade (`FitatuLibrary`)** | | |
| `FitatuLibrary(session_data, *, base_url=None, timeout_seconds=25, session_path=None)` | stable | Facade over a session dict; with `session_path`, refreshed tokens go to that file. |
| `FitatuLibrary.from_session(path=None)` | stable | Facade over the session file, written back on refresh. |
| `close()`, `describe_session()`, `clear_session()`, `export_session_context(*, include_tokens=False)` | stable | Session helpers; not wrapped like `*_via_api`, so they can raise. |
| `get_day_summary_via_api()`, `get_day_macros_via_api()` | stable | Day per meal with `totals` and `eaten_totals`; or only the totals. |
| `get_daily_targets_via_api()` | stable | Energy and macro targets, BMR/TMR, plus the raw settings. |
| `add_product_to_day_meal_via_api()` | stable | `planner.add_product_to_day_meal`, or the unit variant with `measure_unit`. |
| `add_recipe_to_day_meal_via_api()`, `add_custom_item_to_day_meal_via_api()`, `add_search_result_to_day_meal_via_api()` | stable | The planner adds. |
| `update_day_item_via_api()`, `remove_day_item_via_api()`, `remove_day_items_by_kind_via_api()` | stable | The planner update and removals. |
| `move_day_item_via_api()`, `replace_day_item_with_custom_item_via_api()` | beta | The planner move and replace. |
| `log_weight_via_api()` | stable | `measurements.set_day` with a weight (today by default). |
| `get_weight_history_via_api()` | stable | Every weight entry of a range (walks the pages). |
| `get_range_summary_via_api()` | stable | `summary.get_nutrition` and `summary.get_activities` for a range. |
| `export_diary_via_api(*, from_date, to_date, fmt="csv", path=None, overwrite=False)` | stable | The export as text or base64, or written to a 0600 file. |
| `search_food_via_api()`, `get_recipe_via_api()`, `get_recipes_catalog_via_api()` | stable | Food search, a recipe, the recipe catalog or one category. |
| `get_favorite_foods_via_api()`, `get_recent_foods_via_api()` | stable | Favourite foods; foods recently logged in a meal. |
| `search_user_food_via_api()`, `find_matching_user_product_via_api()` | beta | The user's own foods. |
| `create_product_via_api()`, `add_user_dish_via_api()` | stable | Create a product or a recipe. |
| `delete_product_via_api()` | beta | Delete a product the user created. |
| `set_product_proposal_via_api()`, `set_product_raw_ingredients_via_api()`, `cleanup_duplicate_user_products_via_api()` | beta | Product proposals and duplicate cleanup (dry run by default). |
| **Errors** | | |
| `FitatuApiError` | stable | Base class: `status_code`, `body`, `codename`; the message is `METHOD /path -> status CODENAME: server message`. |
| `FitatuValidationError` | stable | A bad argument, raised before any request; also a `ValueError`. |
| `FitatuNotFoundError` | stable | The id, meal or row named is not there; a `FitatuValidationError`. |
| `FitatuAuthError` | stable | The session is gone (401 after a refresh, no session file), or `login()` got a wrong e-mail or password; `hint` says what to do instead of logging in again. |
| `FitatuRateLimitError` | stable | 429; `retry_after` in seconds when the server said. |
| `FitatuConnectionError` | stable | No HTTP response (DNS, timeout, reset). |
| `FitatuMaintenanceError` | stable | Fitatu's technical break; `display_from`, `display_to`. |
| `FitatuPlannerSyncError` | stable | The day sync rejected a day (HTTP 202); `results`. |
| `describe_error(exc)` | stable | Any exception as a JSON-safe dict with `error_type`, `retryable` and `hint`. |

## Code layout

| Module | Contents |
|---|---|
| `client.py` | `FitatuApiClient`: HTTP, headers, refresh, retries, error mapping, catalog calls. |
| `auth.py` | `FitatuAuthContext`, `FitatuTokenStore`, session path resolution, JWT decoding, private file writes. |
| `planner.py` | `PlannerModule`: the diary. |
| `service_modules.py` | Measurements, summary, foods, water, activities, settings, resources. |
| `agent.py` | `FitatuTools`, `TOOLS`, `anthropic_tools()`, `openai_tools()`. |
| `facade.py` | `FitatuLibrary`. |
| `exceptions.py` | The error classes and `describe_error()`. |
| `constants.py` | Default host, session path and the client profile (`WEB_CLIENT_PROFILE`). |
| `__main__.py` | `fitatu-api login` and `fitatu-api import-session`. |
| `_summaries.py`, `_shapes.py`, `_validation.py` | Private helpers: day summaries, reply shapes, argument checks. |

The route-level catalog of the Fitatu app, including routes this library does not call, is in
[APP_4_17_1_API_CATALOG.md](APP_4_17_1_API_CATALOG.md).
