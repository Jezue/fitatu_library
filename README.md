# fitatu-api

<!-- mcp-name: io.github.Jezue/fitatu-api -->

![PyPI](https://img.shields.io/pypi/v/fitatu-api)
![Python](https://img.shields.io/pypi/pyversions/fitatu-api)
![Status](https://img.shields.io/badge/status-alpha-orange)
![License](https://img.shields.io/badge/license-MIT-green)
![CI](https://github.com/Jezue/fitatu_library/actions/workflows/ci.yml/badge.svg)

An unofficial Python client for the API of [Fitatu](https://www.fitatu.com), the calorie
counter, built to be driven by AI agents and scripts. It reads and writes the food diary,
activities, body measurements and water, reads and sets goals, and reads summaries and the food
catalog.
`FitatuTools` turns the diary into tools a language model can call. The session handling
follows [python-garminconnect](https://github.com/cyberjunky/python-garminconnect): log in once,
keep one session file, let the client refresh it.

> **Unofficial.** This project is not affiliated with or endorsed by Fitatu Sp. z o.o. The API
> was reverse-engineered from the Fitatu apps and can change without notice. Automated access
> may break Fitatu's Terms of Service; check them, and use the library only with your own
> account and at your own risk. The MIT licence covers this code, not the Fitatu service, its
> API or its data.

## Install

Python 3.11 or newer; the only dependency is `requests` (and `tzdata` on Windows).

```bash
pip install fitatu-api                                     # the library and the fitatu-api command
pip install "fitatu-api[mcp]"                              # plus fitatu-mcp, the MCP server
pip install "git+https://github.com/Jezue/fitatu_library"   # unreleased changes from main
```

To work on the library or run its tests offline, clone it instead: `git clone
https://github.com/Jezue/fitatu_library && cd fitatu_library && pip install -e ".[dev]"`.

## Quick start

```bash
fitatu-api login   # asks for e-mail and password, writes ~/.fitatu/session.json
```

Signed up with Apple or Facebook? Then the account has no password: copy the session from the
web app with `fitatu-api import-session` instead, as described under
[Authentication & session file](#authentication--session-file).

```python
from datetime import date

from fitatu_api import FitatuApiClient

with FitatuApiClient.from_session() as client:  # closes the HTTP connections at the end
    user_id = client.auth.fitatu_user_id
    today = date.today()

    day = client.planner.get_day(user_id, today)
    for meal_key, meal in day["dietPlan"].items():
        for row in meal["items"]:
            print(meal_key, row["name"], row["energy"], row["planDayDietItemId"])

    egg = client.search_food("jajko kurze", limit=1)[0]  # search rows carry the id as foodId
    added = client.planner.add_product_to_day_meal(
        user_id, today, meal_type="breakfast", product_id=egg["foodId"],
        measure_id=egg["measure"]["measureId"], measure_quantity=2,  # 2 of the hit's measure
    )
    print("added row", added["row_id"])
```

How writes, row ids and removals work: [docs/PLANNER.md](https://github.com/Jezue/fitatu_library/blob/main/docs/PLANNER.md).

## For AI agents

`FitatuTools` exposes 44 tools (read the day, search food, log food, weight, water,
workouts and private recipes, ...) with JSON Schema inputs and read-only / destructive / idempotent annotations.
`call()` never raises: it returns `{"ok": true, "data": ...}` or `{"ok": false, "error":
{"error_type", "message", "retryable", "hint", ...}}`, so the model can read what went wrong.
`SYSTEM_PROMPT` is the recommended system prompt: the rules the model needs on top of the tool
descriptions (read before writing, users' own meal names, `data.ok`, confirmations). The model
asks `get_today` for the date, in the account's time zone.

```python
import json

import anthropic

from fitatu_api import FitatuTools
from fitatu_api.agent import READ_ONLY_TOOLS, SYSTEM_PROMPT, anthropic_tools  # openai_tools() for OpenAI

client = anthropic.Anthropic()
messages = [{"role": "user", "content": "How many kcal did I eat today?"}]
# Only the read tools, as this loop runs tool calls without asking the user: call() refuses
# any other tool, which the model can still name.
with FitatuTools.from_session(tools=READ_ONLY_TOOLS) as tools:
    while True:
        response = client.messages.create(
            model="claude-opus-5-5",
            max_tokens=16000,
            system=SYSTEM_PROMPT,
            tools=anthropic_tools(READ_ONLY_TOOLS),
            messages=messages,
        )
        messages.append({"role": "assistant", "content": response.content})
        if response.stop_reason != "tool_use":
            break
        results = [
            {
                "type": "tool_result",
                "tool_use_id": block.id,
                "content": json.dumps(tools.call(block.name, block.input)),
            }
            for block in response.content
            if block.type == "tool_use"
        ]
        messages.append({"role": "user", "content": results})
if response.stop_reason != "end_turn":  # max_tokens or refusal: the answer is cut or missing
    raise RuntimeError(f"the model stopped early: {response.stop_reason}")
print("".join(block.text for block in response.content if block.type == "text"))
```

The write tools (log food, update, move, copy and delete rows, weight, water, workouts, private
recipes) need a loop
that asks the user before destructive calls; it is in
[docs/AGENTS.md](https://github.com/Jezue/fitatu_library/blob/main/docs/AGENTS.md), with the
tool table, the calls never to expose (session tokens, catalog writes), how to make writes safe
to repeat, what to do per `error_type`, the OpenAI variant, long-running hosts, and how to try
the tools without an account against the offline test backend.

### MCP

`fitatu-mcp` serves the same tools to Claude Code, Claude Desktop and other MCP hosts over
stdio, with `SYSTEM_PROMPT` as its instructions and three Polish slash commands
(`podsumowanie_dnia`, `przeglad_tygodnia`, `zaloguj_posilek`):

```bash
pipx install "fitatu-api[mcp]"
fitatu-api login
claude mcp add fitatu --scope user -- fitatu-mcp   # fitatu-mcp --read-only: the read tools only
```

Without an account, `fitatu-mcp --demo` (or `FitatuTools.demo()`) serves a small made-up diary
kept in memory. For Claude Desktop, each [GitHub release](https://github.com/Jezue/fitatu_library/releases/latest)
has `fitatu-<version>.mcpb`, installed with a double click (log in with `fitatu-api login` first). Without installing anything, `uvx --from "fitatu-api[mcp]" fitatu-mcp` runs the server. It is
listed in the [MCP Registry](https://registry.modelcontextprotocol.io) as
`io.github.Jezue/fitatu-api` (`server.json`). For Hermes Agent and other hosts that read
[Agent Skills](https://agentskills.io), [`skills/fitatu-diary/SKILL.md`](https://github.com/Jezue/fitatu_library/blob/main/skills/fitatu-diary/SKILL.md)
tells the agent how to set the server up and use its tools well.

The host's permission prompt is the confirmation step, so keep it on for the destructive tools.
Claude Desktop, uvx and what the server does:
[docs/AGENTS.md](https://github.com/Jezue/fitatu_library/blob/main/docs/AGENTS.md#mcp).

## What it covers

| Area | Client (`FitatuApiClient`) | Agent tools |
|---|---|---|
| Food diary | `client.planner`: read a day, add products, recipes, custom rows and search hits, update, move, copy, remove; the day's note | `get_day`, `add_food`, `add_foods`, `add_custom_item`, `update_item`, `move_item`, `copy_meal`, `preview_removal`, `remove_items`, `set_note` |
| Activities | `planner.add_activity` / `remove_activity`, `client.activities` (catalog, search, recent) | `search_activity`, `add_activity`, `remove_activity` |
| Foods | `search_food`, `get_product_details`, `get_recipe`, recipe catalog and its search, `client.foods` (favourites, recent) | `search_food`, `search_recipes`, `get_recipe`, `find_by_barcode`, `get_food_measures`, `get_recent_foods`, `get_favorite_foods`, `set_favorite` |
| The user's own foods | `search_user_food`, `create_recipe`, `update_recipe`, `delete_recipe` | `get_my_foods`; `create_recipe`, `update_recipe` and `delete_recipe` for private recipes only, the user's own |
| Measurements | `client.measurements`: weight and body sizes per day, history, charts | `get_weight_history`, `get_body_measurements`, `get_measurement_history`, `log_weight`, `log_measurements` |
| Water | `client.water` | `get_water`, `set_water` |
| Summary and export | `client.summary`: range totals, nutrient per day, top products, diary export | `get_range_summary`, `get_progress`, `get_top_foods`, `get_report`, `get_shopping_list` (worked out from the planned rows) |
| Fasting and habits | `client.fasting` (schemes, eating periods per day), `client.habits` (catalog, the user's habits, good days); read only | `get_fasting`, `get_habits`, `get_good_days` |
| Goals and settings | `client.user_settings`: targets, BMR/TMR, profile | `get_targets`, `set_targets` (the manual or automatic goal, macro split, glass size), `get_profile` (height, weight, goal, BMI, Premium; nothing that identifies the user) |
| Catalog writes | `create_product`, `create_recipe(shared=True)`, `delete_product`, `set_product_proposal`, duplicate cleanup | none, on purpose |

`FitatuLibrary`, which wraps most of these as `*_via_api` methods that return `{"status": ...}`
instead of raising, is deprecated and will be removed in 1.0: for results that never raise use
`FitatuTools` (`{"ok": true, "data": ...}`), otherwise the client. Every public call with its stability is listed in
[docs/API_REFERENCE.md](https://github.com/Jezue/fitatu_library/blob/main/docs/API_REFERENCE.md).

### Free and Premium accounts

| | Free account | Premium |
|---|---|---|
| Diary: read, log, update, move, copy, remove food, notes, activities, water, weight, body measurements | ✅ | ✅ |
| Search, barcode, food measures, recipe search and recipes in full (premium catalog recipes too), private recipes, favourites, recent foods | ✅ | ✅ |
| Goals: read and set (`get_targets`, `set_targets`), progress (`get_progress`), reports (`get_report`), range totals (`get_range_summary`) | ✅ (progress: a request per day, at most 31 days) | ✅ |
| Weight and body measurement history, profile, fasting and habits (read), shopping list | ✅ | ✅ |
| Top foods (`get_top_foods`, `summary.get_top_products()`) | ❌ 403: "needs Fitatu Premium" | ✅ |
| Diary export (`fitatu-api export`, `summary.export_diary()`) | ❌ 403: "needs Fitatu Premium" | ✅ |
| Protein / fat / carbohydrate day by day (`summary.get_nutrient_by_day()`; energy works on both) | ❌ 403 (`get_progress` reads around it) | ✅ |

Nearly everything works on an account without Fitatu Premium. Fitatu answers 403 without it to
(checked live 2026-10-15):

- `summary.get_nutrient_by_day()` for protein, fat and carbohydrate (energy works on any account);
- `summary.get_top_products()`, and so the `get_top_foods` tool;
- `summary.export_diary()`, and so `fitatu-api export`, which then says so in one line.

`get_progress` and `get_report` work on every account: when the macros' day-by-day route is
refused, they read each day's own summary instead (a request per day, at most 31 days), and
`get_report` gives `top_foods` null with a `top_foods_note`. A free account's macro goals are a
range (`target_min` below `target_max`); on a Premium account a one-day summary gave the two
equal (the day-by-day route `get_progress` reads there agrees, checked live). It takes a manual
goal and its own macro split too (`set_targets`, checked live 2026-10-15); with a split, its
macro goals are single numbers. `fitatu-api doctor`
says whether the account has a premium plan. See
[Free and Premium accounts](https://github.com/Jezue/fitatu_library/blob/main/docs/AGENTS.md#free-and-premium-accounts)
in docs/AGENTS.md for the tools.

## Command line

`fitatu-api` (also `python -m fitatu_api`) works on the session file, `$FITATU_SESSION` or
`~/.fitatu/session.json`:

```bash
fitatu-api login            # or import-session, see Authentication & session file below
fitatu-api status           # the session: file, user id, host, token expiry; sends nothing
fitatu-api doctor           # checks the session, the clock and the client version with Fitatu
fitatu-api logout           # deletes the session file after asking (--yes: without asking)
fitatu-api tools            # the agent tools: name, read / write / destructive, what it does
fitatu-api tools --json     # the same tools in the Anthropic API format, anthropic_tools()
fitatu-api call get_day '{"date": "2026-10-14"}'
fitatu-api export --from 2026-10-01 --to 2026-10-14 --format csv -o diary.csv  # needs Fitatu Premium
```

`status` prints where the session comes from (the file and its permissions, or
`$FITATU_SESSION_JSON`), the user id, API host, locales, when the bearer token expires and
whether there is a refresh token, without sending anything; `--check` also reads the profile
once. `doctor` checks the setup with Fitatu, one `ok` / `WARN` / `FAIL` line each: the session
file's permissions, the refresh token (it refreshes the bearer once and saves it, as any
refresh does), this computer's clock against the fresh token's issue time, a profile read,
whether a premium plan runs (not until when, nor its price), and whether Fitatu's web app is still at the version the library sends (`GET
/upgrade-application/WEB/<version>`); it exits with 1 when a check failed and prints no
token, user id or home directory, so its output can go into a bug report. `logout` deletes the
session file; stop `fitatu-mcp` and other processes that use it first, as they would write
their refreshed tokens back. Fitatu has no call the library knows of that revokes tokens.
None of these commands prints a token. `fitatu-api --version` prints the version.

`call` runs one tool through `FitatuTools.call()` and prints its envelope as JSON; the exit
status is 0 when `ok` is true and 1 when it is false. Only the read tools (`READ_ONLY_TOOLS`)
run unless `--allow-writes` is given. `export` saves Fitatu's diary export (`csv`, `xls`, `pdf`
or `txt`) to a new file readable only by you (0600); it does not replace an existing file
without `--overwrite`, and a range with nothing logged writes no file and exits with 1.
`fitatu-api COMMAND --help` describes each command. Logs go to stderr, from WARNING unless
`fitatu-api --log-level DEBUG COMMAND` or `FITATU_LOG_LEVEL` says otherwise (see
[Privacy & logging](#privacy--logging)).

## Authentication & session file

`fitatu-api login [--email ADDRESS] [-o PATH] [--base-url URL]` (also `python -m fitatu_api
login`) logs in, reads your profile for the account's locales and timezone, and writes the
session file. It reads `FITATU_EMAIL` and
`FITATU_PASSWORD` when they are set and prompts otherwise. Accounts without a password use
`fitatu-api import-session` (below).

- **Where.** `fitatu-api login`, `fitatu-api import-session`, `FitatuApiClient.from_session()`,
  `FitatuTools.from_session()` and the deprecated `FitatuLibrary.from_session()` use the same file: the path
  you pass (`-o` for the CLI), else `$FITATU_SESSION`, else `$FITATU_SESSION_PATH`, else
  `~/.fitatu/session.json`. A missing file raises `FitatuAuthError` telling you to run
  `fitatu-api login`.
- **What.** JSON with `bearer_token`, `refresh_token`, `fitatu_user_id`, `base_url` (the
  region's API host), `app_locale`, `app_searchlocale`, `app_storagelocale`, `app_timezone`
  and a schema version. The client's identity (API key, app version, ...) is not stored; it
  comes from the library.
- **Permissions.** The file is written owner-only (0600), in a directory created 0700, through
  a temporary file that replaces it, so a reader never sees half a session. It holds live
  tokens: treat it like a password.
- **Tokens.** Checked live on 2026-10-13: the bearer token is a JWT that lives one hour. The
  refresh token is an opaque string that Fitatu does not rotate: `POST /token/refresh` returns
  a new bearer token and the same refresh token, which keeps working, and older bearer tokens
  stay valid until they expire. `POST /login` answers a wrong e-mail or password with 422, not
  401.
- **Refresh and write-back.** The client refreshes the bearer token 60 seconds before it
  expires (from the JWT `exp`) and once after a 401, one refresh at a time. The tokens are
  merged into the session file, so the next process starts with them; a process that shares
  the file picks up a pair another one saved, e.g. after `fitatu-api login` ran again. Pass
  `persist_tokens=False` to keep the file untouched. A pair of another account (a login to
  another account with the same file) is not picked up: the running client keeps its own tokens
  and stops writing to the file, so that login stays. When a token fresh from Fitatu already
  looks expired, the computer's clock runs fast: the client then logs one warning and refreshes
  only after a 401.
- **Logging in again.** When Fitatu rejects the refresh token, calls raise `FitatuAuthError`
  (`error_type` `auth`). Run `fitatu-api login` (or `import-session`) again; a running client
  picks the new pair up from the file the next time it needs a refresh.
- **Accounts without a password.** Accounts created with Apple or Facebook (Apple's "Hide my
  e-mail" addresses included) have no password for `fitatu-api login`. Copy the session from
  the web app instead:

  1. Log in at <https://www.fitatu.com/app/> with Apple or Facebook.
  2. Open the browser's developer console on that page and run the line below. It copies only
     the tokens and your user id; the web app's `localStorage.user` also holds your e-mail,
     nickname and more.

     ```js
     copy(JSON.stringify((u => ({token: u.token, refresh_token: u.refresh_token, id: u.id}))(JSON.parse(localStorage.user))))
     ```

  3. On macOS run `pbpaste | fitatu-api import-session`. On Linux or Windows paste it into a
     file, run `fitatu-api import-session < file` (PowerShell:
     `Get-Content file | fitatu-api import-session`) and delete the file.

  `import-session [-o PATH] [--base-url URL]` reads the JSON from stdin (a session file of
  this library works too), checks both tokens with Fitatu by refreshing the bearer once (so the
  saved one is good for an hour), reads the account's locales and timezone like `login` and
  writes the session file the same way.
  Nothing else from the input is saved. The token is a full account credential: do not paste
  it anywhere else.

- **Containers and cron.** Where no writable session file fits, put the session JSON itself
  in `FITATU_SESSION_JSON`: `fitatu-api login --print` (or `import-session --print`) prints it
  to stdout instead of writing a file, for a secret store. `from_session()` without a path
  takes it before any file, and keeps the tokens it refreshes in memory: Fitatu does not
  rotate the refresh token, so the same value works for every run until the refresh token
  itself expires (how long it lives is not known yet) or you log in again.
- **Facade (deprecated).** `FitatuLibrary.from_session()` loads the same file and writes refreshed tokens
  back to it. `FitatuLibrary(session_data, session_path=...)` does the same for a session
  dict, and copies the current pair into `session_data` after every call.
- **From Python.** `FitatuApiClient.login(email, password, token_store_path=path)` logs in and
  merges the whole session into `path`.

## Regions & headers

The API host is the session's `base_url`, else the host of the account's storage locale:
`pl_PL` becomes `https://pl-pl.fitatu.com/api`, `de_DE` becomes `https://de-de.fitatu.com/api`.
`fitatu-api login` logs in on `https://pl-pl.fitatu.com/api` and stores the storage locale's
host; with `--base-url` it logs in there and stores that URL.

| Group | Headers | Notes |
|---|---|---|
| Required | `API-Key`, `API-Secret`, `Authorization: Bearer <jwt>` | The key and secret are static app values (403 without them); `Authorization` is needed on user routes (401). |
| Semantic | `APP-SearchLocale`, `APP-Locale`, `APP-StorageLocale`, `APP-Timezone` | `APP-SearchLocale` and the `locale` query parameter change search results. The others describe the user. All four come from the session, which `login()` fills from the profile; a session without them sends the `pl_PL` / `Europe/Warsaw` defaults. |
| Identification | `APP-OS`, `APP-Version`, `APP-UUID`, `API-Cluster`, `User-Agent` | Only identify the client. The library always sends one constant web-app profile (`WEB_CLIENT_PROFILE` in `constants.py`), never stored in sessions. |

If Fitatu changes what the web app sends (its version, or the API key and secret) before a
release of the library follows, set `FITATU_CLIENT_PROFILE` to a JSON object with the keys of
`WEB_CLIENT_PROFILE` to change, e.g. `FITATU_CLIENT_PROFILE='{"app_version": "4.14.0"}'`. Any
other key or a value that is not plain text is refused with a `FitatuValidationError`
(`fitatu-mcp` then does not start). `fitatu-api doctor` says whether the version is still the
newest.

Login (`POST /login` with `_username` and `_password`) and refresh (`POST /token/refresh` with
`{"refresh_token": ...}`) go through the same request path and send the same headers, without
`Authorization`; at login the locale headers are still the `pl_PL` defaults. There is no
request signing, and Firebase App Check is not enforced; the library does not send it.

Credentials only go to `https` hosts under `fitatu.com`: any other `base_url` or absolute URL
raises `FitatuValidationError` before a request is made, and `Authorization` is only sent to
URLs under `base_url` or `https://fitatu.com/api`. Redirects are not followed: a 3xx answer is
a `FitatuApiError`, so a request body never goes on to another host.

## Errors, retries, rate limits

The library's errors are `FitatuApiError` subclasses. `describe_error(exc)` turns any
exception into the JSON-safe dict that `FitatuTools.call()` (and the deprecated `FitatuLibrary`) return.

| Exception | Raised when | `error_type` |
|---|---|---|
| `FitatuValidationError` (also a `ValueError`) | An argument is invalid; nothing was sent. | `validation` |
| `FitatuNotFoundError` | The id, meal or row you named is not there: the library found that in what it read (the message lists what is there), or the server answered 404 (`status_code` 404). | `not_found` |
| `FitatuAuthError` | 401 after a refresh, a rejected refresh token, no session file, or a wrong e-mail or password at login (Fitatu answers 422); `hint` then points to `import-session`. | `auth` |
| `FitatuRateLimitError` | 429 after retries; `retry_after` holds the server's wait in seconds. | `rate_limit` |
| `FitatuConnectionError` | No HTTP response (DNS, timeout, connection reset). | `network` |
| `FitatuMaintenanceError` | Fitatu's `technical-break` header; `display_from` / `display_to`. | `maintenance` |
| `FitatuPlannerSyncError` | The day sync answered 202 but rejected a day; `results` has the per-day errors. | `sync_rejected` |
| `FitatuApiError` | Any other HTTP error: `status_code`, `body`, `codename` (e.g. `NUMBER_OF_USES_EXCEEDED`). | `forbidden` (403), `validation` (400/409/422), `server` |

Messages read `METHOD /path -> status CODENAME: server message`. A 403 whose body is an HTML page
comes from a firewall in front of the API (e.g. Cloudflare), not from Fitatu's premium or key
checks, and its `hint` says so. Any other exception, such as a `TypeError` raised inside the
library, is `unexpected`: a bug to report, since `FitatuTools.call()` checks the arguments first.

Retries: network errors, 429, 500/502/503/504 and Cloudflare's 520 to 524 are retried for `GET`, `HEAD` and `OPTIONS`,
and for writes marked idempotent: the day sync (all planner writes; it upserts by row id),
`water.set_day`, `measurements.set_day`, `measurements.delete_day` and `foods.set_favorite`.
Other writes (catalog writes, settings, login, refresh) are sent once. Up to
`retry_max_attempts=3` attempts with exponential backoff from `retry_base_delay_seconds=0.5`.
A `Retry-After` of up to 30 seconds is honoured; a longer one is not waited for, and the error
is raised at once (`FitatuRateLimitError` with `retry_after` for a 429). During a technical
break nothing is retried or refreshed. Inside `with client.time_limit(seconds):` the requests
end within that time in all: a retry whose wait would leave it less than 2 seconds is not made, each request's
timeout is cut to the time left, and a request with no time left is not sent
(`FitatuConnectionError`). `FitatuTools` runs every call in one of 45 seconds (`call_timeout`).

Most writes make several requests, which counts when you budget against 429s: the tools read
the day before every diary write, and updates, moves, removals and activity writes read it
again afterwards to set `ok`. Requests per agent tool call, before retries and token refreshes:

| Requests | Tools |
|---|---|
| 0 | `get_today` |
| 1 | the other read tools; `set_water`; `set_favorite`; `preview_removal` and `remove_items` with `dry_run`; `delete_recipe` of a recipe already deleted |
| 2 | `get_range_summary`, `get_profile`, `get_fasting` (3 when the settings name the scheme by id), `get_habits`, `find_by_barcode` (1 without a product hit), `add_food` for a recipe in portions, `add_custom_item`, `log_weight`, `delete_recipe`, `set_targets`; `add_foods`, plus 1 for each item with `unit` or `measure_id`; `create_recipe`, plus 1 for each ingredient |
| 3 | `add_food` with `unit` or `measure_id` (reads the food first), `log_measurements` (reads the size unit from the profile), `copy_meal` within a day, `remove_items`, `add_activity`, `remove_activity`, `set_note`; `update_recipe`, plus 1 for each new ingredient |
| 4 | `update_item`, `move_item` within a day, `copy_meal` to another day |
| 5 | `get_progress` (any range up to 62 days; without Fitatu Premium, see below) |
| 6 | `move_item` to another day |
| 8 | `get_report` (one more for every further 41 weigh-ins in the range) |
| 1 a day | `get_shopping_list`, plus 1 for each recipe planned in the range; `get_progress` and `get_report` without Fitatu Premium, plus 1 for the range (none for a single day) and `get_report`'s weigh-ins, and the first such call on a `FitatuTools` in 10 minutes 1 more, which Fitatu refuses |

## Privacy & logging

The library logs through the standard `logging` module and configures no handlers. The
`fitatu-api` command and `fitatu-mcp` log to stderr, never stdout (which carries the command's
result, or the MCP protocol): the library from WARNING, or from the level that `--log-level
DEBUG|INFO|WARNING|ERROR` (for `fitatu-api` before the command, e.g. `fitatu-api --log-level
DEBUG call get_today`) or `FITATU_LOG_LEVEL` sets; other libraries from WARNING.

- `INFO` (`fitatu_api.client`): auth events as JSON lines: login (user id), token refresh
  start, success and failure, and `clear_auth`, with a non-secret state snapshot (user id,
  locales, timezone, host, token expiry, whether tokens are present).
- `WARNING` (`fitatu_api.client`): `fitatu.request.error` for a request that failed for good on
  a network error, a 429 or a 5xx, after its retries; `fitatu.auth.clock_skew` (a token fresh
  from Fitatu looks expired, so the local clock is off; refreshing ahead of expiry stops) and
  `fitatu.auth.store_other_account` (the session file now holds another account's tokens; the
  client keeps its own). From `fitatu_api.agent`, `fitatu_api.facade` and
  `fitatu_api.mcp_server`, a call whose result reports an `unexpected` error (with the
  traceback), an `auth` or a `maintenance` error: `get_day failed (auth): ...`. From
  `fitatu_api.auth`, a token store (usually the session file) that could not be read.
- `DEBUG` (`fitatu_api.client`): every request as JSON lines: `fitatu.request.start` (method,
  path, query parameters such as search phrases), `fitatu.request.retry` before each retry's
  wait (the failed `attempt`, `delay_seconds`, and the `reason`: the status or the network
  error's type), then `fitatu.request.ok` or `fitatu.request.error` (status, `attempt`,
  `elapsed_ms` since the start, and the first 300 characters of an error body; a 4xx stays at
  DEBUG, since the caller gets it as an exception and some, such as a 404, are expected). From
  `fitatu_api.planner`, one line per planner write (user id, day, meal, food id, a custom
  row's name and kcal); from the tool layers, every other error a result reports. Paths
  contain the user id and dates.

Request bodies, passwords and tokens are never logged: values under keys containing `token`,
`secret`, `password` or `authorization` are replaced with `[redacted]`, and the auth context
keeps tokens and the API secret out of its `repr`. Error messages carry up to 200 characters of
the server's message; the full response body is in `exc.body`, which `describe_error()` and
the error results of `FitatuTools` (and the deprecated `FitatuLibrary`) leave out. A token that is not a valid header value (e.g. pasted with a
newline) is reported without quoting it.

## Testing

```bash
pip install -e ".[dev]"
pytest -q
```

The suite runs offline. `tests/fixtures/api_recording.json` holds real API responses (scrubbed
of personal data) for the day sync, measurements, summaries, search and settings;
`tests/fake_server.py` is a fake backend built from them, and `tests/test_recorded_contract.py`
fails if the fake drifts from the recording. How the recording is made and refreshed:
[tests/fixtures/README.md](https://github.com/Jezue/fitatu_library/blob/main/tests/fixtures/README.md).

The live suite (`tests/test_live_all.py`) runs against a real account and **writes to it**:

```bash
FITATU_RUN_LIVE_TESTS=1 pytest tests/test_live_all.py
```

It uses the session file that `fitatu-api login` wrote (or `$FITATU_SESSION`) and saves
refreshed tokens back to it; with `$FITATU_SESSION_JSON` set, that session is used instead and
refreshed tokens are not saved. Optional: `FITATU_LIVE_DAY` (after today; default a year from
today), `FITATU_LIVE_MEAL_KEY` (`snack`), `FITATU_LIVE_PRODUCT_ID`, `FITATU_LIVE_RECIPE_ID`,
`FITATU_LIVE_MEASUREMENT_DAY` (`2020-01-01`; skipped if it has a measurement) and
`FITATU_LIVE_RUN_WRITE_ACCOUNT=1`.

Each test deletes exactly the rows it added on `FITATU_LIVE_DAY` and fails if one is left; a
day on or before today is refused. Water and the note on that day are set back, and the weight
and measurements written on `FITATU_LIVE_MEASUREMENT_DAY` are deleted. `-k ReadOnly` runs only
the tests that read.
Logging catalog foods adds them to the account's recent foods, which is not undone. With
`FITATU_LIVE_RUN_WRITE_ACCOUNT=1` the suite also changes and restores the water glass size and the
daily goals (Fitatu keeps the restored goals as a new version from today), and creates, then
deletes, a catalog product and two private recipes (one through the tools, logged on
`FITATU_LIVE_DAY` and removed). Recipe deletes are soft, so like the recent foods they are
not fully undone: both recipes stay on the account, marked deleted and hidden from your foods
and searches. The full list is in the module docstring of `tests/test_live_all.py`. Never point `FITATU_LIVE_DAY` at a day with entries you care about.

`scripts/check_contract.py` tells, reading only, whether the live API still answers with the
structure the recording holds: it prints route and key names, never values, and exits 1 when a
key is gone, a type changed or a route fails. The *Live contract* workflow runs it and the
read-only live tests every Monday with a throwaway account's session in the `FITATU_SESSION_JSON`
secret, and skips without it:
[CONTRIBUTING.md](https://github.com/Jezue/fitatu_library/blob/main/CONTRIBUTING.md#detecting-api-changes).

`scripts/eval_agent.py` scores how well a Claude model uses the tools on the demo diary (29
tasks in Polish and English; needs `ANTHROPIC_API_KEY` and costs API usage, so CI does not run
it): [docs/AGENTS.md](https://github.com/Jezue/fitatu_library/blob/main/docs/AGENTS.md#evaluating-a-model).

## Documentation

- [docs/AGENTS.md](https://github.com/Jezue/fitatu_library/blob/main/docs/AGENTS.md): tools, safe writes, error handling and the MCP server for agents.
- [docs/PLANNER.md](https://github.com/Jezue/fitatu_library/blob/main/docs/PLANNER.md): the day sync contract, ids, and the result of every diary write.
- [docs/API_REFERENCE.md](https://github.com/Jezue/fitatu_library/blob/main/docs/API_REFERENCE.md): every public class and method with its stability.
- [docs/APP_4_17_1_API_CATALOG.md](https://github.com/Jezue/fitatu_library/blob/main/docs/APP_4_17_1_API_CATALOG.md): every route of the Fitatu Android app 4.17.1, from a decompile.
- [docs/RELEASING.md](https://github.com/Jezue/fitatu_library/blob/main/docs/RELEASING.md): release steps for maintainers.
- [CHANGELOG.md](https://github.com/Jezue/fitatu_library/blob/main/CHANGELOG.md)

Bugs and questions: [GitHub issues](https://github.com/Jezue/fitatu_library/issues). Security
problems are reported privately: [SECURITY.md](https://github.com/Jezue/fitatu_library/blob/main/SECURITY.md).
Setup, checks and rules for pull requests:
[CONTRIBUTING.md](https://github.com/Jezue/fitatu_library/blob/main/CONTRIBUTING.md).

## License

MIT, see [LICENSE](https://github.com/Jezue/fitatu_library/blob/main/LICENSE). It covers this
code only, not the Fitatu service, its API or the data it returns.
