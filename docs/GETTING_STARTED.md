# Getting Started

## 1. Install the package

```bash
pip install fitatu-api
```

For local development:

```bash
pip install -e ".[dev]"
```

## 2. Get a session

The library works with a small session file:

```json
{
  "bearer_token": "...",
  "refresh_token": "...",
  "fitatu_user_id": "123"
}
```

### Option A: log in from the command line (e-mail + password)

```bash
python -m fitatu_api login            # or: fitatu-api login
```

It asks for your e-mail and password (or reads `FITATU_EMAIL` / `FITATU_PASSWORD`) and writes
`session_data.json` with owner-only permissions. Use `-o path` for another location and
`--base-url https://de-de.fitatu.com/api` (etc.) if your account is not Polish.

### Option B: log in from Python

```python
from fitatu_api import FitatuApiClient

client = FitatuApiClient.login("you@example.com", "password", token_store_path="~/.fitatu/tokens.json")
session_data = client.auth.to_session_data(include_tokens=True)  # save it for later runs
```

`login()` raises `FitatuAuthError` on wrong credentials and `FitatuConnectionError` when the
server cannot be reached.

### Option C: copy it from the web app (Apple / Facebook accounts)

Accounts created with "Continue with Apple" or "Continue with Facebook" have no password, so
options A and B do not work for them. Log in at <https://www.fitatu.com/app/>, open the browser
developer console on that page and run:

```js
const u = JSON.parse(localStorage.user);
copy(JSON.stringify({bearer_token: u.token, refresh_token: u.refresh_token, fitatu_user_id: u.id}, null, 2));
```

Paste the clipboard into `session_data.json`. Treat the file like a password: the refresh token
keeps the session alive, and the library refreshes the bearer token on its own.

`FitatuAuthContext.from_session_data()` also accepts Playwright storage-state payloads
(`origins` / `localStorage`), so a saved browser state works as is.

## 3. Create a client

```python
from fitatu_api import FitatuApiClient, FitatuAuthContext

auth = FitatuAuthContext.from_session_data(session_data)
client = FitatuApiClient(auth=auth)
```

## 4. Run a first request

```python
foods = client.search_food("banan", limit=5)
for item in foods:
    print(item["name"])
```

## 5. Explore the package

- `example.py`: smallest possible usage example
- `demo.py`: larger category-driven showcase demo
- `FitatuApiClient`: low-level API surface
- `FitatuLibrary`: convenience facade for common flows
- `docs/COOKBOOK.md`: practical usage snippets
- `docs/ARCHITECTURE.md`: package layout and design notes
- `docs/API_OVERVIEW.md`: endpoint/helper map, including product proposals, user-food search, day summaries, and experimental move/replace
- `docs/DELETE_GUIDE.md`: detailed guide to item deletion and soft-delete behavior
- `docs/DELETE_SEARCH_ITEMS.md`: step-by-step guide for deleting items added from search

Useful first helpers after `search_food()`:

```python
from datetime import date
from fitatu_api import FitatuLibrary

lib = FitatuLibrary(session_data=session_data)

macros = lib.get_day_macros_via_api(target_date=date.today())
summary = lib.get_day_summary_via_api(target_date=date.today())

print(macros["result"]["totals"])
print(summary["result"]["meals"])
```

## 6. Recommended developer workflow

```bash
ruff check .
mypy src
pytest
python -m build
```

## 7. Recommended next step

After the first request succeeds, run:

```bash
python demo.py
```

This gives a guided overview of the parts of the package that are currently the most polished.
