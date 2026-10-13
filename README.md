# fitatu-api

![Python](https://img.shields.io/badge/python-3.11%2B-blue)
![Status](https://img.shields.io/badge/status-alpha-orange)
![License](https://img.shields.io/badge/license-MIT-green)
![CI](https://github.com/Jezue/fitatu_library/actions/workflows/ci.yml/badge.svg)

An unofficial Python client for the API of [Fitatu](https://www.fitatu.com), the calorie
counter, built to be driven by AI agents and scripts. It reads and writes the food diary,
activities, body measurements and water, and reads goals, summaries and the food catalog.
`FitatuTools` turns the diary into tools a language model can call. The session handling
follows [python-garminconnect](https://github.com/cyberjunky/python-garminconnect): log in once,
keep one session file, let the client refresh it.

> **Unofficial.** This project is not affiliated with or endorsed by Fitatu Sp. z o.o. The API
> was reverse-engineered from the Fitatu apps and can change without notice. Automated access
> may break Fitatu's Terms of Service; check them, and use the library only with your own
> account and at your own risk. The MIT licence covers this code, not the Fitatu service, its
> API or its data.

## Install

Python 3.11 or newer; the only dependency is `requests`.

```bash
pip install fitatu-api                                     # from PyPI, once 0.5.0 is published
pip install "git+https://github.com/Jezue/fitatu_library"   # or straight from GitHub
```

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
    print("added row", added["rowId"])
```

How writes, row ids and removals work: [docs/PLANNER.md](https://github.com/Jezue/fitatu_library/blob/main/docs/PLANNER.md).

## For AI agents

`FitatuTools` exposes 19 tools (read the day, search food, log food, weight, water and
workouts, ...) with JSON Schema inputs and read-only / destructive / idempotent annotations.
`call()` never raises: it returns `{"ok": true, "data": ...}` or `{"ok": false, "error":
{"error_type", "message", "retryable", "hint", ...}}`, so the model can read what went wrong.

```python
import json
from datetime import date

import anthropic

from fitatu_api import FitatuTools
from fitatu_api.agent import TOOLS, anthropic_tools  # openai_tools() for OpenAI

# Only the read tools: this loop runs every tool call without asking the user.
READ_ONLY = {tool["name"] for tool in TOOLS if tool["annotations"]["readOnlyHint"]}
read_tools = [tool for tool in anthropic_tools() if tool["name"] in READ_ONLY]

client = anthropic.Anthropic()
messages = [{"role": "user", "content": "How many kcal did I eat today?"}]
with FitatuTools.from_session() as tools:
    while True:
        response = client.messages.create(
            model="claude-opus-5-5",
            max_tokens=16000,
            system=f"You keep the user's Fitatu food diary. Today is {date.today().isoformat()}.",
            tools=read_tools,
            messages=messages,
        )
        messages.append({"role": "assistant", "content": response.content})
        if response.stop_reason != "tool_use":
            break
        results = [
            {"type": "tool_result", "tool_use_id": block.id, "content": json.dumps(tools.call(block.name, block.input))}
            for block in response.content
            if block.type == "tool_use"
        ]
        messages.append({"role": "user", "content": results})
print("".join(block.text for block in response.content if block.type == "text"))
```

The write tools (log food, update, move and delete rows, weight, water, workouts) need a loop
that asks the user before destructive calls; it is in
[docs/AGENTS.md](https://github.com/Jezue/fitatu_library/blob/main/docs/AGENTS.md), with the
tool table, the calls never to expose (session tokens, catalog writes), how to make writes safe
to repeat, what to do per `error_type`, and the OpenAI variant.

## What it covers

| Area | Client (`FitatuApiClient`) | Agent tools |
|---|---|---|
| Food diary | `client.planner`: read a day, add products, recipes, custom rows and search hits, update, move, remove | `get_day`, `add_food`, `add_custom_item`, `update_item`, `move_item`, `remove_items` |
| Activities | `planner.add_activity` / `remove_activity`, `client.activities` (catalog, search, recent) | `search_activity`, `add_activity`, `remove_activity` |
| Foods | `search_food`, `get_product_details`, `get_recipe`, recipe catalog, `client.foods` (favourites, recent) | `search_food`, `get_food_measures`, `get_recent_foods`, `get_favorite_foods` |
| Measurements | `client.measurements`: weight and body sizes per day, history, charts | `get_weight_history`, `log_weight` |
| Water | `client.water` | `get_water`, `set_water` |
| Summary and export | `client.summary`: range totals, nutrient per day, top products, diary export | `get_range_summary` |
| Goals and settings | `client.user_settings`: targets, BMR/TMR, profile | `get_targets` |
| Catalog writes | `create_product`, `create_recipe`, `delete_product`, `set_product_proposal`, duplicate cleanup | none, on purpose |

`FitatuLibrary` wraps most of these as `*_via_api` methods that return `{"status": ...}`
instead of raising. Every public call with its stability is listed in
[docs/API_REFERENCE.md](https://github.com/Jezue/fitatu_library/blob/main/docs/API_REFERENCE.md).

## Authentication & session file

`fitatu-api login [--email ADDRESS] [-o PATH] [--base-url URL]` (also `python -m fitatu_api
login`) logs in, reads your profile for the account's locales and timezone, and writes the
session file. It reads `FITATU_EMAIL` and
`FITATU_PASSWORD` when they are set and prompts otherwise. Accounts without a password use
`fitatu-api import-session` (below).

- **Where.** `fitatu-api login`, `fitatu-api import-session`, `FitatuApiClient.from_session()`,
  `FitatuTools.from_session()` and `FitatuLibrary.from_session()` use the same file: the path
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
  `persist_tokens=False` to keep the file untouched.
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

- **Facade.** `FitatuLibrary.from_session()` loads the same file and writes refreshed tokens
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
exception into the JSON-safe dict that `FitatuTools.call()` and `FitatuLibrary` return.

| Exception | Raised when | `error_type` |
|---|---|---|
| `FitatuValidationError` (also a `ValueError`) | An argument is invalid; nothing was sent. | `validation` |
| `FitatuNotFoundError` (a `FitatuValidationError`) | The id, meal or row you named is not there; the message lists what is. | `not_found` |
| `FitatuAuthError` | 401 after a refresh, a rejected refresh token, no session file, or a wrong e-mail or password at login (Fitatu answers 422); `hint` then points to `import-session`. | `auth` |
| `FitatuRateLimitError` | 429 after retries; `retry_after` holds the server's wait in seconds. | `rate_limit` |
| `FitatuConnectionError` | No HTTP response (DNS, timeout, connection reset). | `network` |
| `FitatuMaintenanceError` | Fitatu's `technical-break` header; `display_from` / `display_to`. | `maintenance` |
| `FitatuPlannerSyncError` | The day sync answered 202 but rejected a day; `results` has the per-day errors. | `sync_rejected` |
| `FitatuApiError` | Any other HTTP error: `status_code`, `body`, `codename` (e.g. `NUMBER_OF_USES_EXCEEDED`). | `forbidden` (403), `validation` (400/409/422), `not_found` (404), `server` |

Messages read `METHOD /path -> status CODENAME: server message`.

Retries: network errors, 429 and 500/502/503/504 are retried for `GET`, `HEAD` and `OPTIONS`,
and for writes marked idempotent: the day sync (all planner writes; it upserts by row id),
`water.set_day`, `measurements.set_day`, `measurements.delete_day` and `foods.set_favorite`.
Other writes (catalog writes, settings, login, refresh) are sent once. Up to
`retry_max_attempts=3` attempts with exponential backoff from `retry_base_delay_seconds=0.5`.
A `Retry-After` of up to 30 seconds is honoured; a longer one is not waited for, and the error
is raised at once (`FitatuRateLimitError` with `retry_after` for a 429). During a technical
break nothing is retried or refreshed.

Most writes make several requests, which counts when you budget against 429s: the tools read
the day before every diary write, and updates, moves, removals and activity writes read it
again afterwards to set `ok`. Requests per agent tool call, before retries and token refreshes:

| Requests | Tools |
|---|---|
| 1 | the read tools except `get_range_summary`; `set_water`; `remove_items` with `dry_run` |
| 2 | `get_range_summary`, `add_food` with `measure_id` or a recipe, `add_custom_item`, `log_weight` |
| 3 | `add_food` with `unit` (reads the product first), `remove_items`, `add_activity`, `remove_activity` |
| 4 | `update_item`, `move_item` within a day |
| 6 | `move_item` to another day |

## Privacy & logging

The library logs through the standard `logging` module and configures no handlers.

- `INFO` (`fitatu_api.client`): auth events as JSON lines: login (user id), token refresh
  start, success and failure, and `clear_auth`, with a non-secret state snapshot (user id,
  locales, timezone, host, token expiry, whether tokens are present).
- `WARNING` (`fitatu_api.auth`): the token store (usually the session file) could not be read.
- `DEBUG`: every request (method, path, query parameters such as search phrases, status, and
  the first 300 characters of an error body) from `fitatu_api.client`, and one line per
  planner write (user id, day, meal, food id, a custom row's name and kcal) from
  `fitatu_api.planner`. Paths contain the user id and dates.

Request bodies, passwords and tokens are never logged: values under keys containing `token`,
`secret`, `password` or `authorization` are replaced with `[redacted]`, and the auth context
keeps tokens and the API secret out of its `repr`. Error messages carry up to 200 characters of
the server's message; the full response body is in `exc.body`, which `describe_error()` and
the error results of `FitatuTools` and `FitatuLibrary` leave out. A token that is not a valid header value (e.g. pasted with a
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
refreshed tokens back to it. Optional: `FITATU_LIVE_DAY` (after today; default a year from
today), `FITATU_LIVE_MEAL_KEY` (`snack`), `FITATU_LIVE_PRODUCT_ID`, `FITATU_LIVE_RECIPE_ID`,
`FITATU_LIVE_MEASUREMENT_DAY` (`2020-01-01`; skipped if it has a measurement) and
`FITATU_LIVE_RUN_WRITE_ACCOUNT=1`.

Each test deletes exactly the rows it added on `FITATU_LIVE_DAY` and fails if one is left; a
day on or before today is refused. Water on that day and the test measurement are set back.
Logging catalog foods adds them to the account's recent foods, which is not undone. With
`FITATU_LIVE_RUN_WRITE_ACCOUNT=1` the suite also changes and restores the water glass size and
creates, then deletes, a catalog product and a private recipe. The full list is in the
module docstring of `tests/test_live_all.py`. Never point `FITATU_LIVE_DAY` at a day with entries you care about.

## Documentation

- [docs/AGENTS.md](https://github.com/Jezue/fitatu_library/blob/main/docs/AGENTS.md): tools, safe writes and error handling for agents.
- [docs/PLANNER.md](https://github.com/Jezue/fitatu_library/blob/main/docs/PLANNER.md): the day sync contract, ids, and the result of every diary write.
- [docs/API_REFERENCE.md](https://github.com/Jezue/fitatu_library/blob/main/docs/API_REFERENCE.md): every public class and method with its stability.
- [docs/APP_4_17_1_API_CATALOG.md](https://github.com/Jezue/fitatu_library/blob/main/docs/APP_4_17_1_API_CATALOG.md): every route of the Fitatu Android app 4.17.1, from a decompile.
- [docs/RELEASING.md](https://github.com/Jezue/fitatu_library/blob/main/docs/RELEASING.md): release steps for maintainers.
- [CHANGELOG.md](https://github.com/Jezue/fitatu_library/blob/main/CHANGELOG.md)

Bugs and questions: [GitHub issues](https://github.com/Jezue/fitatu_library/issues). Before a
pull request, run `ruff check .`, `mypy src` and `pytest -q`.

## License

MIT, see [LICENSE](https://github.com/Jezue/fitatu_library/blob/main/LICENSE). It covers this
code only, not the Fitatu service, its API or the data it returns.
