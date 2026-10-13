"""Smallest useful fitatu-api script: today's targets next to what is logged.

Like python-garminconnect's example.py, init_api() loads the saved session
(~/.fitatu/session.json, or the file in $FITATU_SESSION). When there is none, or it no
longer works, it logs in the way `fitatu-api login` does: e-mail and password from
FITATU_EMAIL / FITATU_PASSWORD or a prompt, and a new session file (mode 0600) in the
same place.

    python example.py
"""

from __future__ import annotations

from datetime import date

from fitatu_api import (
    FitatuApiClient,
    FitatuAuthError,
    FitatuConnectionError,
    FitatuMaintenanceError,
    FitatuRateLimitError,
    FitatuTools,
)
from fitatu_api.__main__ import main as fitatu_cli


def init_api() -> FitatuApiClient:
    """Return a client whose session works, logging in first when it is missing or dead."""
    try:
        client = FitatuApiClient.from_session()
        user_id = client.auth.fitatu_user_id
        if user_id:
            # from_session() only reads the file; reading the profile shows the tokens still work.
            client.user_settings.get_profile(user_id)
            return client
    except FitatuAuthError as exc:
        print(exc)
    print("Logging in to save a new session.")
    if fitatu_cli(["login"]) != 0:  # the `fitatu-api login` command; it prints why it failed
        raise SystemExit(1)
    return FitatuApiClient.from_session()


def show_today(tools: FitatuTools) -> None:
    """Print today's energy and macro targets, what is eaten and what is still planned."""
    today = date.today().isoformat()
    targets = tools.get_targets(today)
    day = tools.get_day(today)
    eaten, logged = day["eaten_totals"], day["totals"]  # totals count planned rows too
    rows = [
        ("energy, kcal", "energy", targets["energy"]),
        ("protein, g", "protein", targets["protein"]["grams"]),
        ("fat, g", "fat", targets["fat"]["grams"]),
        ("carbohydrate, g", "carbohydrate", targets["carbohydrate"]["grams"]),
    ]
    print(f"{'Today, ' + today:<18}{'target':>8}{'eaten':>8}{'planned':>9}")
    for label, key, target in rows:
        goal = "-" if target is None else f"{target:.0f}"
        print(f"{label:<18}{goal:>8}{eaten[key]:>8.0f}{logged[key] - eaten[key]:>9.0f}")


def main() -> int:
    try:
        with init_api() as client:
            show_today(FitatuTools(client))
    except (FitatuRateLimitError, FitatuConnectionError, FitatuMaintenanceError) as exc:
        print(f"Fitatu is not available right now: {exc}")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
