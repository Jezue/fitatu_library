"""Check that Fitatu's live replies still have the structure the library and its tests expect.

    FITATU_SESSION_JSON="$(fitatu-api login --print)" python scripts/check_contract.py
    python scripts/check_contract.py --session session_data.json

It only reads. Every read route the library uses is called once, on an empty day a year
ahead (``--day``) and on public catalog items, and the structure of each reply (its keys,
the types of their values and the shape of list items, recursively) is compared with the
reply recorded in ``tests/fixtures/api_recording.json`` or, for the routes that recording
lacks, with the neutral example in ``tests/fixtures/contract_shapes.json``.

The output names routes and keys, never a value, so it can go into a public CI log. A
route whose reply lost a key, changed a value's type or failed is DRIFT, and the exit
status is 1; 2 means the check could not run (no session, rejected session, rate limit,
technical break, network). A key the expected reply lacks does not fail the check: it
breaks nothing, and the recording leaves out personal fields on purpose (``--show-new``
lists them).

Rules: null matches any type (a value is often null on an empty day) and int and float are
both "number"; an empty list or object says nothing about its contents (PHP sends an empty
map as ``[]``); keys made of digits and dashes (dates, ids) and meal names count as one
key, ``*``.

The account decides two things. On an account without a premium plan
(``user_settings.get_premium()``), the routes Fitatu keeps for Premium (``PREMIUM_ONLY``) are
not called and print "skipped (premium)". And a key that only some accounts' replies have
(``ACCOUNT_KEYS``) is not drift when it is missing.
"""

from __future__ import annotations

import argparse
import functools
import json
import re
import sys
from datetime import date, timedelta
from pathlib import Path
from typing import Any

FIXTURES = Path(__file__).resolve().parent.parent / "tests" / "fixtures"
MEALS = {"breakfast", "second_breakfast", "lunch", "dinner", "snack", "supper"}
# Recorded reads, called as recorded with the account's user id and --day in place of the recording's.
RECORDED = (
    "day_empty", "day_revisions", "search_food", "search_food_recipe", "product_details", "recipe_details",
    "summary_nutrition", "summary_nutrient_energy", "summary_top_products_energy", "summary_activities",
    "export_csv", "measurement_weight_history", "measurement_weight_chart", "measurement_body_part_waist",
    "measurement_size_summary", "settings_new", "user_info", "activities_catalog", "activity_search",
)
# Fitatu answers these with 403 on an account without Premium (checked live 2026-10-15).
PREMIUM_ONLY = {"summary_nutrient_protein", "summary_top_products_energy", "summary_top_products_protein", "export_csv"}
# Keys some accounts' replies lack, by route: a free account's meals have no recommendedPercent (checked live 2026-10-15).
ACCOUNT_KEYS = {"day_empty": {"dietPlan.*.recommendedPercent"}}


def shape(value: Any) -> Any:
    """The structure of a JSON value: an object of shapes, a list of one merged item shape, or a type."""
    if isinstance(value, dict):
        fields: dict[str, Any] = {}
        for key, item in value.items():
            key = "*" if key in MEALS or re.fullmatch(r"[\d-]+", key) else key
            fields[key] = merge(fields[key], shape(item)) if key in fields else shape(item)
        return fields
    if isinstance(value, list):
        return [functools.reduce(merge, map(shape, value))] if value else []
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "bool"
    return "number" if isinstance(value, (int, float)) else type(value).__name__


def _kind(shape_: Any) -> str:
    return "object" if isinstance(shape_, dict) else "list" if isinstance(shape_, list) else shape_


def merge(a: Any, b: Any) -> Any:
    """One shape for two values met in one place: two list items, or two keys counted as ``*``."""
    if a == "null" or a == b:
        return b
    if b == "null":
        return a
    if isinstance(a, dict) and isinstance(b, dict):
        return {key: merge(a[key], b[key]) if key in a and key in b else a.get(key, b.get(key)) for key in a | b}
    if isinstance(a, list) and isinstance(b, list):
        return [merge(a[0], b[0])] if a and b else a or b
    return "|".join(sorted(set(_kind(a).split("|")) | set(_kind(b).split("|"))))


def compare(expected: Any, actual: Any, path: str = "") -> list[tuple[str, str]]:
    """``(kind, where)`` for each difference: ``missing``, ``new`` or ``type`` (``where`` names both types)."""
    if "null" in (expected, actual) or expected in ([], {}) or actual in ([], {}):
        return []
    if isinstance(expected, dict) and isinstance(actual, dict):
        diffs = [("missing", f"{path}.{key}".lstrip(".")) for key in expected if key not in actual]
        diffs += [("new", f"{path}.{key}".lstrip(".")) for key in actual if key not in expected]
        for key in (key for key in expected if key in actual):
            diffs += compare(expected[key], actual[key], f"{path}.{key}".lstrip("."))
        return diffs
    if isinstance(expected, list) and isinstance(actual, list):
        return compare(expected[0], actual[0], f"{path}[]")
    if set(_kind(expected).split("|")) & set(_kind(actual).split("|")):
        return []
    return [("type", f"{path or '(reply)'}: {_kind(expected)} -> {_kind(actual)}")]


def probes(recording: dict[str, Any], examples: dict[str, Any], context: dict[str, str]) -> dict[str, tuple[str, Any, bool, Any]]:
    """``{name: (path, query, include_auth, expected reply)}`` for every route the check calls."""
    calls = recording["calls"]
    recorded_day = calls["day_empty"]["request"]["path"].rsplit("/", 1)[1]

    def fill(text: str) -> str:
        return text.replace(recording["userId"], context["user_id"]).replace(recorded_day, context["day"])

    found = {}
    for name in RECORDED:
        request = calls[name]["request"]
        query = {key: fill(value) if isinstance(value, str) else value for key, value in (request["query"] or {}).items()}
        found[name] = (fill(request["path"]), query or None, True, calls[name]["body"])
    for name, example in examples.items():
        query = {key: value.format_map(context) if isinstance(value, str) else value for key, value in example.get("query", {}).items()}
        found[name] = (example["path"].format_map(context), query or None, example.get("include_auth", True), example["reply"])
    return found


def main(argv: list[str] | None = None) -> int:
    from fitatu_api import (
        FitatuApiClient,
        FitatuApiError,
        FitatuAuthError,
        FitatuConnectionError,
        FitatuMaintenanceError,
        FitatuRateLimitError,
    )

    parser = argparse.ArgumentParser(description=(__doc__ or "").splitlines()[0])
    parser.add_argument("--session", help="session file (default: $FITATU_SESSION_JSON, else $FITATU_SESSION, else ~/.fitatu/session.json)")
    parser.add_argument("--day", type=date.fromisoformat, default=date.today() + timedelta(days=365), help="an empty day to read (default: a year from today)")
    parser.add_argument("--show-new", action="store_true", help="also list the keys the expected replies lack")
    args = parser.parse_args(argv)

    try:
        client = FitatuApiClient.from_session(args.session)
    except FitatuAuthError as exc:
        print(f"no session: {exc}", file=sys.stderr)
        return 2
    if not client.auth.fitatu_user_id:
        print("the session has no fitatu_user_id; run `fitatu-api login`", file=sys.stderr)
        return 2
    context = {"user_id": client.auth.fitatu_user_id, "day": args.day.isoformat(), "app_os": client.auth.app_os, "app_version": client.auth.app_version}
    recording = json.loads((FIXTURES / "api_recording.json").read_text(encoding="utf-8"))
    examples = json.loads((FIXTURES / "contract_shapes.json").read_text(encoding="utf-8"))

    routes, drift, skipped = probes(recording, examples, context), 0, 0
    with client:
        try:
            premium = client.user_settings.get_premium(context["user_id"])["active"]
        except FitatuApiError:  # unknown: check every route (a session problem stops at the first one)
            premium = True
        for name, (path, query, include_auth, expected) in routes.items():
            if name in PREMIUM_ONLY and not premium:
                skipped += 1
                print(f"skipped (premium) {name}")
                continue
            try:
                reply = client.request("GET", path, params=query, include_auth=include_auth)
            except (FitatuAuthError, FitatuRateLimitError, FitatuMaintenanceError, FitatuConnectionError) as exc:
                # Only the class and status: the message quotes the path, with the user id.
                print(f"stopped at {name}: {type(exc).__name__} (status {exc.status_code})", file=sys.stderr)
                return 2
            except FitatuApiError as exc:
                drift += 1
                print(f"DRIFT {name}: the request failed with status {exc.status_code}")
                continue
            diffs = compare(shape(expected), shape(reply))
            optional = ACCOUNT_KEYS.get(name, set())
            breaking = [f"{kind} {where}" for kind, where in diffs if kind != "new" and not (kind == "missing" and where in optional)]
            new = [where for kind, where in diffs if kind == "new"]
            drift += bool(breaking)
            print(f"{'DRIFT' if breaking else 'ok'} {name}" + (f" ({len(new)} new keys)" if new else ""))
            for line in breaking + ([f"new {where}" for where in new] if args.show_new else []):
                print(f"    {line}")
    checked = len(routes) - skipped
    outcome = f"{drift} of {checked} routes drifted" if drift else f"no drift in {checked} routes"
    print(outcome + (f", {skipped} skipped (premium)" if skipped else ""))
    return 1 if drift else 0


if __name__ == "__main__":
    sys.exit(main())
