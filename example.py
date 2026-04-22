"""Minimal but pleasant example for using fitatu-api."""

from __future__ import annotations

import json
from pathlib import Path

from fitatu_api import FitatuApiClient, FitatuAuthContext


def main() -> None:
    """Run the smallest useful fitatu-api example."""
    session_path = Path("session_data.json")
    if not session_path.exists():
        raise SystemExit("Missing session_data.json in the current directory.")

    session_data = json.loads(session_path.read_text(encoding="utf-8"))
    auth = FitatuAuthContext.from_session_data(session_data)
    client = FitatuApiClient(auth=auth)

    print("Fitatu Library Example")
    print("======================")
    print("User ID:", auth.fitatu_user_id or "unknown")
    print("Lifecycle:", client.describe_auth_state()["lifecycle_state"])
    print()
    foods = client.search_food("banan", limit=5)
    print(f"Found {len(foods)} foods")
    for item in foods[:5]:
        print("-", item.get("name"))


if __name__ == "__main__":
    main()
