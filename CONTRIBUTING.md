# Contributing

Thanks for helping. Bugs and ideas go to [GitHub issues](https://github.com/Jezue/fitatu_library/issues),
security problems to [SECURITY.md](SECURITY.md).

## Set up

```bash
git clone https://github.com/Jezue/fitatu_library && cd fitatu_library
python -m venv .venv && . .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -e ".[dev]"
pre-commit install
```

## Checks

CI runs these on Linux with Python 3.11 to 3.14 and on macOS and Windows; run them before a pull
request:

```bash
ruff check .
mypy src demo.py example.py
pytest -q
pre-commit run --all-files
```

`hatch run all` runs the first three and builds the package.

## Tests

- The suite runs offline. Fitatu's replies come from `tests/fake_server.py`, a fake backend
  built from the recorded responses in `tests/fixtures/api_recording.json`, or from mocks. A test
  never calls the real API.
- The recording is made only with `scripts/record_fixtures.py`, which scrubs personal data and
  refuses to write the file if a personal value survives. Do not edit it by hand to add values
  of your own; what it covers and how to refresh it is in
  [tests/fixtures/README.md](tests/fixtures/README.md).
- The live suite (`tests/test_live_all.py`, `FITATU_RUN_LIVE_TESTS=1`) writes to a real account
  and runs on your own machine; see the README's Testing section. Its classes ending in
  `ReadOnly` only read. Assertion messages there name what failed, never what the account
  holds.

## Detecting API changes

Fitatu's API is unofficial and changes without notice. `scripts/check_contract.py` reads every
route the library reads and compares the structure of each reply with the recording (or with
`tests/fixtures/contract_shapes.json`); it prints route and key names, never values, and exits
1 when a key is gone, a type changed or a route fails. How it compares:
[tests/fixtures/README.md](tests/fixtures/README.md#checking-the-recording-against-the-live-api).

The *Live contract* workflow (`.github/workflows/live-contract.yml`) runs it every Monday and on
demand, followed by the read-only live tests (`-k ReadOnly`, with `--assert=plain` so pytest
does not print compared values). It needs the repository secret `FITATU_SESSION_JSON`; without
it the run ends with a notice and succeeds. To set it up (repository owner):

1. Create a throwaway Fitatu account for it, with no real data in it. Only reads run, and the
   logs carry route and key names with the account id and tokens masked, but Actions logs of a
   public repository are public, and the secret is a working login to that account.
2. Store its session: `fitatu-api login --print | gh secret set FITATU_SESSION_JSON` (the
   e-mail and password are asked for; nothing is written to disk).
3. Start the workflow once by hand (*Actions > Live contract > Run workflow*).

Fitatu does not rotate refresh tokens, so the secret keeps working until the refresh token
expires (how long that takes is unknown; the job will tell). When a run stops with
`FitatuAuthError`, log in again and replace the secret. When it reports drift, refresh the
recording (`scripts/record_fixtures.py`, see docs/RELEASING.md), update the fakes and the code
that reads the route, and say what changed in the changelog.

## No personal data

Code, tests, fixtures, docs, issues and commits hold neutral values only: the synthetic user id
`1234567`, made-up foods and amounts, `example.com` addresses. Never commit a session file, a
token, a name, an e-mail address, or your own meals, weights or measurements.

## Agent tools

- A changed tool needs its row in the table in [docs/AGENTS.md](docs/AGENTS.md) (a test checks
  it) and, when its name, parameters, required arguments or annotations change, a new
  `tests/fixtures/tool_contract.json` (`python tests/test_tool_contract.py`). The
  [compatibility policy](docs/RELEASING.md#compatibility-policy) says which version part that
  bumps.
- New tools reach `fitatu-mcp` without a change to the server; `--read-only` serves the ones
  whose `readOnlyHint` is true.
- Tools do not write to the shared food catalog or to other users' items, do not change account
  goals or settings, and never handle passwords. Those stay library calls on purpose.

## Changes and commits

- One logical change per pull request, with its tests and the docs it makes untrue updated:
  README.md, docs/AGENTS.md, docs/API_REFERENCE.md (with the call's stability), docs/PLANNER.md,
  and an entry in `CHANGELOG.md` under the unreleased version for anything a user notices.
- Commit subjects follow [Conventional Commits](https://www.conventionalcommits.org/):
  `feat(agent): ...`, `fix(client): ...`, `docs: ...`, `test: ...`, `ci: ...`, `build: ...`.
  The body says why the change is needed, not only what it does.
