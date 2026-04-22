"""Interactive demo script for fitatu-api."""

from __future__ import annotations

import json
import os
from datetime import date
from pathlib import Path

from fitatu_api import FitatuApiClient, FitatuApiError, FitatuAuthContext, FitatuLibrary


def _load_session() -> dict:
    session_path = Path("session_data.json")
    if not session_path.exists():
        raise SystemExit(
            "Missing session_data.json in the current directory. "
            "Create it with python fitatu_login.py --email <email> --password <password> "
            "or copy a reusable session payload there before running the demo."
        )
    return json.loads(session_path.read_text(encoding="utf-8"))


def _print_section(title: str) -> None:
    print()
    print(title)
    print("-" * len(title))


def _print_key_value(label: str, value: object) -> None:
    print(f"{label}: {value}")


def _print_intro(client: FitatuApiClient, auth: FitatuAuthContext) -> None:
    print("Fitatu Library Demo")
    print("===================")
    _print_key_value("User ID", auth.fitatu_user_id or "unknown")
    _print_key_value("Lifecycle", client.describe_auth_state()["lifecycle_state"])
    print("Showcase areas: session, planner, search, recipes, diagnostics")
    print("Session bootstrap: python fitatu_login.py --email <email> --password <password>")
    print("Planner delete model: deletedAt + reduced quantity + full-day sync + reload")
    print("Tip: choose [0] for a full guided tour.")
    print("Coverage: 4 categories, guided tour, search, planner, report, export")


def _print_top_foods(client: FitatuApiClient, phrase: str) -> None:
    _print_section(f"Food search: {phrase!r}")
    foods = client.search_food(phrase, limit=5)
    if not foods:
        print("No foods found.")
        return
    _print_key_value("Matches", len(foods))
    for item in foods[:5]:
        print("-", item.get("name"), f"(id={item.get('id') or item.get('foodId')})")


def _print_user_settings(client: FitatuApiClient, user_id: str, target_day: date) -> None:
    _print_section(f"User settings for {target_day.isoformat()}")
    settings = client.get_user_settings(user_id, day=target_day)
    if not settings:
        print("No settings returned.")
        return
    for key in ("goal", "activityLevel", "dietType", "mealsCount", "kcal"):
        if key in settings:
            _print_key_value(key, settings[key])
    _print_key_value("Top-level keys", ", ".join(sorted(settings.keys())[:10]))


def _print_recipe_catalog(client: FitatuApiClient) -> None:
    _print_section("Recipe catalog preview")
    catalog = client.get_recipes_catalog(params={"page": 1})
    if isinstance(catalog, dict):
        items = catalog.get("items") or catalog.get("data") or []
        if isinstance(items, list) and items:
            _print_key_value("Visible entries", len(items))
            for item in items[:5]:
                if isinstance(item, dict):
                    print("-", item.get("name", "<unnamed recipe>"), f"(id={item.get('id')})")
            return
        _print_key_value("Top-level keys", ", ".join(sorted(catalog.keys())[:10]))
        return
    _print_key_value("Received non-dict catalog payload", type(catalog).__name__)


def _print_probe_summary(client: FitatuApiClient, user_id: str, target_day: date) -> None:
    _print_section("Endpoint probe")
    results = client.probe_known_endpoints(user_id, target_day)
    ok_count = sum(1 for result in results if result["ok"])
    _print_key_value("Successful checks", f"{ok_count}/{len(results)}")
    for result in results:
        status = "OK" if result["ok"] else f"ERR {result['status']}"
        print(f"- {status}: {result['path']}")


def _print_session_export(lib: FitatuLibrary) -> None:
    _print_section("Reusable session export")
    exported = lib.export_session_context()
    preview = {
        "fitatu_user_id": exported.get("fitatu_user_id"),
        "api_cluster": exported.get("api_cluster"),
        "has_bearer_token": bool(exported.get("bearer_token")),
        "has_refresh_token": bool(exported.get("refresh_token")),
    }
    print(json.dumps(preview, indent=2, ensure_ascii=True))


def _print_planner_summary(client: FitatuApiClient, user_id: str, target_day: date) -> None:
    _print_section(f"Planner snapshot for {target_day.isoformat()}")
    planner_day = client.get_day_plan(user_id, target_day)
    diet_plan = planner_day.get("dietPlan") or {}
    meals = sorted(diet_plan.keys())
    _print_key_value("Meals", ", ".join(meals) if meals else "none")
    total_items = 0
    for meal_key in meals:
        meal = diet_plan.get(meal_key) or {}
        items = meal.get("items") or []
        total_items += len(items)
        print(f"- {meal_key}: {len(items)} item(s)")
    _print_key_value("Total planner items", total_items)


def _print_report_summary(client: FitatuApiClient) -> None:
    _print_section("Management report summary")
    report = client.management_report()
    _print_key_value("Lifecycle", report["lifecycle_state"])
    _print_key_value("Modules", ", ".join(report["modules"]))
    _print_key_value("Has token store", report["has_token_store"])
    _print_key_value("Has operational store", report["has_operational_store"])
    _print_key_value("Operational events", report["operational_event_count"])

    _print_section("Management report JSON")
    print(json.dumps(report, indent=2, ensure_ascii=True))


def _print_category_menu() -> None:
    print()
    print("Categories")
    print("----------")
    print("[0] Guided tour")
    print("[1] Session & Auth        auth summary, export")
    print("[2] Planner & Settings    planner snapshot, user settings")
    print("[3] Search & Catalog      foods, recipe catalog")
    print("[4] Diagnostics & Export  probe, report, session export")
    print("[q] Quit")


def _print_session_menu() -> None:
    _print_section("Session & Auth")
    print("[11] Auth summary")
    print("[12] Export reusable session context")
    print("[b] Back")


def _print_planner_menu() -> None:
    _print_section("Planner & Settings")
    print("[21] Planner snapshot for today")
    print("[22] User settings snapshot")
    print("[b] Back")


def _print_search_menu() -> None:
    _print_section("Search & Catalog")
    print("[31] Search 'banan'")
    print("[32] Search 'jogurt'")
    print("[33] Recipe catalog preview")
    print("[b] Back")


def _print_diagnostics_menu() -> None:
    _print_section("Diagnostics & Export")
    print("[41] Endpoint health probe")
    print("[42] Management report")
    print("[43] Export reusable session context")
    print("[b] Back")


def _print_auth_summary(client: FitatuApiClient) -> None:
    _print_section("Auth summary")
    state = client.describe_auth_state()
    _print_key_value("Lifecycle", state["lifecycle_state"])
    _print_key_value("Has bearer token", state["has_bearer_token"])
    _print_key_value("Has refresh token", state["has_refresh_token"])
    _print_key_value("User ID", state["fitatu_user_id"] or "unknown")
    print("Working login contract: POST /login with _username and _password.")


def _run_step(title: str, action: callable[[], None]) -> None:
    try:
        action()
    except FitatuApiError as exc:
        _print_section(title)
        print(f"Request failed: {exc}")
        if exc.status_code is not None:
            _print_key_value("Status code", exc.status_code)
    except Exception as exc:  # pragma: no cover - defensive demo UX
        _print_section(title)
        print(f"Unexpected error: {exc}")


def _run_action(client: FitatuApiClient, auth: FitatuAuthContext, choice: str) -> bool:
    today = date.today()
    lib = FitatuLibrary(session_data=auth.to_session_data(include_tokens=True))

    if choice == "11":
        _run_step("Auth summary", lambda: _print_auth_summary(client))
        return True
    if choice == "21":
        if auth.fitatu_user_id:
            _run_step(
                "Planner snapshot",
                lambda: _print_planner_summary(client, auth.fitatu_user_id or "", today),
            )
        else:
            _print_section(f"Planner snapshot for {today.isoformat()}")
            print("Session has no fitatu_user_id, skipping planner day fetch.")
        return True
    if choice == "22":
        if auth.fitatu_user_id:
            _run_step(
                "User settings",
                lambda: _print_user_settings(client, auth.fitatu_user_id or "", today),
            )
        else:
            _print_section("User settings")
            print("Session has no fitatu_user_id, skipping settings fetch.")
        return True
    if choice == "31":
        _run_step("Food search", lambda: _print_top_foods(client, "banan"))
        return True
    if choice == "32":
        _run_step("Food search", lambda: _print_top_foods(client, "jogurt"))
        return True
    if choice == "33":
        _run_step("Recipe catalog", lambda: _print_recipe_catalog(client))
        return True
    if choice == "41":
        if auth.fitatu_user_id:
            _run_step(
                "Endpoint probe",
                lambda: _print_probe_summary(client, auth.fitatu_user_id or "", today),
            )
        else:
            _print_section("Endpoint probe")
            print("Session has no fitatu_user_id, skipping probe.")
        return True
    if choice in {"12", "43"}:
        _run_step("Reusable session export", lambda: _print_session_export(lib))
        return True
    if choice == "42":
        _run_step("Management report", lambda: _print_report_summary(client))
        return True
    if choice.lower() in {"q", "quit", "exit"}:
        return False
    if choice.lower() in {"b", "back"}:
        return True

    _print_section("Unknown option")
    print("Choose one of the currently shown menu entries or q.")
    return True


def _run_non_interactive_demo(client: FitatuApiClient, auth: FitatuAuthContext) -> None:
    lib = FitatuLibrary(session_data=auth.to_session_data(include_tokens=True))
    _run_step("Auth summary", lambda: _print_auth_summary(client))
    today = date.today()
    if auth.fitatu_user_id:
        _run_step("Planner snapshot", lambda: _print_planner_summary(client, auth.fitatu_user_id or "", today))
        _run_step("User settings", lambda: _print_user_settings(client, auth.fitatu_user_id or "", today))
        _run_step("Endpoint probe", lambda: _print_probe_summary(client, auth.fitatu_user_id or "", today))
    else:
        _print_section(f"Planner snapshot for {today.isoformat()}")
        print("Session has no fitatu_user_id, skipping planner day fetch.")
    _run_step("Food search", lambda: _print_top_foods(client, "banan"))
    _run_step("Food search", lambda: _print_top_foods(client, "jogurt"))
    _run_step("Recipe catalog", lambda: _print_recipe_catalog(client))
    _run_step("Reusable session export", lambda: _print_session_export(lib))
    _run_step("Management report", lambda: _print_report_summary(client))


def _run_guided_tour(client: FitatuApiClient, auth: FitatuAuthContext) -> None:
    _print_section("Guided tour")
    print("Running the main showcase flow for this library.")
    print("Sequence: auth -> planner -> settings -> search -> catalog -> export -> report")
    _run_non_interactive_demo(client, auth)


def _run_menu(client: FitatuApiClient, auth: FitatuAuthContext) -> None:
    while True:
        _print_category_menu()
        choice = input("Select option: ").strip()
        if choice == "0":
            _run_guided_tour(client, auth)
            continue
        if choice == "1":
            _print_session_menu()
            while True:
                nested = input("Select option: ").strip()
                if nested.lower() in {"b", "back"}:
                    break
                if not _run_action(client, auth, nested):
                    return
            continue
        if choice == "2":
            _print_planner_menu()
            while True:
                nested = input("Select option: ").strip()
                if nested.lower() in {"b", "back"}:
                    break
                if not _run_action(client, auth, nested):
                    return
            continue
        if choice == "3":
            _print_search_menu()
            while True:
                nested = input("Select option: ").strip()
                if nested.lower() in {"b", "back"}:
                    break
                if not _run_action(client, auth, nested):
                    return
            continue
        if choice == "4":
            _print_diagnostics_menu()
            while True:
                nested = input("Select option: ").strip()
                if nested.lower() in {"b", "back"}:
                    break
                if not _run_action(client, auth, nested):
                    return
            continue
        if not _run_action(client, auth, choice):
            break


def main() -> None:
    """Run a broader demo that showcases the main user-facing library flows."""
    session_data = _load_session()
    auth = FitatuAuthContext.from_session_data(session_data)
    client = FitatuApiClient(auth=auth)

    _print_intro(client, auth)

    # Keep CI and pipe-friendly behavior by falling back to a scripted demo.
    if os.isatty(0):
        _run_menu(client, auth)
    else:
        _run_non_interactive_demo(client, auth)


if __name__ == "__main__":
    main()
