# Getting Started

## 1. Install the package

```bash
pip install fitatu-api
```

For local development:

```bash
pip install -e ".[dev]"
```

## 2. Prepare session data

The library expects a session payload that contains enough information to build
`FitatuAuthContext`. The most common fields are:

```json
{
  "bearer_token": "...",
  "refresh_token": "...",
  "fitatu_user_id": "123"
}
```

It can also consume storage-state-like payloads that include `origins` and
`localStorage`.

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
