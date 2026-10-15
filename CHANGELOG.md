# Changelog

All notable changes to this project will be documented in this file.

The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and this project
uses [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added

- `skills/fitatu-diary/SKILL.md`: an [Agent Skills](https://agentskills.io) skill for Hermes
  Agent and other hosts that read the format: how to set up `fitatu-mcp` (Hermes `config.yaml`,
  Claude Code), the rules for using the tools well, common tasks, free vs Premium accounts and
  what to do on each error. A test checks it against the format and that it names only real
  tools.
- `catering_sync.py` (in the repository, not the package) copies a catering menu into the diary:
  a JSON list or a CSV file of dishes (date, meal, name, and kcal with macros, or grams with a
  barcode or a search phrase). Each dish's row id is made from its date and name, so a second
  run skips what is already logged instead of logging it twice; `--dry-run` shows what it would
  log. docs/PLANNER.md says how to write an adapter for a catering provider's export. Checked
  live (2026-10-15): Fitatu takes any UUID of version 1 as a row id, also one made from a hash,
  and row ids belong to one day.

## [0.5.1] - 2026-10-15

### Fixed

- The Claude Desktop bundle found no session with its default setting: Claude Desktop passes the
  default `${HOME}/.fitatu/session.json` without expanding `${HOME}`. The bundle's entry point
  expands it (and falls back to `~/.fitatu/session.json` for an empty or unexpanded value), and
  `resolve_session_path()` now expands variables in `$FITATU_SESSION` too. The
  `fitatu-0.5.0.mcpb` attached to the v0.5.0 release is rebuilt with the fix.
- `get_progress` and `get_report` failed with a 403 on an account without Fitatu Premium: the
  macros' day-by-day route they read is Premium-only (checked live 2026-10-15). When it is
  refused, they read each day's own range summary instead, which every account may read and
  which gives each day's goals (a macro's as a range on a free account) and its `eaten`
  too; `FitatuTools` remembers the refusal for 10 minutes and goes straight to it on later calls
  (a `get_top_foods` that succeeds ends it, so a plan bought meanwhile shows). Without
  Premium this covers at most 31 days (a request per day), and a longer range is a validation
  error that points to `get_range_summary`; so do `get_progress`'s description and its error
  over 62 days, which named only `get_report`. `get_report` gives `top_foods` null with a
  `top_foods_note` instead of failing, as the top foods are Premium-only too.
- `fitatu-api export` on an account without Fitatu Premium printed the API's 403 and a hint that
  listed three possible causes. The diary export is Premium-only (checked live 2026-10-15), so it
  now says that in one line; `summary.export_diary()` and `FitatuLibrary.export_diary_via_api()`
  add it to the error's message.

### Changed

- The tool descriptions, `SYSTEM_PROMPT`, the `przeglad_tygodnia` MCP prompt (the top foods only
  with Premium) and the docs say what needs Fitatu Premium: among the
  tools only `get_top_foods`; in the client `summary.get_nutrient_by_day()` for the macros,
  `summary.get_top_products()` and `summary.export_diary()`. README and docs/AGENTS.md have a
  "Free and Premium accounts" section. `search_recipes` no longer says a premium catalog recipe
  may be unreadable on a free account: its details read fine there (checked live 2026-10-15).
- `scripts/check_contract.py` no longer reports drift on an account without Premium: it skips
  the Premium-only routes there (`skipped (premium)`, from `user_settings.get_premium()`), and a
  key only some accounts' replies have, such as a meal's `recommendedPercent` in the day GET, is
  not drift when it is missing.
- The live tests skip what needs Premium on an account without it ("needs Fitatu Premium"), check
  that `get_top_foods` is refused there, and check on any account that `get_progress` and
  `get_report` give goals, the latter with `top_foods` null and a note without Premium.

## [0.5.0] - 2026-10-14

The release that makes the library usable from an agent. The planner is rebuilt on what the day
sync endpoint really does, pinned by recorded API responses; sessions live in one file that the
client keeps up to date; every failure is a typed, explained error; and `FitatuTools` exposes
the diary as tools. 0.5.0 is the first version on PyPI (0.1.0 to 0.4.0 were only ever in git,
untagged), so unused and misleading surface was removed instead of deprecated. See "Upgrading
from 0.4" at the end of this section.

### Added

- `FitatuTools` (`fitatu_api.agent`): 40 diary tools with JSON Schema inputs, descriptions for
  language models and `readOnlyHint` / `destructiveHint` / `idempotentHint` annotations;
  `TOOLS`, `READ_ONLY_TOOLS`, and `anthropic_tools()` and `openai_tools()` (all tools or the ones
  named). `FitatuTools(client, tools=...)` and `from_session(tools=...)` take an allowlist:
  `call()` refuses any other tool, which a model can name although it was not offered, and its
  error lists only the allowed ones; `fitatu-mcp --read-only` serves `READ_ONLY_TOOLS`. `call()` checks the arguments (array
  items and dates included) before anything is sent and never raises; a meal or date it refuses
  says where the right one comes from (`get_day`'s `meal_name`, `get_today`); `add_food` refuses a `measure_id`
  the food does not have before it writes, since the day sync accepts any and logs a wrong
  amount; an add without a `row_id` gets one, and when an add-type call (`add_food`,
  `add_foods`, `add_custom_item`, `copy_meal`, `add_activity`) fails in a way that may still have
  saved it (network, 5xx, 429, session, maintenance) the error carries that `row_id` as its own
  field, `retry_row_id` (also when the caller gave it), and the hint names it, so the add can be
  repeated safely without parsing text. Every `call()` ends within `call_timeout` seconds (45 by
  default), retries and their waits included, so a slow Fitatu gives the model that error
  envelope before an MCP host gives up on the call; `fitatu-mcp --call-timeout` sets it (0: no limit) and
  counts the time a call waited behind others. `FitatuApiClient.time_limit(seconds)` is the
  block it uses
- Day notes: `get_day` (and `day_summary`) returns the day's `note` and `tags_ids`, and the
  `set_note` tool (`planner.set_note()`) replaces the note or deletes it with `""`, keeping its
  tags and the rest of the day; it returns the `previous` note (`""` when there was none), to put it back. A partial day
  sync keeps the note, its tags and the water (checked live). `SYSTEM_PROMPT` says note text is
  the user's data, not instructions
- `search_recipes` tool (`client.search_recipes()`): the recipe catalog searched by words and/or
  kcal per portion, for meal ideas such as "a dinner under 500 kcal"; the hits can be logged with
  `add_food` and say which recipes are premium
- Intermittent fasting and habits, read only: the `get_fasting` tool (each fasting day's eating
  periods next to the expected windows, and the scheme from the settings), `get_habits` (the
  user's habits with the days ticked, and the ones on offer) and `get_good_days` (the app's good
  days, filtered by date here), over the new `client.fasting` (`FastingModule`) and
  `client.habits` (`HabitsModule`). Nothing writes fasting periods or ticks habits
- `update_recipe` tool (`client.update_recipe()`, beta, not checked live yet): change the name,
  ingredients, servings or description of one of the user's own private recipes, with the
  ownership check of `delete_recipe` and the ingredient checks of `create_recipe`; it sends the
  whole recipe back, as the app does, and returns the `previous` values that undo it
- `get_shopping_list` tool: a shopping list for up to 14 days, worked out from the rows planned
  and not ticked as eaten, as Fitatu has no route for it: products summed per product and
  measure (with grams where the rows give them), planned recipes added as their ingredients for
  the portions planned, custom rows listed as they are
- `user_settings.get_premium()`: whether a premium plan runs today and its end date, and nothing
  else of `GET /users/{id}/subscriptions`, which also carries prices and store transaction ids.
  `fitatu-api doctor` says whether one runs (not until when), and a tool call that gets a 403 from the API reads it once to
  say in the `hint` whether this is a premium feature the account lacks or, with premium, an AI
  quota or API key problem. It is not a tool
- `get_body_measurements` tool: how body sizes and fat percentage changed over a range (first
  and last value and the difference per body part, parts with nothing logged left out), and
  `set_favorite` tool: add a food to the favourites or remove it (applied asynchronously by the
  server, so `get_favorite_foods` may lag a moment)
- `get_progress` tool: each day of a range against the user's goals for energy, protein, fat and
  carbohydrate (`logged`, counting planned rows too and null on a day with nothing logged; the
  `target`, or a macro's `target_min`/`target_max`; `left`, the goal minus `logged`, a macro's
  from `target_max`) and range `totals` with `eaten`, only the rows ticked as eaten; five
  requests for any range. It answers "what is left today", "did I hit protein" and how each day
  went in one call; `get_range_summary` also returns `eaten_nutrition` next to `nutrition`
- `copy_meal` tool: copy a meal, or the rows named in `row_ids`, to another meal and/or day in
  one write ("the same breakfast as yesterday", "plan this lunch for tomorrow"), with `eaten`
  set on every copy or kept per row; rows of types it cannot rebuild are returned in `skipped`.
  Like an add, it gets a `row_id` when it has none, and a failure that may have saved the copies
  names that `row_id` in its hint, since the copies' ids are derived from it
- `add_foods` tool: log several catalog foods in one meal with one write ("two eggs, bread and
  coffee for breakfast"). Every item is checked like an `add_food` first, measures included, so
  one wrong item logs nothing and its error names it (`items[1]: ...`); `eaten` is true by
  default and sets every row. Like the other adds it gets a `row_id` when it has none, and the
  rows' ids are derived from it, so a repeat with the hinted `row_id` logs each food once
- `find_by_barcode` tool: the catalog product with a barcode, in the shape of a search hit. The
  search finds a product by its EAN when the digits are the phrase, but it matches names too,
  so the first product hit counts only when its details carry that `barcode` (one more
  request); otherwise the result is `not_found` and the model searches by name. `search_food`'s
  description sends barcodes there
- `log_measurements` tool: save body sizes (neck to biceps) and fat percentage for a day,
  replacing only the values given and keeping the day's others and its weight. Sizes are in the
  user's size unit, read from the profile, since a day without a record would otherwise be
  labelled cm
- `get_top_foods` tool: the foods that gave the most of a nutrient over a range (energy by
  default), with the amounts as numbers where the server sends strings, and no id, since the
  server does not say whether it is a product's, a recipe's or a custom row's. `get_report`
  tool: a weekly or monthly report in one call (8 requests): per nutrient the range's logged and
  eaten totals and the daily average of the days with anything logged next to the average daily
  goal, the first and last weigh-in of the range and the change, and the top 5 foods by energy
  and by protein. `get_weight_history`'s description says what was checked live: newest first,
  and without dates every entry up to `limit`, however old
- private recipes: `create_recipe` tool saves a recipe from catalog products, always private
  (`shared: false`), each ingredient checked like an `add_food` item first (products only: the
  app sends nothing else, and a recipe id would name an unrelated product), and returns the
  portion measure with the kcal and macros of one serving, read back after the create, for
  `add_food`; `get_my_foods` tool lists the user's own recipes and products as search hits;
  `delete_recipe` tool deletes a recipe only when its details say it is private, not shared and
  the session user's, and treats one already (softly) deleted as done. A `create_recipe` that
  fails after its POST may have been sent (anything but `validation`, `not_found` or
  `rate_limit`) is not `retryable` and its hint says to look in `get_my_foods` first, since a
  repeat would make a second recipe
- `get_today` tool: today's date, time zone and weekday in the account's time zone (the
  session's `app_timezone`, Europe/Warsaw when it has none; the host's clock only when that zone
  cannot be loaded, e.g. on Windows without `tzdata`), so a model resolves "today" and
  "yesterday" without the host putting a date in the prompt; every date argument's description
  points to it
- tools: `get_water` also returns the day's goal (`target_ml`) and the glass size (`glass_ml`, null
  when the settings say 0); `get_food_measures` gives the protein, fat and carbohydrate of each
  measure next to its energy (from the food's details: per 100 g or ml for a product, per portion
  for a recipe; null when the measure's weight does not give its own energy back); food hits
  carry `times_used` and `favorite`; `get_day` leaves out the account id, which would otherwise
  reach the model provider with every result; `add_food` returns the measure the
  row was stored in (`measure_id`, `measure_name`, `measure_quantity`), since grams of a food
  without a gram measure become a count of another one; `remove_items` lists each row it removed
  (or would, in a dry run) with its meal, name, food, measure, quantity, kcal, macros and `eaten`, not
  only its id, and `update_item` returns the row's `previous` quantity and `eaten`, so a model
  can show what goes and undo a change (AGENTS.md and `SYSTEM_PROMPT` say how); `add_custom_item` needs only the
  kcal (protein, fat and carbohydrate default to 0, and the model is told to estimate them); the
  descriptions say that the outcome of a write is `data.ok`, not the envelope's `ok` (every
  write returns one, `log_weight` and `set_water` included), that meal keys are slots the user
  renames (match `get_day`'s `meal_name`), that `totals` and `get_range_summary` count planned
  rows while `eaten_totals` does not, that a `get_day` row's `product_id` or `recipe_id` is the
  `food_id` for `add_food`, and that food is logged with `eaten` true unless the user plans ahead
- `fitatu_api.agent.SYSTEM_PROMPT`: the recommended system prompt (read before writing, match the
  user's own meal names, what the user ate and what is left (`get_progress`: its `eaten`, or
  `logged` when no row is ticked as eaten, and each day's `left`), `data.ok`, `preview_removal`, a direct
  request as the user's consent (the host confirms destructive calls, the model asks only about
  changes it inferred), retrying adds and copies with the error's `retry_row_id`, estimated macros, units),
  under 4000 characters (Claude Code cuts server instructions at 4096) and with a first line
  that names the domain in English and Polish; the README and AGENTS.md loops use it
- `preview_removal` tool: what `remove_items` would delete (names, meals, amounts), without
  deleting anything. It only reads, so an MCP host asks once about a removal instead of twice
  (`remove_items` with `dry_run` was destructive to the host); `dry_run` stays for callers that
  use it
- Bounded results: every `limit` has a maximum (50; 41 for `get_weight_history`, one page as
  the app asks), which `call()` checks with the other bounds of the schema, and `get_progress`
  covers at most 62 days, with an error that points to `get_report` for longer ranges (a year of
  daily entries is ~130k characters, past what hosts pass to the model)
- `fitatu-mcp` (`fitatu_api.mcp_server`, also `python -m fitatu_api.mcp_server`; extra `mcp`,
  `pip install "fitatu-api[mcp]"`): the tools as an MCP server over stdio for Claude Desktop,
  Claude Code and other MCP hosts, with `SYSTEM_PROMPT` as its instructions. Every result is the
  `call()` envelope as JSON text and `structuredContent`, with `isError` when `ok` is false;
  the session file is opened on the first call, so a missing one is an `auth` error the model
  can relay; calls run one at a time; `--read-only` serves only the read tools. Every tool has a
  short human `title` in `TOOLS` (e.g. "Read a diary day"), which the server sends as the tool's
  title and `annotations.title` for hosts to show; `anthropic_tools()` and `openai_tools()` leave
  it out, since those APIs reject unknown keys. Setup in docs/AGENTS.md. `fitatu-api mcp
  [--read-only]` runs the same server, for hosts that start the package by its own name, such as
  the MCP Registry entry run with uvx. The server names itself (title "Fitatu", a description,
  the repository as its website) and offers three Polish prompts, which hosts show as slash
  commands: `podsumowanie_dnia`, `przeglad_tygodnia` and `zaloguj_posilek`, each listed only
  when the tools it names are served. `--tools NAME,...` serves only those tools (a warning
  names the ones the instructions mention and it leaves out). Tools carry `openWorldHint`, true
  only for the shared catalog searches, and `delete_recipe`, which cannot be undone, carries
  `_meta["anthropic/requiresUserInteraction"]`, so Claude Code asks before every call of it.
  Every tool has an `outputSchema` for the envelope, with `data` in the shape of its return
  type (`DaySummary`, `DailyTargets`, `FoodHit`, `AddResult`, `RemoveResult` for the main
  tools; `output_schema(name)`); the tests run every tool through the SDK client, which checks
  results against it, and the live suite checks the read tools' results
- `FitatuTools.demo()` and `fitatu-mcp --demo`: the tools over a small made-up diary kept in
  memory (a 4 KB dataset in the package: foods, recipes, activities, three days of meals up to
  today, weights, water and goals), to try the server without a Fitatu account. Nothing is sent
  anywhere and writes last as long as the process; the instructions tell the model the diary is
  made up
- `scripts/eval_agent.py`: 29 tasks in Polish and English that a Claude model runs against the
  demo diary (`ANTHROPIC_API_KEY`, `claude-opus-5-5` by default, `--model` and `--effort`),
  scored by the tools it calls, their arguments and order, the diary afterwards and the numbers
  in its answer; it prints the score and how to run it when there is no key. Not run in CI
- `mcpb/` and `scripts/build_mcpb.py`: a Claude Desktop bundle (`dist/fitatu-<version>.mcpb`,
  MCPB manifest 0.4, server type `uv`) with the session file and a read-only checkbox as its
  settings; it installs the released `fitatu-api[mcp]` from PyPI and is attached to the GitHub
  release. The manifest validates against the bundle schema; installed through the Claude Desktop
  UI (after the fix below) it starts, finds the session and answers tool calls
- `describe_error()`: any exception as a JSON-safe dict with `error_type`, `retryable` and a
  `hint`, used by `FitatuTools` and `FitatuLibrary`; a 429 without `Retry-After` gets a hint to
  wait a few seconds and retry once, and a 403 whose body is an HTML page (a firewall such as
  Cloudflare, not the API) a hint that says so instead of premium or the API key. A `TypeError`
  is `unexpected`, not `validation`: `call()` checks the arguments first, so one raised inside
  the library is a bug
- the session file: `fitatu-api login` / `python -m fitatu_api login` (reads `FITATU_EMAIL` /
  `FITATU_PASSWORD`, `-o`, `--base-url`) writes `~/.fitatu/session.json` (or `$FITATU_SESSION`,
  alias `$FITATU_SESSION_PATH`), and `FitatuApiClient.from_session()`,
  `FitatuLibrary.from_session()` and `FitatuTools.from_session()` load it and write refreshed
  tokens back. The session carries the region host and the account's locales (schema 1.1);
  `login()` reads them from the profile
- `fitatu-api status` describes the session without sending anything and without tokens (the
  file and its permissions or `$FITATU_SESSION_JSON`, user id, host, locales, bearer expiry,
  whether there is a refresh token, the client version), and `--check` reads the profile once;
  `fitatu-api doctor` checks the setup with Fitatu (the file's permissions, the refresh token by
  refreshing once, the clock against the fresh token's `iat`, a profile read, whether a premium
  plan runs, and whether the web app version the library sends is still the newest, via `GET /upgrade-application/WEB/<version>`)
  and prints no token or personal data, so the output fits a bug report; `fitatu-api logout`
  deletes the session file after asking (`--yes` without); `fitatu-api --version`
- `fitatu-api tools` lists the agent tools (name, read / write / destructive, the first sentence
  of the description; `--json` prints `anthropic_tools()`); `fitatu-api call NAME [JSON]` runs one
  through `FitatuTools.call()` on the session file and prints the envelope as JSON, exiting with 1
  when `ok` is false, and runs only `READ_ONLY_TOOLS` unless `--allow-writes`; `fitatu-api export
  --from DATE --to DATE --format csv|xls|pdf|txt -o PATH` saves the diary export to a new 0600 file
  and replaces an existing one only with `--overwrite`
- `FITATU_SESSION_JSON`: the session JSON itself, which `from_session()` without a path takes
  before any file and whose refreshed tokens stay in memory, for containers, cron jobs and
  remote machines; `fitatu-api login --print` and `import-session --print` print that JSON to
  stdout instead of writing a file, with a warning on stderr that it is a credential.
  `FITATU_CLIENT_PROFILE` (a JSON object) overrides keys of `WEB_CLIENT_PROFILE`, so a change of
  the web app's version or key can be followed without waiting for a release
- `fitatu-api import-session` for accounts without a password (Apple or Facebook sign-in): it
  reads the tokens copied from the web app's `localStorage.user` (or a session file) on stdin,
  checks both with Fitatu by refreshing the bearer once, fills in the locales, timezone and host
  like `login()` and writes only those fields to the session file.
  `FitatuAuthContext.from_session_data()` reads that `localStorage.user` object directly
- the bearer token is refreshed 60 seconds before it expires, one refresh at a time, and a pair
  another process saved to the session file is picked up; `bearer_expires_at` in
  `describe_auth_state()`. A pair of another account (someone logged in to another account with
  the same file) is not picked up, and the client stops writing to that file, so it does not
  overwrite that login. When a token fresh from Fitatu already looks expired (the computer's
  clock runs fast), the client stops refreshing ahead of expiry, which would otherwise precede
  every request, and logs one warning; a 401 still refreshes
- `FitatuValidationError` (also a `ValueError`), `FitatuNotFoundError` (a meal, row or id that
  is not there, found in what the library read or answered 404 by the server; a `FitatuApiError`,
  not a `ValueError`) and
  `FitatuMaintenanceError` (Fitatu's `technical-break` header); `FitatuRateLimitError.retry_after`.
  Error messages say `METHOD /path -> status CODENAME: server message`
- planner: an optional `row_id` (UUID v1) on every add, so a repeated add updates the row
  instead of logging it twice; `row_id` in the results of adds, moves, replacements and
  activity writes; `add_activity()` and `remove_activity()` for workouts;
  `remove_day_items_by_kind(all_meals=, row_ids=, dry_run=)` with `missing_row_ids`, and its
  `targets` (each with the meal's `meal_name`) in real runs too, not only in dry runs;
  `copy_meal()` copies a meal, or some of its rows, to another meal and/or day in one sync,
  each row rebuilt in the write shape of an add, with ids derived from one `row_id` so a
  repeated copy writes the same rows again; `add_foods_to_day_meal()` adds several products
  and recipes to a meal in one sync, every item checked first and its ids derived from one
  `row_id` the same way
- dates as `YYYY-MM-DD` strings in every method that takes a day
- `request(idempotent=True)`; the client keeps one pooled HTTP session, and `FitatuLibrary` and
  `FitatuTools` are context managers like the client
- `cleanup_duplicate_user_products(dry_run=True, max_deletions=10)`
- `client.delete_recipe()`: delete a recipe the user created. Fitatu deletes it softly (checked
  live): `get_recipe()` still answers, with `"deleted": true`, and the recipe leaves the user's
  searches; the live tests' cleanup check accepts that as well as a 404
- day summaries report `eaten_totals`, the day's activities, each row's food type and its
  `row_id`, with quantities and nutrition rounded to 2 decimals (the API sends float noise such as
  16.799999999999997)
- `FitatuLibrary.search_food_via_api()`; `export_diary_via_api(overwrite=)`
- `tests/fixtures/api_recording.json` (real, scrubbed API responses) with
  `scripts/record_fixtures.py`, a fake server built from it and a test that replays it
- `scripts/check_contract.py`: reads every route the library reads once on the live API and
  compares the structure of each reply (keys, value types, list items) with the recording, or
  with a neutral example in `tests/fixtures/contract_shapes.json` for routes the recording lacks.
  It prints route and key names only, never values, and exits 1 when a key is gone, a type
  changed or a route fails. The *Live contract* workflow runs it and the read-only live tests
  (`-k ReadOnly`) every Monday with a throwaway account's session from the `FITATU_SESSION_JSON`
  secret, and skips without it
- docs: `docs/AGENTS.md`, `docs/PLANNER.md`, `docs/API_REFERENCE.md`; the README covers the
  session file, headers, retries, logging and testing. AGENTS.md has loops for Anthropic and
  OpenAI that run only the offered tools, confirm destructive calls (not `dry_run` previews)
  and stop on a cut answer; sections on the system prompt, long-running hosts and trying the
  tools offline against `tests/fake_server.py`; and an error table that says whether the host
  or the model acts
- releases: pushing a tag `vX.Y.Z` runs lint and tests, builds the package and publishes it to
  PyPI with trusted publishing (`.github/workflows/release.yml`), after checking the tag against
  `fitatu_api.__version__`, which is now the only place the package version is set (pyproject.toml
  reads it); then it lists the server in the MCP Registry as `io.github.Jezue/fitatu-api`
  (`server.json`, run with uvx). The one-time PyPI and GitHub setup is in docs/RELEASING.md
- package metadata: the SPDX licence expression `MIT`, `Typing :: Typed` (the wheel ships
  `py.typed`), Python 3.14, keywords and Documentation / Source links; on Windows the package
  depends on `tzdata`, since `zoneinfo` has no time zone database there and `get_today` needs
  the account's zone
- CI runs the tests on Linux with Python 3.11 to 3.14 and on macOS and Windows, with the
  workflows' actions pinned to commits and updated weekly by Dependabot; `SECURITY.md` (private
  reports through GitHub, and never posting the session file), `CONTRIBUTING.md`, and issue and
  pull request templates that ask for the version and the error result, not the session
- a compatibility policy for the library and the agent tools (docs/RELEASING.md), and
  `tests/fixtures/tool_contract.json`, a snapshot of every tool's name, parameters, required
  arguments and annotations that fails the tests when one changes by accident
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
- a search hit that names no measure is no longer logged in measure id 1, which the day sync
  takes for any food and stores the quantity in: `add_search_result_to_day_meal()` without
  `measure_unit` raises `FitatuValidationError`, and the `strict_measure=False` fallback uses
  the product's first measure instead
- `get_product_details()` reads `/products/{id}` only and no longer falls back to
  `/recipes/{id}`: product and recipe ids overlap, so that could return an unrelated recipe. A
  recipe id now gives a 404 or the unrelated product with that number; use `get_recipe()`
- removal results are simpler; a missing row returns `ok: False` with
  `reason: "item_not_found"` and a listing of the day's rows; update, move, replace and
  classify raise `FitatuNotFoundError` for it
- `add_recipe_to_day_meal()` sends the row without name or nutrition (the server fills them in
  from `recipeId`) and no longer fetches recipe details first; `added_item` is the row as sent
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
- every result the library builds has snake_case keys: the planner's write and removal results,
  `resolve_product_measure()`, `classify_day_item_for_removal()`, `list_day_items_for_removal()`
  and `cleanup_duplicate_user_products()` named their keys in camelCase (`rowId`, `syncResponse`,
  `missingRowIds`, ...), next to the snake_case of the summaries and tools. Fitatu's own payloads
  inside them (the rows sent or read, `sync_response`) are passed through unchanged. The rule and
  the per-layer argument names are in API_REFERENCE.md ("Naming"); `FitatuTools` returns what it
  did before, while `FitatuLibrary` passes planner results through, so its `result` has the new
  keys. The full list is under "Upgrading from 0.4"
- `refresh_access_token()` returns `describe_auth_state()` and raises instead of returning
  `{"status": "ok" | "error", "state", ...}`: `FitatuAuthError` when the session has no refresh
  token or Fitatu rejects it (a 400 too, which was a plain `FitatuApiError`), the failure's own
  `FitatuApiError` subclass otherwise. `FitatuApiClient` and its modules never return a status
  envelope now; `fitatu-api doctor` and `import-session` use it
- a `Retry-After` of up to 30 s is honoured; a longer one raises at once
- 520 to 524 (Cloudflare could not reach the API or got no answer in time) are retried like 502
  to 504
- a 404 from the server raises `FitatuNotFoundError` (still a `FitatuApiError`), so
  `except FitatuNotFoundError` catches a missing id whether the library or the server found it
- `water.set_day()`, `measurements.set_day()`, `measurements.delete_day()` and
  `foods.set_favorite()` are retried like reads; writes return `{}` for an empty reply, and a
  read with an unexpected shape raises instead of returning `{"raw": ...}`
- `measurements.set_day()` normalises and checks units and values, and `None` means "not given";
  `measurements.delete_day()` treats a 404 as already deleted
- `activities.get_catalog()` and `search()` send the session's search locale by default
- request logs moved to DEBUG, except `fitatu.request.error` for a request that failed for good
  on a network error, a 429 or a 5xx, which is a WARNING; a `fitatu.request.retry` event before
  each retry's wait says which attempt failed, why (`status 503`, `network error:
  ConnectionError`) and the delay, and the `fitatu.request.ok` / `fitatu.request.error` events
  carry `attempt` and `elapsed_ms` (a network error after the last attempt now logs
  `fitatu.request.error` too). `FitatuTools.call()`, `FitatuLibrary` and `fitatu-mcp` log the
  exceptions their results report: a bug (`unexpected`) as a WARNING with its traceback, `auth`
  and `maintenance` as WARNINGs, the rest at DEBUG
- `fitatu-api` and `fitatu-mcp` log to stderr (never stdout, which carries the result or the MCP
  protocol): WARNING and above by default, or the level `--log-level` or `FITATU_LOG_LEVEL` sets
  for the library
- live tests use the session file, refuse a day on or before today, delete exactly the rows
  each test added, and run account writes only with `FITATU_LIVE_RUN_WRITE_ACCOUNT=1` (it
  replaces `FITATU_LIVE_RUN_WRITE_PRODUCT` / `FITATU_LIVE_RUN_WRITE_RECIPE`;
  `FITATU_LIVE_USER_ID` is no longer read)

### Deprecated

- `FitatuLibrary`, to be removed in 1.0: constructing it (or `from_session()`) emits a
  `DeprecationWarning` naming the replacements. The library keeps one result envelope for calls
  that never raise, `FitatuTools`' `{"ok": true, "data": ...}` / `{"ok": false, "error":
  describe_error(exc)}`, and `FitatuApiClient` for data and typed exceptions; the facade's
  `{"status": "ok" | "partial" | "error", "result"}` was a second one. Its behaviour and envelope
  stay as they are until then. `fitatu-api export` now uses the client, so nothing in the library
  depends on the facade any more
- `user_settings.get()`, `user_settings.get_for_day()` and every `DietPlanModule` method, to be
  removed in 1.0: use `user_settings.get_new(user_id, day)`

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
- `scripts/record_fixtures.py` sent the session's bearer token as it was and never refreshed it,
  so with a session older than an hour it stopped at its first check with a misleading "already
  has a measurement". It now sends through `FitatuApiClient`, which refreshes the token and saves
  it to the session file
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
- `FitatuLibrary` still works but warns: move to `FitatuTools` (e.g. `get_day_summary_via_api()`
  -> `tools.call("get_day", {"date": ...})`, whose `ok` / `data` / `error` replace `status` /
  `result`) or to `FitatuApiClient` with `try` / `except FitatuApiError`; to silence the warning
  meanwhile, `warnings.filterwarnings("ignore", "FitatuLibrary is deprecated", DeprecationWarning)`
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
  `rollback_added_item(..., meal_type=, item_id=added["row_id"])`
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
- planner and client result keys are snake_case; rename what your code reads (also in
  `FitatuLibrary`'s `result`):

  | Before | Now | In the results of |
  |---|---|---|
  | `rowId`, `rowIds` | `row_id`, `row_ids` | adds, `add_foods_to_day_meal`, `move_day_item`, `copy_meal`, `replace_day_item_with_custom_item`, `add_activity`, `remove_activity` |
  | `syncResponse`, `syncPayload` | `sync_response`, `sync_payload` | every planner write |
  | `addedItem`, `addedItems`, `addedActivity` | `added_item`, `added_items`, `added_activity` | adds, moves, copies, `add_activity` |
  | `replacementItem`, `deleteMarker` | `replacement_item`, `delete_marker` | `move_day_item`, `replace_day_item_with_custom_item` |
  | `removedId`, `removedIdAbsentAfterSync`, `cleanupMode` | `removed_id`, `removed_absent_after_sync`, `cleanup_mode` | `remove_day_item`, `rollback_added_item`, `move_day_item`, replace; `cleanup_mode` also in `attempts[]` |
  | `fromDate`, `toDate`, `fromMeal`, `toMeal` | `from_date`, `to_date`, `from_meal`, `to_meal` | `move_day_item`, `copy_meal` |
  | `productId`, `recipeId`, `foodType` (top level) | `product_id`, `recipe_id`, `food_type` | the adds, `update_day_item`, `resolve_product_measure`, `list_day_items_for_removal` and `targets[]` |
  | `itemId`, `requestedMeal` | `item_id`, `requested_meal` | `update_day_item`; `item_id` also in `targets[]`, `attempts[]` and `list_day_items_for_removal` |
  | `requestedItemId`, `requestedKind`, `resolvedKind`, `mealName` | `requested_item_id`, `requested_kind`, `resolved_kind`, `meal_name` | `classify_day_item_for_removal`, `list_day_items_for_removal`, `remove_day_items_by_kind` |
  | `dryRun`, `targetedCount`, `removedCount`, `failedCount`, `missingRowIds` | `dry_run`, `targeted_count`, `removed_count`, `failed_count`, `missing_row_ids` | `remove_day_items_by_kind` (`dry_run` also in `cleanup_duplicate_user_products`) |
  | `measureResolution`, `searchItem` (its `foodId`, `measureId`) | `measure_resolution`, `search_item` (`food_id`, `measure_id`) | `add_product_to_day_meal_with_unit`, `add_search_result_to_day_meal` |
  | `measureId`, `measureName`, `measureQuantity`, `requestedAmount`, `requestedUnit`, `strictMeasure` | `measure_id`, `measure_name`, `measure_quantity`, `requested_amount`, `requested_unit`, `strict_measure` | `resolve_product_measure` and `measure_resolution` |
  | `matchedCount`, `keptProductId`, `toDeleteProductIds`, `deletedCount`, `deletedProductIds`, `errors[].productId` | `matched_count`, `kept_product_id`, `to_delete_product_ids`, `deleted_count`, `deleted_product_ids`, `errors[].product_id` | `cleanup_duplicate_user_products` |

  The rows inside (`added_item`, `before`, `removed`, `item`, ...), `sync_response`, and the keys
  of `update_day_item()`'s `updates` and `validation` keep the API's camelCase
- `result = client.refresh_access_token()` with a check of `result["status"]` -> call it and
  catch `FitatuAuthError` (log in again) or `FitatuApiError` (anything else); on success it
  returns what `result["state"]` held

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

[Unreleased]: https://github.com/Jezue/fitatu_library/compare/v0.5.1...HEAD
[0.5.1]: https://github.com/Jezue/fitatu_library/compare/v0.5.0...v0.5.1
[0.5.0]: https://github.com/Jezue/fitatu_library/releases/tag/v0.5.0
