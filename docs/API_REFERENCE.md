# API reference

Everything below is importable from `fitatu_api` (the tool helpers from `fitatu_api.agent`, the
MCP server from `fitatu_api.mcp_server`).
There are two ways in, and a third that is on its way out:

- `FitatuApiClient`: the client. Every call returns data or raises a typed error, never a
  status envelope: the API's own payloads as Fitatu sends them, and the results the library
  builds (planner writes, ...) with snake_case keys (see [Naming](#naming)). Its modules
  (`client.planner`, `client.measurements`, ...) group the routes.
- `FitatuTools`: the agent tool layer over the client, JSON in and out; see
  [AGENTS.md](AGENTS.md).
- `FitatuLibrary` (**deprecated**, removed in 1.0): a facade over the client whose `*_via_api`
  methods never raise. They return `{"status": "ok", "result": ...}`, `"partial"` when the call
  ran but its result has `ok` false (a write the re-read did not confirm, a row that was not
  there, a duplicate cleanup with errors), or `{"status": "error", "operation": ...,
  **describe_error(exc)}`. All calls share one client. Constructing it emits a
  `DeprecationWarning`; it works as it does now until 1.0. For results that never raise use
  `FitatuTools`, whose envelope (`{"ok": true, "data": ...}` / `{"ok": false, "error":
  describe_error(exc)}`) is the library's only one from 1.0 on; otherwise use the client.

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
| `FitatuApiClient.from_session(path=None, **kwargs)` | stable | Load the session file (see the README) and use it as the token store; without `path`, `$FITATU_SESSION_JSON` (the session itself, tokens kept in memory) comes first. |
| `FitatuApiClient.login(email, password, base_url=None, **kwargs)` | stable | Log in with e-mail and password, read the profile's locales, pick the region host. |
| `FitatuApiClient(auth, base_url=None, timeout_seconds=25, retry_max_attempts=3, retry_base_delay_seconds=0.5, token_store_path=None, persist_tokens=True)` | stable | Build a client from a `FitatuAuthContext`. |
| `client.close()`, `with FitatuApiClient...` | stable | Close the pooled HTTP connections. |
| `with client.time_limit(seconds):` | beta | The requests this thread sends in the block end within `seconds` in all: no retry whose wait would leave it less than 2 s, each timeout cut to the time left, no request sent once it is up (`FitatuConnectionError`); `None` for no limit. |
| `client.request(method, path, *, params, json_data, include_auth, allow_refresh_on_401, raw, idempotent)` | stable | One HTTP call with refresh, retries and error mapping; for routes the library does not wrap. |
| `client.refresh_access_token()` | stable | Get a new bearer token for the refresh token (Fitatu returns the same refresh token); returns `describe_auth_state()`, without tokens. Raises `FitatuAuthError` when the session has no refresh token or Fitatu rejects it (400 or 401), another `FitatuApiError` otherwise. |
| `client.describe_auth_state()` | stable | Non-secret snapshot: user id, locales, host, token expiry, whether tokens are persisted. |
| `client.clear_auth(*, clear_token_store=True)` | stable | Forget the tokens and, by default, delete the token store file. |
| `FitatuAuthContext(bearer_token=None, refresh_token=None, fitatu_user_id=None, base_url=None, app_locale="pl_PL", app_search_locale="pl_PL", app_storage_locale="pl_PL", app_timezone="Europe/Warsaw")` | stable | Tokens, user id, host, locales and time zone of a session, as keyword arguments; the client identity fields (`api_key`, `api_secret`, `app_version`, ...) default to `WEB_CLIENT_PROFILE`, with the keys `$FITATU_CLIENT_PROFILE` (a JSON object) overrides. Secrets are hidden from `repr`. |
| `FitatuAuthContext.from_session_data(data)` | stable | Read a session dict, the web app's `localStorage.user` object or a Playwright storage state. |
| `auth.to_session_data(*, include_tokens=False)` | stable | The session as a dict; tokens only when asked. |
| `auth.snapshot()`, `auth.bearer_expires_at` | stable | Non-secret state; the bearer token's expiry from its JWT. |
| `FitatuTokenStore(path)` | stable | JSON file the token pair is merged into (`load`, `save`, `clear`), written 0600. |
| `fitatu-api --version` | stable | Prints the version. |
| `fitatu-api status [--check]` | beta | The session without its tokens: file and permissions (or `$FITATU_SESSION_JSON`), user id, host, locales, bearer expiry, refresh token present; `--check` reads the profile once. Exits 1 without a session or when the check fails. |
| `fitatu-api doctor` | beta | Checks the session file, the refresh token (one refresh), the clock against the new token's `iat`, a profile read, whether a premium plan runs (yes or no) and the web client version (`GET /upgrade-application/WEB/<version>`); prints no token, user id or personal data, nor the library's log lines without `--log-level`; exits 1 when a check failed. |
| `fitatu-api logout [--yes]` | beta | Deletes the session file after asking. |
| `fitatu-api --log-level DEBUG\|INFO\|WARNING\|ERROR COMMAND` | beta | Log the library to stderr from that level (default: `$FITATU_LOG_LEVEL`, else WARNING); given before the command. |
| `fitatu-api login [--email] [-o PATH \| --print] [--base-url URL]` | stable | Command line login that writes the session file; `--print` prints the session JSON instead (for `FITATU_SESSION_JSON`). |
| `fitatu-api import-session [-o PATH \| --print] [--base-url URL]` | stable | Writes the session file from tokens copied out of the web app (JSON on stdin), for accounts without a password; `--print` as for `login`. |
| `fitatu-api tools [--json]` | beta | Lists the agent tools with their kind (read, write, destructive) and the first sentence of their description; `--json` prints `anthropic_tools()`. |
| `fitatu-api call NAME [JSON] [--allow-writes]` | beta | Runs one tool through `FitatuTools.call()` on the session file and prints the envelope as JSON; exits 1 when `ok` is false. Only `READ_ONLY_TOOLS` run unless `--allow-writes`. |
| `fitatu-api export --from DATE --to DATE [--format csv\|xls\|pdf\|txt] -o PATH [--overwrite]` | stable | `summary.export_diary()` written to a new 0600 file; an existing one only with `--overwrite`. An empty range writes nothing and exits 1. Premium only: without Fitatu Premium it prints that in one line and exits 1. |
| `fitatu-api mcp [--read-only] [--demo] [--tools NAME,...] [--call-timeout SECONDS]` | beta | The MCP server, the same as `fitatu-mcp`; for hosts that start the package by its name, such as the MCP Registry entry run with uvx. Needs the `mcp` extra. |
| **Food catalog** | | |
| `client.search_food(phrase, *, locale=None, limit=5, page=1)` | stable | Food search (products and recipes); rows carry the id as `foodId`. |
| `client.get_product_details(product_id)` | stable | A product with its measures (`/products/{id}` only; recipes: `get_recipe`). |
| `client.search_recipes(phrase="", *, max_energy=None, min_protein=None, max_carbohydrate=None, max_cooking_time=None, tags=(), meals=(), locale=None, limit=10, page=1)` | beta | Recipe catalog search: RECIPE hits shaped like `search_food` rows plus `premiumInRecipesCatalog`, `rating`, `cookingTime`, `preparationTime`. Filters are per portion and whole numbers (Fitatu answers 400 to a decimal, so `max_energy` is rounded down); `max_cooking_time` works in two steps (10 to 29 keeps recipes of up to 10 minutes, 30 or more up to 30, less than 10 nothing); `tags` are the catalog's `tag.*` names (e.g. `tag.vegan`) and must all apply; `meals` any of them (checked live 2026-10-15). |
| `client.get_recipe(recipe_id)` | stable | A recipe with ingredients and nutrition (numeric id only). Fitatu serves any user's private recipe by id, with the author's `username` and `userId` (checked live 2026-10-15): the `get_recipe` tool answers `not_found` for another user's. |
| `client.get_recipes_catalog()` | stable | The recipe catalog home (categories with recipes). |
| `client.get_recipes_catalog_category(category_id)` | stable | One catalog category by id or slug. |
| `client.search_user_food(user_id, phrase, day, *, page=1, limit=50)` | beta | Search the user's own products and recipes. Fitatu ignores `phrase` (checked live 2026-10-15): it answers every own food. |
| `client.find_matching_user_product(user_id, phrase, day, *, nutrition, brand=None, ...)` | beta | The user's first product matching brand and nutrition within a tolerance, or `None`. |
| `client.nutrition_values_match()`, `client.product_nutrition_matches()` | stable | Local helpers for comparing nutrition values. |
| `client.normalize_recipe_items(items)` | stable | Local helper: ingredient dicts in the shape `create_recipe` sends. |
| `client.create_product(*, name, energy, protein, fat, carbohydrate, brand=None, ..., measures=None, barcode=None)` | stable | Create a private product of the user's (values per 100 g, or per the first of `measures`; `barcode` stays private). |
| `client.update_product(product_id, product)` | stable | Replace a product the user created with the whole body (`PUT /products/{id}`); a key left out is cleared; answers `newProductCreated` (checked live 2026-10-15). |
| `client.create_recipe(*, name, items, ...)` | stable | Create a recipe from `{"foodId", "measureId", "measureQuantity"}` items. |
| `client.delete_product(product_id)` | beta | Delete a product the user created. Soft, like recipes. |
| `client.update_recipe(recipe_id, *, name, items, ...)` | beta | Replace a recipe the user created with `create_recipe`'s body (`PUT /recipes/{id}`): the app sends the whole recipe, so pass what `get_recipe()` returned for the fields to keep. The app's route and body, not checked live yet. |
| `client.delete_recipe(recipe_id)` | beta | Delete a recipe the user created. Fitatu deletes it softly: `get_recipe()` still returns it, with `"deleted": true`, and it leaves the user's searches. |
| `client.set_product_proposal(product_id, *, property_name, property_value)` | beta | Propose a change to a catalog product; only `rawIngredients` is accepted. |
| `client.set_product_raw_ingredients(product_id, raw_ingredients)` | beta | Propose a product's ingredient list. |
| `client.cleanup_duplicate_user_products(user_id, phrase, day, *, brand=None, predicate=None, keep_product_id=None, dry_run=True, max_deletions=10, ...)` | beta | Delete duplicates of the user's own product; dry run by default. |
| `client.resources.get_food_tags_recipe()` | stable | Recipe tag resources. |
| **Planner (`client.planner`)** | | |
| `planner.get_day(user_id, day)` | stable | The day: `dietPlan` (meals with rows), `activityPlan`, water, toilet, note. |
| `planner.get_meal()`, `list_meal_items()`, `find_meal_item()` | stable | One meal, its rows, or its first row whose name contains a text. |
| `planner.get_day_revisions(user_id, day)` | stable | Revision ids of the day, newest first. |
| `planner.set_note(user_id, day, *, note)` | beta | Replace the day's note (`None` or `""` deletes it), keeping its tags; `ok` from a re-read, `previous` the note before. |
| `planner.add_product_to_day_meal(...)` | stable | Add a product in one of its measures. |
| `planner.add_product_to_day_meal_with_unit(..., strict_measure=True)` | stable | Add a product by unit (`g`, `ml`, `sztuka`, ...), converted to one of its measures. When no measure of the product has a weight (for `g`) or capacity (for `ml`) to convert with, `strict_measure=True` raises; `False` logs `amount` unconverted in the search hit's measure (the product's first one when the hit names none) and says so in `measure_resolution["warnings"]`. Other units always raise when the product lacks them. |
| `planner.resolve_product_measure(*, product_id, requested_amount, requested_unit, ...)` | stable | The measure and quantity a unit converts to, with the strategy used. |
| `planner.add_recipe_to_day_meal(...)` | stable | Add a recipe. |
| `planner.add_custom_item_to_day_meal(...)` | stable | Add a row with a name, kcal and macros. |
| `planner.add_search_result_to_day_meal(...)` | stable | Add one food search hit (product or recipe). |
| `planner.add_foods_to_day_meal(...)` | beta | Add several products and recipes to a meal in one sync; `row_id` makes a repeat safe. |
| `planner.update_day_item(...)` | stable | Change a row's quantity, measure, `eaten`, name or other fields; verified by a re-read. |
| `planner.remove_day_item(...)` | stable | Delete a row with `deletedAt`; verified by a re-read; a missing row is `reason: item_not_found`. |
| `planner.rollback_added_item(...)` | stable | Delete exactly the row an add returned. |
| `planner.remove_day_items_by_kind(...)` | stable | Delete many rows in one sync; explicit scope, `row_ids`, `max_items`, `dry_run`. |
| `planner.classify_day_item_for_removal()`, `list_day_items_for_removal()` | stable | The removal kind of one row, or of every row. |
| `planner.move_day_item(...)` | beta | Move a row to another meal and/or day in one sync. |
| `planner.copy_meal(...)` | beta | Copy a meal, or some of its rows, to another meal and/or day in one sync; `row_id` makes a repeat safe. |
| `planner.replace_day_item_with_custom_item(...)` | beta | Replace a row with a custom row in one sync. |
| `planner.add_activity(...)`, `planner.remove_activity(...)` | beta | Log or delete a catalog activity (workout) in the day sync. |
| `planner.sync_days(user_id, days_payload, *, synchronous=True)` | stable | Raw day sync; raises `FitatuPlannerSyncError` for a rejected day. |
| `planner.sync_single_day(user_id, day, *, day_payload, synchronous=True)` | stable | Raw sync of one day. |
| `planner.normalize_meal_key(meal_type)` | stable | `"second breakfast"` to `second_breakfast`, and so on. |
| **Measurements (`client.measurements`)** | | |
| `measurements.get_day(user_id, day)` | stable | The day's weight and body sizes, or `None` when there are none. |
| `measurements.set_day(user_id, day, *, weight=None, fat_percentage=None, weight_unit=None, size_unit=None, merge=True, **body_parts)` | stable | Save weight and/or sizes, merged with what the day already has. Without `weight_unit` the weight is taken in the unit already stored for that day, else `KG` (`size_unit` likewise, else `CM`); pass `weight_unit="KG"` to be sure. The `log_weight` tool always sends kg. |
| `measurements.delete_day(user_id, day)` | stable | Delete the whole record of a day; safe to repeat. |
| `measurements.get_weight_history(user_id, *, from_date=None, to_date=None, page=1, limit=41)`, `get_weight_chart()` | stable | One page of weight entries `{date, value, difference, direction}`, in the user's weight unit, newest first; the weight chart with targets. Without dates no range is sent and the page holds every entry up to `limit`, however old: there is no default period (checked live 2026-10-14, not recorded). |
| `measurements.get_body_part_history()`, `get_size_summary()` | stable | One body part (or `fat_percentage`) over time, newest first, up to today without `to_date`; size changes across a range. |
| **Summary and export (`client.summary`)** | | |
| `summary.get_nutrition(user_id, from_date, to_date)` | stable | Range summary per nutrient (`current`, `eaten`, `min`, `max`); `current` is the total of every row over the whole range, not a daily average (checked live 2026-10-13), `eaten` only of the rows ticked as eaten (null when none is), `min` and `max` the goals summed over the days (checked live 2026-10-14). |
| `summary.get_nutrient_by_day(user_id, nutrient, from_date, to_date)` | stable | One nutrient day by day: `measures` (every row, planned or eaten; null on a day with nothing logged) with `targets` for energy, `targetRanges` (`min`, `max`) for protein, fat and carbohydrate (checked live 2026-10-14). Premium only for protein, fat and carbohydrate: without Fitatu Premium the API answers 403; energy works on any account (checked live 2026-10-15). |
| `summary.get_top_products(user_id, nutrient, from_date, to_date, *, page=1, limit=20)` | stable | The foods that contributed most of a nutrient: `{list: [{id, name, amount: {current, per100}}], count}`, highest first, numbers as strings (checked live 2026-10-14). Premium only: without Fitatu Premium the API answers 403 (checked live 2026-10-15). |
| `summary.get_activities(user_id, from_date, to_date)` | stable | Burned energy and activity totals for a range. |
| `summary.export_diary(user_id, from_date, to_date, *, fmt="csv")` | stable | The diary as `csv`, `xls`, `pdf` or `txt` bytes; `None` for an empty range. Premium only: without Fitatu Premium the API answers 403, raised as a `FitatuApiError` whose message says the export needs Premium (checked live 2026-10-15). |
| **Water, activities, foods, fasting, habits** | | |
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
| `fasting.get_summary(user_id, from_date, to_date)` | beta | Per fasting day of the range `{date, eatingPeriods, expectedEatingPeriods}`, periods `{date, from, to}`; an empty list without fasting (checked live 2026-10-14). |
| `fasting.get_types(user_id)` | beta | The fasting schemes `{id, symbol, currentlyInUse, rating, ...}`, symbols such as `16_08_hours` or `5_2_days`. |
| `habits.get_catalog(user_id)`, `habits.get_user_habits(user_id)` | beta | The habits on offer `{id, name, translationKey, length, premium}`; the user's habits `{completed, enrolledDate, habit, habitItems}`. |
| `habits.get_good_days(user_id)` | beta | Every date the app's summary marks as a good day, as strings. |
| **Settings (`client.user_settings`, `client.diet_plan`)** | | |
| `user_settings.get_new(user_id, day=None)` | stable | Goals, macro targets, BMR/TMR, meal schema, water settings for a day. |
| `user_settings.get_premium(user_id)` | beta | `{"active", "end_date"}`: whether a premium plan runs today and its latest end (`YYYY-MM-DD`, None without an end); nothing else of the subscriptions reply, which carries prices and transaction ids. |
| `user_settings.get_profile(user_id)` | stable | The user's profile, including locales and timezone. It carries the e-mail, username and ids: not for a model (the `get_profile` tool reads the settings instead). |
| `user_settings.update_water_settings(user_id, *, unit_capacity)` | stable | Set the size of one water unit (a glass) in ml. |
| `user_settings.update_new(user_id, payload)`, `update_profile(user_id, payload)` | experimental | Raw PATCH of settings or of the profile. |
| `user_settings.get()`, `user_settings.get_for_day()` | deprecated | Legacy settings reads, removed in 1.0; use `get_new`. |
| `diet_plan.get_settings()`, `get_meal_schema()`, `get_default_meal_schema()` | deprecated | Legacy diet-plan settings, removed in 1.0; use `user_settings.get_new`. |
| **Agent tools** | | |
| `FitatuTools.from_session(path=None, *, tools=None, call_timeout=45, **client_kwargs)`, `FitatuTools(client, *, tools=None, call_timeout=45)` | beta | The tool layer over a client; `tools` is an allowlist of tool names for `call()`; `call_timeout` (also an attribute) is the seconds one `call()` may take, retries included (`None`: no limit). |
| `FitatuTools.demo(*, tools=None)` | beta | The tools over a small made-up diary kept in memory (`fitatu_api/demo_data.json`): nothing is sent, no session is read, writes last as long as the instance. |
| `FitatuTools.call(name, arguments)` | beta | Run a tool by name within `call_timeout`; never raises, and refuses a tool outside the allowlist. |
| `FitatuTools.<tool>(...)` | beta | Each of the 47 tools in [AGENTS.md](AGENTS.md) as a method that raises. |
| `fitatu_api.agent.TOOLS`, `READ_ONLY_TOOLS`, `anthropic_tools(names=None)`, `openai_tools(names=None)` | beta | Tool definitions with titles and annotations; the names of the read tools; copies of all tools, or of the ones named, in the Anthropic and OpenAI formats. |
| `fitatu_api.agent.SYSTEM_PROMPT` | beta | The recommended system prompt for a model that uses the tools (static English text). |
| `fitatu_api.mcp_server.build_server(open_tools=FitatuTools.from_session, *, read_only=False, tools=None, call_timeout=45, demo=False)`, `main(argv=None)`, `serve(args)` | beta | The tools as an MCP server over stdio (`fitatu-mcp [--read-only] [--demo] [--tools NAME,...] [--call-timeout SECONDS] [--log-level LEVEL]`, `python -m fitatu_api.mcp_server`) with the prompts of `PROMPTS`; logs to stderr (see the README); `open_tools` runs on the first tool call; `tools` serves only those (a warning names the ones `SYSTEM_PROMPT` mentions and it leaves out); each call gets what is left of `call_timeout` since the host sent it (`None`, or `--call-timeout 0`: no limit). Needs the `mcp` extra; see [AGENTS.md](AGENTS.md#mcp). |
| `fitatu_api.mcp_server.output_schema(name)` | beta | A tool's MCP `outputSchema`: the `call()` envelope, with `data` from the method's return type (TypedDicts for the main tools). |
| `fitatu_api.mcp_server.PROMPTS` | beta | The MCP prompts (`podsumowanie_dnia`, `przeglad_tygodnia`, `zaloguj_posilek`): Polish text for the model, English descriptions. |
| **Facade (`FitatuLibrary`, deprecated: removed in 1.0; use `FitatuTools` or `FitatuApiClient`)** | | |
| `FitatuLibrary(session_data, *, base_url=None, timeout_seconds=25, session_path=None)` | deprecated | Facade over a session dict; with `session_path`, refreshed tokens go to that file. |
| `FitatuLibrary.from_session(path=None)` | deprecated | Facade over the session file, written back on refresh. |
| `close()`, `describe_session()`, `clear_session()`, `export_session_context(*, include_tokens=False)` | deprecated | Session helpers; not wrapped like `*_via_api`, so they can raise. |
| `get_day_summary_via_api()`, `get_day_macros_via_api()` | deprecated | Day per meal with `totals` and `eaten_totals`, and the day's `note` and `tags_ids`; or only the totals. |
| `get_daily_targets_via_api()` | deprecated | Energy and macro targets, BMR/TMR, plus the raw settings. |
| `add_product_to_day_meal_via_api()` | deprecated | `planner.add_product_to_day_meal`, or the unit variant with `measure_unit`. |
| `add_recipe_to_day_meal_via_api()`, `add_custom_item_to_day_meal_via_api()`, `add_search_result_to_day_meal_via_api()` | deprecated | The planner adds. |
| `update_day_item_via_api()`, `remove_day_item_via_api()`, `remove_day_items_by_kind_via_api()` | deprecated | The planner update and removals. |
| `move_day_item_via_api()`, `replace_day_item_with_custom_item_via_api()` | deprecated | The planner move and replace. |
| `log_weight_via_api()` | deprecated | `measurements.set_day` with a weight (today by default). |
| `get_weight_history_via_api()` | deprecated | Every weight entry of a range (walks the pages). |
| `get_range_summary_via_api()` | deprecated | `summary.get_nutrition` and `summary.get_activities` for a range. |
| `export_diary_via_api(*, from_date, to_date, fmt="csv", path=None, overwrite=False)` | deprecated | The export as text or base64, or written to a 0600 file. Premium only: without Fitatu Premium a `forbidden` error whose message says so. |
| `search_food_via_api()`, `get_recipe_via_api()`, `get_recipes_catalog_via_api()` | deprecated | Food search, a recipe, the recipe catalog or one category. |
| `get_favorite_foods_via_api()`, `get_recent_foods_via_api()` | deprecated | Favourite foods; foods recently logged in a meal. |
| `search_user_food_via_api()`, `find_matching_user_product_via_api()` | deprecated | The user's own foods. |
| `create_product_via_api()`, `add_user_dish_via_api()` | deprecated | Create a product or a recipe. |
| `delete_product_via_api()` | deprecated | Delete a product the user created. |
| `set_product_proposal_via_api()`, `set_product_raw_ingredients_via_api()`, `cleanup_duplicate_user_products_via_api()` | deprecated | Product proposals and duplicate cleanup (dry run by default). |
| **Errors** | | |
| `FitatuApiError` | stable | Base class: `status_code`, `body`, `codename`; the message is `METHOD /path -> status CODENAME: server message`. |
| `FitatuValidationError` | stable | A bad argument, raised before any request; also a `ValueError`. |
| `FitatuNotFoundError` | stable | The id, meal or row named is not there, in what the library read or as a 404 from the server (`status_code` 404). Not a `ValueError`. |
| `FitatuAuthError` | stable | The session is gone (401 after a refresh, no session file), or `login()` got a wrong e-mail or password; `hint` says what to do instead of logging in again. |
| `FitatuRateLimitError` | stable | 429; `retry_after` in seconds when the server said. |
| `FitatuConnectionError` | stable | No HTTP response (DNS, timeout, reset). |
| `FitatuMaintenanceError` | stable | Fitatu's technical break; `display_from`, `display_to`. |
| `FitatuPlannerSyncError` | stable | The day sync rejected a day (HTTP 202); `results`. |
| `FitatuPremiumRequiredError` | beta | A `FitatuTools` tool refused what the Fitatu app keeps for Premium (the account's `accessControl` lacks it), though the API would serve it: `search_recipes`' filters but `max_energy_kcal`, `get_recipe`, `get_food_measures`, `add_food` and `add_foods` of a premium recipe. `status_code` None. |
| `describe_error(exc)` | stable | Any exception as a JSON-safe dict with `error_type`, `retryable` and `hint`. |

## Naming

**Result keys.** Every dict the library builds has snake_case keys: planner results (`row_id`,
`sync_response`, `added_item`, `removed_id`, `missing_row_ids`, ...), `resolve_product_measure()`,
`list_day_items_for_removal()`, `cleanup_duplicate_user_products()`, `get_premium()`,
`describe_auth_state()`, the tools' `data` and the day summaries. Payloads of the Fitatu API are
passed through unchanged, with the API's camelCase keys, and are named as such: the reads that
return what Fitatu sent (`planner.get_day()`, `get_product_details()`, `search_food()` hits, the
modules' reads) and, inside library results, the rows sent or read (`added_item`, `added_items`,
`added_activity`, `replacement_item`, `delete_marker`, `removed`, `before`, `after`, `item`,
`skipped`), the request and response of the day sync (`sync_payload`, `sync_response`) and the
`updates` and `validation` of `update_day_item()`, which are keyed by the row's API field names
(`measureQuantity`). `tests/test_naming.py` runs the planner and client calls on the fake server and
fails on a camelCase key anywhere else, and on a core result that carries a `status`.

**Argument names.** One rule per layer, with the exceptions named in the table:

| Layer | Day | Meal | An existing row | A new row | Ranges, copies and moves |
|---|---|---|---|---|---|
| `FitatuApiClient` and its modules | `day` | `meal_type` | `item_id` (`item_ids` in `copy_meal`; `remove_day_items_by_kind` takes the existing rows as `row_ids`) | `row_id` | `move_day_item` and `copy_meal`: `from_day` / `from_meal_type` to `to_day` / `to_meal_type`; `from_date` / `to_date` for the summary and measurement ranges |
| `FitatuTools` (and the MCP tools) | `date` | `meal` | `row_id` (`row_ids` for several) | `row_id` | `move_item`: the row's `date` and `row_id` to `to_date` / `to_meal`; `copy_meal`: `from_date` / `from_meal` to `to_date` / `to_meal`; ranges: `from_date` / `to_date` |

## Code layout

| Module | Contents |
|---|---|
| `client.py` | `FitatuApiClient`: HTTP, headers, refresh, retries, error mapping, catalog calls. |
| `auth.py` | `FitatuAuthContext`, `FitatuTokenStore`, session path resolution, JWT decoding, private file writes. |
| `planner.py` | `PlannerModule`: the diary. |
| `service_modules.py` | Measurements, summary, foods, water, activities, fasting, habits, settings, resources. |
| `agent.py` | `FitatuTools`, `TOOLS`, `READ_ONLY_TOOLS`, `SYSTEM_PROMPT`, `anthropic_tools()`, `openai_tools()`. |
| `mcp_server.py` | `fitatu-mcp`: the tools over MCP (stdio); imported only with the `mcp` extra. |
| `_demo.py`, `demo_data.json` | The made-up diary of `FitatuTools.demo()`, read only when it is asked for. |
| `facade.py` | `FitatuLibrary` (deprecated). |
| `exceptions.py` | The error classes and `describe_error()`. |
| `constants.py` | Default host, session path and the client profile (`WEB_CLIENT_PROFILE`). |
| `__main__.py` | The `fitatu-api` command: `login`, `import-session`, `status`, `doctor`, `logout`, `tools`, `call`, `export` and `mcp`. |
| `_summaries.py`, `_shapes.py`, `_validation.py`, `_files.py` | Private helpers: day summaries, reply shapes, argument checks, the 0600 export file. |

The route-level catalog of the Fitatu app, including routes this library does not call, is in
[APP_4_17_1_API_CATALOG.md](APP_4_17_1_API_CATALOG.md).
