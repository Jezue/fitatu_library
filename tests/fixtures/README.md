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

`scrub()` blanks every water reply except an amount of 0 or 1250 ml (fail closed: a reply may
carry more than the amount, such as the daily goal), and keeps only the recent and favourite
foods the scenario logged itself, with catalog fields only (name, brand, nutrition, measure id
and weight); the rest is the account's history and is dropped. `leaked_values()` looks for the
dropped names and brands and for the blanked water values.

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
