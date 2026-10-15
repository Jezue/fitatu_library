# Recorded API fixtures

`api_recording.json` holds real responses from the Fitatu API (app 4.17.1 era), recorded on
2026-10-05. This first recording was made from a logged-in web session running the same scenario
as `scripts/record_fixtures.py`, and scrubbed with that script's `scrub()`; later refreshes come
from the script itself. The unit tests use it in two ways:

- `tests/fake_server.py` is a fake planner backend whose rules come from this recording, and
  `tests/test_recorded_contract.py` replays the recorded day syncs through it and compares
  every resulting day with the recorded one. If the fake drifts from reality, that test fails.
- Library code is fed recorded responses directly (measurements, summaries, export, search,
  settings) and the requests it builds are compared with the ones that worked live.

What the recording covers: an empty day; adding a public catalog product, a catalog recipe and
a synthetic custom row as three separate single-row syncs; updating a row; re-sending a row
without `eaten`; a sync that leaves rows out; a rejected sync (UUID v4 row id); deleting with
`deletedAt`; revisions; range summaries; the CSV export (including the 204 and 422 cases); a
synthetic body measurement written, read and deleted on an empty date; `settings-new` with and
without the date; the activity catalog and search.

Added on 2026-10-13: the user profile (`GET /users/{id}`, reduced to locales, timezone and
units); a RECIPE hit from food search added by `recipeId` and the search row's measure; a catalog
activity added through `activities` in the day sync. The server stores the client's `energy` as
sent. Deleting it by re-sending the row as the day GET returns it is rejected (202 + a
field-by-field `errorMessage`), while the write shape with `deletedAt` deletes it.

Checked live on 2026-10-13 but not recorded (one-off probes on the far-future test days, cleaned
up afterwards): a `CUSTOM_ITEM` row sent with `eaten: true` is stored as eaten; the range
summary (`/v2/diet-plan/{id}/summary/custom`) adds the days up (100 + 300 kcal on two days gave
400, and the target in `max` doubled too); none of the read routes the library uses answers
with a redirect; the water, last-used and favourite replies have the shapes `tests/test_agent.py`
fakes (water: `{water: {waterConsumption, target}, settings: {waterUnitCapacity}}`; foods: lists
of search-hit-like rows with `measure`), and last-used returns up to 200 foods with no limit.

Added to the scenario on 2026-10-13, not recorded yet (`tests/test_agent.py` fakes these replies
until the next refresh):

- `GET /water/{id}/{day}` on the test day. Only when that day already has water logged (more
  than 0 ml; 0 may mean there is no entry at all), which can be put back exactly (there is no
  DELETE), also a `PUT` of 1250 ml and its re-read; the stored amount is then put back, also
  when a later step fails. Otherwise water is only read.
- `GET /search/food/{id}/last-used?meal=breakfast` (recent foods), after the scenario logged a
  product in breakfast, and `GET /users/{id}/favorite/foods?page=1&limit=50` (favourites).

Checked live on 2026-10-14 but not recorded (rows of 200 kcal eaten and 100 kcal planned on an
empty far-future day, cleaned up afterwards); `tests/test_agent.py` fakes these replies, with
neutral numbers, until the next refresh:

- `GET /v2/diet-plan/{id}/summary/{nutrient}/custom` (one nutrient day by day): for `energy`
  `{measures: {day: kcal | null}, targets: {day: kcal}}`; for `protein`, `fat` and `carbohydrate`
  `{measures: {day: g | null}, targetRanges: {day: {min, max}}}`. Every day of the range is a
  key, a day with nothing logged is null, and `measures` counts every row, planned and eaten
  (the two rows gave 300).
- The range summary's `eaten` counts only the rows marked eaten, `current` every row, and `min`
  and `max` are the goals summed over the days: energy `current` 300 and `eaten` 200.

Checked live on 2026-10-14 but not recorded (reads only); `tests/test_agent.py` fakes these
replies, with neutral values, until the next refresh:

- A 13-digit EAN sent as the phrase of `GET /search/new/food` finds that product as the first
  hit (5 of 5 products tried, 1 or 2 hits in all), and the product's details (`GET
  /products/{id}`) carry it as `barcode` (a string) next to `hasBarcodes`.
- `GET /users/{id}/measurements/summary/weight?limit=41&page=1` without `fromDate` and `toDate`
  returns every entry up to `limit`, however old, newest first: there is no default period.
- `GET /diet-plan/{id}/summary-product/{nutrient}/custom` answers for `protein` as for the
  recorded `energy`: `{list: [{id, name, amount: {current, per100}}], count}` with the numbers
  as strings, sorted by `amount.current`, highest first; a custom row has `per100` null and a
  row-like `id`.

Added to the scenario on 2026-10-14, not recorded yet: the food search with the scenario
product's barcode as the phrase (`search_food_barcode`, only when the product has one) and the
top foods for protein (`summary_top_products_protein`).

Added to the scenario on 2026-10-14, not recorded yet: the by-nutrient summary for `protein`,
`fat` and `carbohydrate` next to the recorded `energy` one (`summary_nutrient_{nutrient}`).
`scrub()` replaces their `targetRanges`, the account's macro goals, with fixed synthetic ranges.

`scrub()` blanks every water reply except an amount of 0 or 1250 ml (fail closed: a reply may
carry more than the amount, such as the daily goal), and keeps only the recent and favourite
foods the scenario logged itself, with catalog fields only (name, brand, nutrition, measure id
and weight); the rest is the account's history and is dropped. `leaked_values()` looks for the
dropped names and brands and for the blanked water values.

Checked live on 2026-10-14 but not recorded (reads, and a note written and deleted again on the
test day); `tests/test_agent.py` and `tests/fake_server.py` fake these with neutral values:

- A day sync without `note` and `tagsIds` (only `dietPlan`, only `activities`, a row deletion)
  keeps them and the water; `{"note": "text", "tagsIds": []}` writes the note, `note: null`
  deletes it, and the day GET returns `note` and `tagsIds`.
- `GET /search/catalog/recipes?page&limit&locale&phrase&hasFilters=true&maxEnergy=500` answers
  RECIPE hits in the food search's shape (energy and measure of one portion) with
  `premiumInRecipesCatalog`, `rating`, `cookingTime` and `preparationTime`; `maxEnergy` filters
  by energy per portion only with `hasFilters=true`.
- `GET /intermittent-fasting/{id}/summary?from&to` is an empty list for days without fasting,
  and `/types` lists 9 schemes; `GET /habits/{id}` lists 22 habits, `GET /users/{id}/habits` the
  user's (none on this account) and `GET /habits/{id}/good-days` date strings. The fasting
  summary's and the user habits' items were not seen filled, so their fakes follow the app's DTOs.
- `GET /users/{id}/subscriptions` is `{"subscriptions": [...]}`, each with dates, plan names,
  the store, the price and transaction ids; the fakes carry only neutral dates (and a dummy price
  that must not come out).

Checked live on 2026-10-15 but not recorded (a free account's goals changed and put back):
`PATCH /users/{id}/settings-new` answers with the whole new settings, as a GET gives them
afterwards. It needs `userId` (a number or a string) and refuses a partial `userDietSettings`
(400: a missing field); `updatedAt` may be null, stale or left out. A manual goal
(`manualEnergyTarget: true`) needs all three macro percentages, whole numbers summing to 100,
with grams within 1 g of energy × percent / 100 / 4 (9 for fat), each above 0 g. Anything else is
a 400, and a fractional percentage a 500. Any energy from 100 to 20000 kcal is taken.
`manualEnergyTarget: false` brings Fitatu's calculated energy back and, on this free account,
nulls the split; `waterSettings` alone changes only the glass. A change applies from today on:
today and later days read the new goals, earlier days keep theirs. `tests/test_agent.py` fakes the
reply from the recorded `settings_new`.

Added to the scenario on 2026-10-14, not recorded yet (reads only): the recipe catalog search
with `maxEnergy` (`search_recipes`), the fasting schemes and the fasting summary of the test day,
the habit catalog, the user's habits and good days, and `GET /upgrade-application/{os}/{version}`
(with the token: although the app lists it under its unauthorized client, the server answers 401
without one, checked live). `scrub()` empties the fasting summary, the user's habits
and the good days (an empty list: they are the account's own), sets `currentlyInUse` in the
fasting schemes to false or 0 (the app decodes it as a number; the contract check accepts either
until a recording shows which), and blanks the `note` and `tagsIds` of every day; `leaked_values()`
looks for the note's text. `GET /users/{id}/subscriptions` is not recorded at all: its reply has
prices and transaction ids.

Personal data is scrubbed by `record_fixtures.scrub()` before anything is written: the user id
becomes `1234567`; profile values, goals and targets become fixed synthetic numbers; any other
settings value is blanked (fail closed); custom meal names and times become neutral ones; search
hits that come from the account's own history are dropped. `leaked_values()` then refuses to
write the file if a personal value from the raw recording is still in it. To refresh the file:

```bash
python -m fitatu_api login -o session_data.json
python scripts/record_fixtures.py --session session_data.json
```

The script only writes to an empty day (`--day`, default 2027-01-03) and an empty measurement
date (`--measurement-day`, default 2020-01-01), deletes everything it created and puts back the
day's water if it changed it.

## Checking the recording against the live API

`scripts/check_contract.py` tells whether Fitatu still answers the way this recording says,
without writing anything. It calls every read route the library uses once (an empty day a year
ahead, public catalog items) and compares the structure of each reply, its keys and value types
and the shape of list items, with the recorded reply, or for routes the recording lacks with the
neutral example in `contract_shapes.json`. Those examples hold only the keys seen live or read by
the library, with made-up values; only their structure counts.

```bash
FITATU_SESSION_JSON="$(fitatu-api login --print)" python scripts/check_contract.py
python scripts/check_contract.py --session session_data.json --show-new
```

It prints route and key names, never values. A missing key, a changed type or a failing route is
drift (exit 1): refresh the recording, update the fakes and fix the code that reads the route. A
key that is only new is counted but does not fail (`--show-new` lists them): the recording drops
personal fields on purpose. Null matches any type, an empty list or object says nothing about its
items, and keys that are dates, ids or meal names count as one. On an account without a premium
plan, the routes Fitatu keeps for Premium (`PREMIUM_ONLY` in the script) are not called and print
`skipped (premium)`; a key only some accounts' replies have (`ACCOUNT_KEYS`, e.g. a meal's
`recommendedPercent`, which a free account's day lacks) is not drift when it is missing. The weekly *Live contract*
workflow runs it ([CONTRIBUTING.md](../../CONTRIBUTING.md#detecting-api-changes)).
