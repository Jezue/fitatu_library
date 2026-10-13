# Using fitatu-api from an AI agent

`FitatuTools` (`fitatu_api.agent`) is the part of the library meant for language models: the
diary as JSON-in, JSON-out tools with JSON Schema inputs, descriptions written for a model and
MCP-style annotations. Tools always act for the user the session belongs to, so none of them
takes a user id. Session handling and writes to the shared food catalog are deliberately not
tools.

```python
from fitatu_api import FitatuTools
from fitatu_api.agent import TOOLS, anthropic_tools, openai_tools

with FitatuTools.from_session() as tools:  # the `fitatu-api login` session file
    result = tools.call("get_day", {"date": "2026-10-13"})
    # {"ok": True, "data": {...}} or {"ok": False, "error": {...}}
```

- `TOOLS` is the list of tool definitions: `name`, `description`, `input_schema`,
  `annotations` (`readOnlyHint`, `destructiveHint`, `idempotentHint`).
- `anthropic_tools()` returns them in the Anthropic Messages API `tools` format,
  `openai_tools()` in the OpenAI function-calling format. Both return a fresh copy, so you may
  adapt it in place. For OpenAI strict mode, make every property required and the optional
  ones nullable: `call()` treats `null` as an omitted argument.
- `FitatuTools.call(name, arguments)` checks the name and the arguments against the schema
  before anything is sent, runs the tool and never raises.
- Each tool is also a method (`tools.get_day("2026-10-13")`) that returns the `data` part and
  raises the library's typed errors, for code that is not a model.
- `FitatuTools.from_session(path=None, **client_kwargs)` finds the session file like
  `FitatuApiClient.from_session()` and writes refreshed tokens back to it; `FitatuTools(client)`
  wraps a client you built yourself.

## Tools

Dates are `YYYY-MM-DD` strings. Meals are `breakfast`, `second_breakfast`, `lunch`, `dinner`,
`snack` and `supper`, but a day only has the meals of the user's meal plan, the ones `get_day`
lists; any other meal is `not_found`. Energy is in kcal, nutrients in grams.

**Kind** is `readOnlyHint`. **Destructive** (`destructiveHint`) means the call can overwrite or
delete diary data: ask the user before running it. **Idempotent** (`idempotentHint`) means
repeating the same call has no further effect.

| Tool | What it does | Kind | Destructive | Idempotent |
|---|---|---|---|---|
| `get_day` | One diary day: rows per meal (`row_id`, name, measure, quantity, weight, energy and nutrients, `eaten`), meal and day totals, `eaten_totals`, logged activities. | read | no | yes |
| `get_targets` | Daily goals for a day: energy, protein/fat/carbohydrate in grams and percent, BMR/TMR. | read | no | yes |
| `get_range_summary` | Fitatu's own nutrition summary and burned energy for a date range; nutrition is the total over the range, not a daily average. | read | no | yes |
| `search_food` | Catalog search; each hit has `food_id`, `type` (PRODUCT or RECIPE), name, brand and its default measure. | read | no | yes |
| `get_food_measures` | The measures a food can be logged in (`measure_id`, name, weight or capacity, energy of one measure). | read | no | yes |
| `get_recent_foods` | Foods recently logged in a meal, shaped like search hits. | read | no | yes |
| `get_favorite_foods` | The user's favourite products and recipes, shaped like search hits. | read | no | yes |
| `get_weight_history` | Logged body weights (`date`, `value` in the user's weight unit, `difference`, `direction`), up to `limit` entries (41), optionally for a range. | read | no | yes |
| `get_water` | Water drunk on a day, in ml. | read | no | yes |
| `search_activity` | Activity catalog search; `activity_id` and the catalog's energy estimate for the effort in `energy_for`. | read | no | yes |
| `add_food` | Log a catalog food in a meal (grams/ml for a product, or `measure_id` and a count; recipes in portions); returns its `row_id`. | write | no | no |
| `add_custom_item` | Log a food that is not in the catalog by name and nutrition of the amount eaten; returns its `row_id`. | write | no | no |
| `update_item` | Change a food row's quantity (in its own measure) and/or `eaten`, nothing else. | write | yes | no |
| `move_item` | Move a row to another meal and/or day; the moved row gets a new `row_id`. | write | yes | no |
| `remove_items` | Delete rows by `row_id`, with an optional `dry_run`. | write | yes | yes |
| `log_weight` | Save the body weight for a day in kg, replacing that day's weight and keeping its other measurements. | write | yes | yes |
| `set_water` | Set the day's water total in ml (replaces it, does not add). | write | yes | yes |
| `add_activity` | Log a catalog activity with minutes and the kcal burned; returns its `row_id`. | write | no | no |
| `remove_activity` | Delete a logged activity by its `row_id`. | write | yes | yes |

The full descriptions the model sees are in `TOOLS`; read them once, they say where every id
comes from.

## Never expose these as tools

The library has calls an agent should not be able to reach. Keep them in host code, behind
a person.

| Call | Why |
|---|---|
| `FitatuLibrary.export_session_context(include_tokens=True)`, `FitatuAuthContext.to_session_data(include_tokens=True)`, `client.auth` | Return the bearer and refresh token: whoever has them has the account. |
| `FitatuLibrary.clear_session()`, `FitatuApiClient.clear_auth()` | Drop the tokens and, by default, delete the session file. |
| `FitatuApiClient.login()`, `refresh_access_token()` | Take a password or mint new session tokens. |
| `FitatuApiClient.request()`, `planner.sync_days()`, `planner.sync_single_day()` | Raw requests and raw day payloads skip every check the tools make. |
| `create_product()`, `delete_product()`, `set_product_proposal()`, `set_product_raw_ingredients()`, `cleanup_duplicate_user_products()`, `create_recipe()`, their `*_via_api` wrappers, and `add_user_dish_via_api()` (the wrapper of `create_recipe()`) | Write to the food catalog (products, change proposals for catalog products, recipes that can be shared) or delete products for good. |
| `user_settings.update_profile()`, `update_new()`, `update_water_settings()` | Change account settings, not the diary. |
| `measurements.delete_day()`, `planner.remove_day_items_by_kind(all_meals=True)` without `row_ids` | Delete a whole day's measurements or every row of a day. `remove_items` always names its rows. |
| `FitatuLibrary.export_diary_via_api(path=..., overwrite=True)` | Writes or replaces a local file at any path the process can write, with content a model can partly choose (custom row names). Let host code pick the path. |

## Writing safely

1. **Read first.** `update_item`, `move_item` and `remove_items` take a food row's `row_id`
   from `get_day` (`meals[].items[].row_id`); `remove_activity` takes one from
   `activities[].row_id`. Never use a product id or guess an id.
2. **Adds log a new row every time.** When an add has no `row_id`, `call()` picks one. If the
   add then fails with any error but `validation`, `not_found` or `sync_rejected` (a timeout,
   a 5xx, a 429, an expired session, a technical break) it may still have been saved, so the
   error says `retryable: false` and its `hint` names that `row_id`: when you repeat the add,
   repeat it with that `row_id`. To make an add retry-safe from the start, pass your own UUID
   version 1 as `row_id`. The same `row_id` with the same values never logs a second row.
3. **Never reuse a `row_id` from `get_day` for an add.** The add is refused when the stored row
   has another meal, food, measure, quantity or `eaten` (for a custom row: name, nutrition or
   `eaten`). If that happens to a repeated add, the earlier attempt was saved and the row has
   changed since (e.g. marked eaten on the phone): do not add it again. `update_item` changes
   only a food row's quantity and `eaten`; to change anything else (another food, a custom row's
   name or nutrition, an activity's minutes or kcal), remove the row with `remove_items` or
   `remove_activity` and add it again.
4. **Preview removals.** Call `remove_items` with `dry_run: true`, show the user what
   `removed` lists, then call it again without `dry_run`. Ids that are not food rows of that
   day (already deleted, or an activity's id) come back in `missing`, so repeating a removal is
   safe; `ok` is false when anything was missing or is still there (`failed`).
5. **Verify by re-reading.** Write results carry `ok`: for updates, moves, removals and
   activities it is the outcome of a re-read. Food adds are not read back: their `ok` only says
   the sync accepted the row, and the add does not check that `measure_id` belongs to the food.
   Before telling the user something changed, check `ok` and, when the food, measure or amount
   matters, call `get_day` again.
6. **Totals, not deltas.** `set_water` replaces the day's total: read `get_water` and send the
   sum. `log_weight` replaces the weight of that day.
7. **Activities.** Fitatu stores `energy_kcal` as given. Scale the `search_activity` estimate
   to what the user did.
8. **Give the model today's date** in the system prompt; it needs it for every tool.

## Results and errors

`call()` returns `{"ok": true, "data": ...}` or `{"ok": false, "error": {...}}`. The envelope's
`ok` says whether the call ran. For updates, moves, removals and activity writes, `data.ok` says
whether a re-read confirmed the effect; food adds report `data.ok` once the sync accepted the
row, without a re-read. The error is `describe_error()` of the exception:

```json
{
  "error_type": "not_found",
  "message": "row_id 'nope' is not on 2026-10-13; read the day with get_day for its row ids",
  "status_code": null,
  "codename": null,
  "retryable": false,
  "retry_after": null,
  "hint": "Check the id, meal or date; read the day again to get current row ids."
}
```

| `error_type` | Meaning | What the agent should do |
|---|---|---|
| `validation` | The arguments were rejected: unknown tool, wrong type or value, or a 400/409/422 from the server. | Fix the arguments using `message`; do not repeat the same call. |
| `not_found` | The meal, row or id is not there (or a 404). | A row: call `get_day` again and use a `row_id` from it. A meal: use one the message lists. A `food_id`: search again. |
| `auth` | The session is gone (refresh rejected, no session file, no user id). | Stop and ask the user to run `fitatu-api login` (`fitatu-api import-session` for an account without a password). An add: repeat it later with the `row_id` from `hint`. |
| `rate_limit` | 429, after the client's own retries or because the server asked to wait more than 30 s. | Wait `retry_after` seconds when set, then repeat; an add with the `row_id` from `hint`. |
| `maintenance` | Fitatu is in a technical break. | Stop; `hint` says until when. Retrying or logging in again does not help. An add: repeat it afterwards with the `row_id` from `hint`. |
| `network` | No HTTP response. | Repeat (`retryable` is true), except an add: repeat it with the `row_id` from `hint`. |
| `sync_rejected` | The day sync answered 202 but rejected the day; nothing was saved. | Fix what `message` names and send it again. |
| `forbidden` | 403: a premium-only feature, a used-up AI quota (see `codename`) or a wrong API key. | Do not retry; tell the user. |
| `server` | A 5xx or another API error. | Repeat a few times when `retryable`; otherwise report it. An add: use the `row_id` from `hint`. |
| `unexpected` | Any other exception (a bug). | Report it; do not retry. |

## A complete agent loop (Anthropic)

The host runs the loop and asks the user before any destructive call. Needs
`pip install anthropic` and an API key in `ANTHROPIC_API_KEY`.

```python
import json
from datetime import date

import anthropic

from fitatu_api import FitatuTools
from fitatu_api.agent import TOOLS, anthropic_tools

DESTRUCTIVE = {tool["name"] for tool in TOOLS if tool["annotations"]["destructiveHint"]}


def run(prompt: str) -> str:
    client = anthropic.Anthropic()
    tool_definitions = anthropic_tools()
    system = f"You keep the user's Fitatu food diary. Today is {date.today().isoformat()}."
    messages = [{"role": "user", "content": prompt}]
    with FitatuTools.from_session() as tools:
        while True:
            response = client.messages.create(
                model="claude-opus-5-5",
                max_tokens=16000,
                system=system,
                tools=tool_definitions,
                messages=messages,
            )
            messages.append({"role": "assistant", "content": response.content})
            if response.stop_reason != "tool_use":
                return "".join(block.text for block in response.content if block.type == "text")
            results = []
            for block in response.content:
                if block.type != "tool_use":
                    continue
                question = f"Allow {block.name} {json.dumps(block.input)}? [y/N] "
                if block.name in DESTRUCTIVE and input(question).strip().lower() != "y":
                    content, failed = "The user declined this change.", True
                else:
                    result = tools.call(block.name, block.input)
                    content, failed = json.dumps(result), not result["ok"]
                results.append(
                    {"type": "tool_result", "tool_use_id": block.id, "content": content, "is_error": failed}
                )
            messages.append({"role": "user", "content": results})


print(run("Log two boiled eggs for breakfast and tell me how many kcal I have left today."))
```

All `tool_result` blocks of one turn go back in a single user message, which keeps parallel
tool calls working.

## The same loop with OpenAI

```python
import json
from datetime import date

from openai import OpenAI

from fitatu_api import FitatuTools
from fitatu_api.agent import openai_tools


def run(prompt: str, model: str) -> str:
    client = OpenAI()
    tool_definitions = openai_tools()
    messages = [
        {"role": "system", "content": f"You keep the user's Fitatu food diary. Today is {date.today().isoformat()}."},
        {"role": "user", "content": prompt},
    ]
    with FitatuTools.from_session() as tools:
        while True:
            message = client.chat.completions.create(
                model=model, messages=messages, tools=tool_definitions
            ).choices[0].message
            messages.append(message)
            if not message.tool_calls:
                return message.content or ""
            for tool_call in message.tool_calls:
                result = tools.call(tool_call.function.name, json.loads(tool_call.function.arguments))
                messages.append({"role": "tool", "tool_call_id": tool_call.id, "content": json.dumps(result)})
```

Add the same confirmation step as above before destructive tools.

## MCP

An MCP server is planned as a thin wrapper over `FitatuTools`; it is not part of 0.5.0. The
annotations in `TOOLS` already use the MCP names, so a host that wraps the tools itself can pass
them through unchanged.

## See also

- [PLANNER.md](PLANNER.md): what the day sync does with each write, and the ids.
- [API_REFERENCE.md](API_REFERENCE.md): every public class and method with its stability.
- The [README](https://github.com/Jezue/fitatu_library/blob/main/README.md): session file,
  headers, retries and logging.
