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
date (`--measurement-day`, default 2020-01-01) and deletes everything it created.
