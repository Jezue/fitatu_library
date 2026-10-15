"""example.py, demo.py and the fixture recorder's scrub, run offline."""

from __future__ import annotations

import base64
import builtins
import importlib.util
import json
import os
import runpy
import sys
import zipfile
from datetime import date, timedelta
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import patch

import pytest
import requests
from fake_server import RECORDED_USER_ID, FakeFitatuServer, recorded

from fitatu_api import (
    FitatuApiClient,
    FitatuApiError,
    FitatuAuthContext,
    FitatuAuthError,
    FitatuMaintenanceError,
    FitatuTools,
    __version__,
)
from fitatu_api.agent import TOOLS

ROOT = Path(__file__).resolve().parents[1]
if not (ROOT / "demo.py").exists():
    pytest.skip("demo.py, example.py and scripts/ are not in the sdist", allow_module_level=True)
sys.path.insert(0, str(ROOT))
import demo  # noqa: E402
import example  # noqa: E402

TODAY = date.today()
PAYLOAD = json.dumps({"user_id": RECORDED_USER_ID, "exp": 4102444800}).encode()  # expires in 2100
SET_WATER = str([tool["name"] for tool in TOOLS].index("set_water") + 1)  # its menu number
JWT = "e30." + base64.urlsafe_b64encode(PAYLOAD).decode().rstrip("=") + ".signature"


@pytest.fixture()
def fake(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> FakeFitatuServer:
    """Route every client's requests to the fake; the session file lives in tmp_path."""
    day = recorded("day_three_rows")
    day["dietPlan"]["snack"]["items"][0]["eaten"] = True  # 250 kcal eaten, the other two rows planned
    server = FakeFitatuServer(day)
    server.routes[("GET", f"/users/{RECORDED_USER_ID}/settings-new/{TODAY}")] = recorded("settings_new")
    server.routes[("POST", "/login")] = {"token": JWT, "refresh_token": "refresh"}

    def request(client: FitatuApiClient, method: str, path: str, **kwargs: Any) -> Any:
        if client.auth.bearer_token == "expired":
            raise FitatuAuthError(f"{method} {path} -> 401", status_code=401)
        return server.request(method, path, **kwargs)

    monkeypatch.setattr(FitatuApiClient, "request", request)
    monkeypatch.setenv("FITATU_SESSION", str(tmp_path / "session.json"))
    monkeypatch.setenv("FITATU_EMAIL", "user@example.com")
    monkeypatch.setenv("FITATU_PASSWORD", "test-password")
    return server


def answer(monkeypatch: pytest.MonkeyPatch, *answers: str) -> None:
    replies = iter(answers)
    monkeypatch.setattr(builtins, "input", lambda prompt="": next(replies))


# -- example.py ---------------------------------------------------------------------------


@pytest.mark.parametrize("bearer", [None, "expired"])
def test_example_logs_in_when_the_session_is_missing_or_dead(
    fake: FakeFitatuServer, tmp_path: Path, capsys: pytest.CaptureFixture[str], bearer: str | None
) -> None:
    session = tmp_path / "session.json"
    if bearer:
        session.write_text(json.dumps({"bearer_token": bearer, "fitatu_user_id": RECORDED_USER_ID}))

    assert example.main() == 0

    saved = json.loads(session.read_text())
    assert saved["bearer_token"] == JWT and saved["base_url"] == "https://pl-pl.fitatu.com/api"
    if os.name != "nt":
        assert session.stat().st_mode & 0o777 == 0o600
    out = capsys.readouterr().out
    assert "Logging in to save a new session." in out
    # Targets are the recorded (synthetic) goals; the snack row is eaten, breakfast and lunch planned.
    assert out.splitlines()[-5:] == [
        f"Today, {TODAY}   target   eaten  planned",
        "energy, kcal          2000     250      143",
        "protein, g             100      10       10",
        "fat, g                  67       8        3",
        "carbohydrate, g        250      30       19",
    ]


def test_example_uses_a_working_session_without_logging_in(
    fake: FakeFitatuServer, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    (tmp_path / "session.json").write_text(json.dumps({"bearer_token": JWT, "fitatu_user_id": RECORDED_USER_ID}))
    assert example.main() == 0
    assert "Logging in" not in capsys.readouterr().out
    assert ("POST", "/login") not in {(r["method"], r["path"]) for r in fake.requests}


def test_example_reports_an_outage_in_one_line(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    def request(client: FitatuApiClient, method: str, path: str, **kwargs: Any) -> Any:
        raise FitatuMaintenanceError(f"{method} {path} -> Fitatu is in a technical break", status_code=503)

    monkeypatch.setattr(FitatuApiClient, "request", request)
    monkeypatch.setenv("FITATU_SESSION", str(tmp_path / "session.json"))
    (tmp_path / "session.json").write_text(json.dumps({"bearer_token": JWT}))
    assert example.main() == 1
    assert capsys.readouterr().out.startswith("Fitatu is not available right now: GET /users/1234567 -> ")


# -- demo.py ------------------------------------------------------------------------------


@pytest.fixture()
def demo_client(fake: FakeFitatuServer, monkeypatch: pytest.MonkeyPatch) -> FakeFitatuServer:
    client = FitatuApiClient(FitatuAuthContext(bearer_token=JWT, fitatu_user_id=RECORDED_USER_ID))
    monkeypatch.setattr(demo, "init_api", lambda: client)
    return fake


def test_demo_prompts_from_the_schema_and_runs_a_confirmed_write(
    demo_client: FakeFitatuServer, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    future = (TODAY + timedelta(days=30)).isoformat()
    answer(
        monkeypatch,
        "search_food", "jajko kurze", "two", "2",  # "two" is not a JSON integer: asked again
        "add_custom_item", future, "supper", "Kanapka", "250", "10", "8.5", "30", "", "",  # skip eaten, row_id
        "y",
        "q",
    )
    demo.main()
    out = capsys.readouterr().out
    assert "Not a JSON integer." in out
    assert "limit (optional, Enter skips it): Maximum number of hits. Default: 10." in out
    assert "meal: Meal slot. " in out and "lists exist. Allowed: breakfast, second_breakfast, lunch, dinner, snack, supper." in out
    assert '"ok": false' not in out and out.count('"ok": true') == 3  # search, add and the add's own ok
    [payload] = demo_client.sync_payloads
    assert payload[future]["dietPlan"]["supper"]["items"][0]["name"] == "Kanapka"


def test_demo_needs_a_past_date_typed_twice_and_a_yes(
    demo_client: FakeFitatuServer, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    yesterday = (TODAY - timedelta(days=1)).isoformat()
    answer(
        monkeypatch,
        "remove_items", yesterday, "some-row-id", "", "2000-01-01",  # retyped date differs
        SET_WATER, TODAY.isoformat(), "1500", TODAY.isoformat(), "n",  # by number; date retyped, then no
        "q",
    )
    demo.main()
    out = capsys.readouterr().out
    assert "remove_items changes your Fitatu diary and can overwrite or delete what is logged." in out
    assert "Not the same date; nothing was sent." in out
    assert not demo_client.sync_payloads
    assert not [r for r in demo_client.requests if r["method"] != "GET"]


# -- scripts/record_fixtures.py: calls added to the scenario -------------------------------


def _recorder() -> Any:
    spec = importlib.util.spec_from_file_location("record_fixtures", ROOT / "scripts" / "record_fixtures.py")
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_scrub_keeps_only_the_scenarios_foods_and_blanks_water() -> None:
    recorder = _recorder()
    logged = {"2027-01-03": {"dietPlan": {"breakfast": {"items": [{"productId": 11, "measureId": 1}]}}}}
    raw = {
        "uid": "987654",
        "calls": {
            "sync_add_product": {"request": {"body": logged}, "body": []},
            "foods_recent": {"request": {}, "body": [
                {"foodId": 11, "type": "PRODUCT", "name": "Jajko kurze", "userNumberOfUse": 4, "favorite": True, "lastUsed": "2026-10-01",
                 "meals": ["supper"], "measure": {"measureId": 46, "measureQuantity": 3, "measureWeight": 60, "measureEnergy": 252}},
                {"foodId": 99, "name": "Grandma's pierogi", "brand": "Home kitchen", "userNumberOfUse": 31},
            ]},
            "foods_favorites": {"request": {}, "body": [{"foodId": 98, "name": "Private smoothie"}]},
            "water_day": {"request": {}, "body": {
                "water": {"waterConsumption": 1750}, "waterGoal": 2600, "updatedAt": "2026-09-30 21:14:00",
            }},
            "water_day_after_put": {"request": {}, "body": {"water": {"waterConsumption": recorder.WATER_ML}}},
        },
    }

    scrubbed = recorder.scrub(raw)["calls"]

    # The account's last quantity, meals and use count are not catalog fields.
    assert scrubbed["foods_recent"]["body"] == [
        {"foodId": 11, "type": "PRODUCT", "name": "Jajko kurze", "measure": {"measureId": 46, "measureWeight": 60}}
    ]
    assert scrubbed["foods_favorites"]["body"] == []
    assert scrubbed["water_day"]["body"] == {"water": {"waterConsumption": 0}, "waterGoal": 0, "updatedAt": None}
    assert scrubbed["water_day_after_put"]["body"] == {"water": {"waterConsumption": recorder.WATER_ML}}
    assert recorder.leaked_values(raw, json.dumps({"calls": scrubbed}, indent=1)) == []
    # Unscrubbed, the leak guard names every personal value of the new calls.
    assert set(recorder.leaked_values(raw, json.dumps(raw, indent=1))) == {
        "987654", "Grandma's pierogi", "Home kitchen", "Private smoothie",
        "2026-09-30 21:14:00", 'waterConsumption": 1750', 'waterGoal": 2600',
    }


def test_scrub_empties_the_note_fasting_and_habits_and_keeps_catalog_recipe_hits() -> None:
    recorder = _recorder()
    raw = {
        "uid": "987654",
        "calls": {
            "day_empty": {"request": {}, "body": {"dietPlan": {}, "note": "Headache after the run", "tagsIds": [3]}},
            "search_recipes": {"request": {}, "body": [
                {"foodId": 7000001, "type": "RECIPE", "index": "LAST_USED", "name": "My own salad"},
                {"foodId": 7000002, "type": "RECIPE", "index": "SEARCH", "userNumberOfUse": 3, "favorite": True},
            ]},
            "fasting_types": {"request": {}, "body": [{"id": 2, "symbol": "16_08_hours", "currentlyInUse": True}, {"id": 3, "currentlyInUse": 0.42}]},
            "fasting_summary": {"request": {}, "body": [{"date": "2026-10-01", "eatingPeriods": [{"from": "09:15", "to": "17:15"}]}]},
            "habits_user": {"request": {}, "body": [{"completed": False, "enrolledDate": "2026-09-01", "habit": {"id": 1}}]},
            "habits_good_days": {"request": {}, "body": ["2026-10-01"]},
        },
    }

    scrubbed = recorder.scrub(raw)["calls"]

    assert scrubbed["day_empty"]["body"] == {"dietPlan": {}, "note": None, "tagsIds": []}
    assert scrubbed["search_recipes"]["body"] == [
        {"foodId": 7000002, "type": "RECIPE", "index": "SEARCH", "userNumberOfUse": 0, "favorite": False}
    ]
    assert scrubbed["fasting_types"]["body"] == [{"id": 2, "symbol": "16_08_hours", "currentlyInUse": False}, {"id": 3, "currentlyInUse": 0}]
    assert [scrubbed[name]["body"] for name in ("fasting_summary", "habits_user", "habits_good_days")] == [[], [], []]
    assert recorder.leaked_values(raw, json.dumps({"calls": scrubbed}, indent=1)) == []
    assert "Headache after the run" in recorder.leaked_values(raw, json.dumps(raw, indent=1))


class _Replies(requests.adapters.BaseAdapter):
    """A transport that answers from a list of (status, JSON body) and notes what was sent."""

    def __init__(self, *replies: tuple[int, Any]) -> None:
        super().__init__()
        self.replies, self.sent = list(replies), []

    def send(self, request: Any, **kwargs: Any) -> requests.Response:  # type: ignore[override]
        self.sent.append((request.method, request.path_url, request.headers.get("authorization")))
        status, body = self.replies.pop(0)
        response = requests.Response()
        response.status_code, response._content, response.request = status, json.dumps(body).encode(), request
        response.headers["content-type"] = "application/json"
        return response

    def close(self) -> None:
        pass


def test_the_recorder_refreshes_an_old_token_and_records_a_4xx_as_it_came() -> None:
    expired = "e30." + base64.urlsafe_b64encode(json.dumps({"user_id": RECORDED_USER_ID, "exp": 1000000000}).encode()).decode().rstrip("=") + ".s"
    client = FitatuApiClient(FitatuAuthContext(bearer_token=expired, refresh_token="refresh", fitatu_user_id=RECORDED_USER_ID))
    transport = _Replies((200, {"token": JWT, "refresh_token": "refresh"}), (404, {"code": 404, "message": "Not Found"}))
    client._http.mount("https://", transport)

    reply = _recorder().send(client, "GET", f"/users/{RECORDED_USER_ID}/measurements/2020-01-01")

    assert reply == (404, "application/json", {"code": 404, "message": "Not Found"})
    assert transport.sent == [
        ("POST", "/api/token/refresh", None),
        ("GET", f"/api/users/{RECORDED_USER_ID}/measurements/2020-01-01", f"Bearer {JWT}"),
    ]
    assert client._http.hooks["response"] == []


# -- scripts/check_contract.py: the drift check, against fakes ------------------------------


def _checker() -> Any:
    spec = importlib.util.spec_from_file_location("check_contract", ROOT / "scripts" / "check_contract.py")
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_the_drift_check_compares_keys_and_types_only() -> None:
    check = _checker()
    expected = check.shape({"list": [{"id": 1, "name": "a", "amount": {"current": "1.0"}}, {"id": 2.5, "brand": None}], "2027-01-03": 5, "note": None})
    # List items merge, a date key is any key, int and float are one type, null is any type.
    assert expected == {"list": [{"id": "number", "name": "str", "amount": {"current": "str"}, "brand": "null"}], "*": "number", "note": "null"}
    actual = check.shape({"list": [{"id": "1", "name": None, "extra": 1}], "2027-06-01": 7, "note": "text"})
    assert check.compare(expected, actual) == [
        ("missing", "list[].amount"), ("missing", "list[].brand"), ("new", "list[].extra"), ("type", "list[].id: number -> str"),
    ]
    # Meal names count as one key; an empty list or object (PHP's empty map) says nothing.
    assert check.shape({"breakfast": {"items": []}, "supper": {"items": [{"x": 1}]}}) == {"*": {"items": [{"x": "number"}]}}
    assert check.compare(check.shape({"weights": {"2020-01-01": 70}}), check.shape({"weights": []})) == []
    assert check.compare(check.shape([{"a": 1}]), check.shape({"a": 1})) == [("type", "(reply): list -> object")]


def _drift_check_against_replies(
    monkeypatch: pytest.MonkeyPatch, user_id: str, day: str, *, premium: bool
) -> tuple[Any, dict[str, Any], dict[tuple[str, str], Any], list[tuple[str, str, bool]]]:
    """The drift check, its routes, the replies FitatuApiClient.request gives (each route's expected
    one, the account's plan as ``premium`` says) and the requests sent."""
    check = _checker()
    monkeypatch.setenv("FITATU_SESSION_JSON", json.dumps({"bearer_token": JWT, "fitatu_user_id": user_id}))
    recording = json.loads((ROOT / "tests" / "fixtures" / "api_recording.json").read_text(encoding="utf-8"))
    examples = json.loads((ROOT / "tests" / "fixtures" / "contract_shapes.json").read_text(encoding="utf-8"))
    routes = check.probes(recording, examples, {"user_id": user_id, "day": day, "app_os": "WEB", "app_version": "4.13.1"})
    assert len(routes) == len(check.RECORDED) + len(examples)
    replies = {(path, json.dumps(query, sort_keys=True)): reply for path, query, _, reply in routes.values()}
    subscriptions, _, _, example = routes["subscriptions"]
    plans = [{**example["subscriptions"][0], "startDate": "2000-01-01", "endDate": "2999-12-31"}] if premium else []
    replies[(subscriptions, "null")] = {"subscriptions": plans}
    sent: list[tuple[str, str, bool]] = []

    def request(client: FitatuApiClient, method: str, path: str, *, params: Any = None, include_auth: bool = True, **kwargs: Any) -> Any:
        sent.append((method, path, include_auth))
        reply = replies[(path, json.dumps(params, sort_keys=True))]
        if isinstance(reply, Exception):
            raise reply
        return json.loads(json.dumps(reply))

    monkeypatch.setattr(FitatuApiClient, "request", request)
    return check, routes, replies, sent


def test_the_drift_check_names_routes_and_keys_never_values(monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    user_id, day = "7654321", "2027-06-01"
    check, routes, replies, sent = _drift_check_against_replies(monkeypatch, user_id, day, premium=True)
    recording = json.loads((ROOT / "tests" / "fixtures" / "api_recording.json").read_text(encoding="utf-8"))
    assert check.main(["--day", day]) == 0
    assert capsys.readouterr().out.endswith(f"no drift in {len(routes)} routes\n")
    assert {method for method, _, _ in sent} == {"GET"}
    assert not [path for _, path, _ in sent if recording["userId"] in path]  # the account's id in place of the recording's
    assert [path for _, path, auth in sent if not auth] == []  # upgrade-application too: it answers 401 without the token
    assert f"/diet-and-activity-plan/{user_id}/day/{day}" in {path for _, path, _ in sent}

    secret = "Sentinel pierogi 9431"
    search = routes["search_food"]
    replies[(search[0], json.dumps(search[1], sort_keys=True))] = [
        {**{key: value for key, value in hit.items() if key != "measure"}, "name": secret, "energy": "84"} for hit in search[3]
    ]
    product = routes["product_details"]
    replies[(product[0], "null")] = FitatuApiError(f"GET {product[0]} -> 404: {secret}", status_code=404)
    settings = routes["settings_new"]
    replies[(settings[0], "null")] = {**settings[3], "secretField": secret}
    assert check.main(["--day", day, "--show-new"]) == 1
    out = capsys.readouterr().out
    assert "DRIFT search_food\n    missing [].measure\n    type [].energy: number -> str\n" in out
    assert "DRIFT product_details: the request failed with status 404\n" in out
    assert "ok settings_new (1 new keys)\n    new secretField\n" in out
    assert out.endswith(f"2 of {len(routes)} routes drifted\n")
    assert secret not in out and user_id not in out

    replies[(routes["day_empty"][0], "null")] = FitatuAuthError("GET -> 401", status_code=401)
    assert check.main(["--day", day]) == 2  # at the first route
    assert capsys.readouterr().err == "stopped at day_empty: FitatuAuthError (status 401)\n"


def test_the_drift_check_skips_premium_routes_and_account_keys_on_a_free_account(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    user_id, day = "7654321", "2027-06-01"
    check, routes, replies, sent = _drift_check_against_replies(monkeypatch, user_id, day, premium=False)
    # A free account's day: its meals lack recommendedPercent, which a missing key elsewhere would not be.
    day_path, _, _, day_reply = routes["day_empty"]
    meals = {meal: {key: value for key, value in fields.items() if key != "recommendedPercent"} for meal, fields in day_reply["dietPlan"].items()}
    replies[(day_path, "null")] = {**day_reply, "dietPlan": meals}
    assert check.main(["--day", day]) == 0
    out = capsys.readouterr().out
    premium_only = sorted(check.PREMIUM_ONLY)
    assert premium_only and all(f"skipped (premium) {name}\n" in out for name in premium_only) and "ok day_empty\n" in out
    assert not {routes[name][0] for name in premium_only} & {path for _, path, _ in sent}
    assert out.endswith(f"no drift in {len(routes) - len(premium_only)} routes, {len(premium_only)} skipped (premium)\n")
    # Any other missing key is still drift.
    settings_path, _, _, settings = routes["settings_new"]
    replies[(settings_path, "null")] = {key: value for key, value in settings.items() if key != "date"}
    assert check.main(["--day", day]) == 1
    assert "DRIFT settings_new\n    missing date\n" in capsys.readouterr().out


# -- scripts/eval_agent.py: the harness, without the API -----------------------------------


def _eval_agent() -> Any:
    spec = importlib.util.spec_from_file_location("eval_agent", ROOT / "scripts" / "eval_agent.py")
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules["eval_agent"] = module  # its dataclass looks the module up
    spec.loader.exec_module(module)
    return module


def test_eval_agent_without_a_key_says_how_to_run_it(monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    for name in ("ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN"):
        monkeypatch.delenv(name, raising=False)
    assert _eval_agent().main([]) == 2
    err = capsys.readouterr().err
    assert "ANTHROPIC_API_KEY=" in err and "kolacja-slot-pl" in err


def test_no_eval_task_passes_when_the_model_does_nothing() -> None:
    harness = _eval_agent()
    assert 20 <= len(harness.TASKS) <= 30
    for task_id, (_, checks) in harness.TASKS.items():
        tools = FitatuTools.demo()
        assert harness.score(checks, harness.Run(tools, tools.get_today()["date"])), task_id


def test_a_scripted_model_run_is_scored_from_its_calls_and_the_diary() -> None:
    harness = _eval_agent()

    def reply(stop: str, *content: Any) -> Any:
        return SimpleNamespace(stop_reason=stop, content=list(content), usage=SimpleNamespace(input_tokens=10, output_tokens=5))

    banana = {"date": "TODAY", "meal": "supper", "food_id": 2000004, "food_type": "PRODUCT", "quantity": 1, "measure_id": 46, "eaten": True}
    replies = iter([
        reply("tool_use", SimpleNamespace(type="tool_use", id="t1", name="get_today", input={})),
        reply("tool_use", SimpleNamespace(type="tool_use", id="t2", name="add_food", input=banana)),
        reply("end_turn", SimpleNamespace(type="text", text="Zapisałem banana (116 kcal) w Przekąsce.")),
    ])
    sent: list[dict[str, Any]] = []

    def create(**request: Any) -> Any:
        sent.append(request)
        response = next(replies)
        for block in response.content:  # the model learns today's date from get_today
            if getattr(block, "input", None) and block.input.get("date") == "TODAY":
                block.input["date"] = FitatuTools.demo().get_today()["date"]
        return response

    run, usage = harness.run_task(SimpleNamespace(messages=SimpleNamespace(create=create)), "claude-opus-5-5", "medium", "Zjadłem banana na przekąskę.")
    assert [name for name, _, _ in run.calls] == ["get_today", "add_food"] and usage == {"input_tokens": 30, "output_tokens": 15}
    assert harness.score(harness.TASKS["banana-supper-pl"][1], run) == []
    assert harness.score(harness.TASKS["kolacja-slot-pl"][1], run)  # nothing in the Kolacja slot
    assert sent[0]["system"].startswith("Fitatu calorie counter") and sent[0]["output_config"] == {"effort": "medium"}
    assert sent[-1]["messages"][-2]["content"][0]["is_error"] is False  # the add's result went back to the model


# -- scripts/build_mcpb.py and mcpb/: the Claude Desktop bundle ----------------------------


def test_the_mcpb_bundle_names_this_version_its_tools_and_the_options_it_passes(tmp_path: Path) -> None:
    spec = importlib.util.spec_from_file_location("build_mcpb", ROOT / "scripts" / "build_mcpb.py")
    assert spec and spec.loader
    builder = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(builder)
    bundle = builder.build(tmp_path)
    with zipfile.ZipFile(bundle) as archive:
        assert sorted(archive.namelist()) == ["LICENSE", "manifest.json", "pyproject.toml", "server/main.py"]
        manifest = json.loads(archive.read("manifest.json"))
        pyproject = archive.read("pyproject.toml").decode()
    assert bundle.name == f"fitatu-{__version__}.mcpb" and manifest["version"] == __version__
    assert f'"fitatu-api[mcp]=={__version__}"' in pyproject  # uv run installs the release the bundle was built for
    assert [tool["name"] for tool in manifest["tools"]] == [tool["name"] for tool in TOOLS]
    assert (manifest["manifest_version"], manifest["server"]["type"], manifest["server"]["entry_point"]) == ("0.4", "uv", "server/main.py")
    env = manifest["server"]["mcp_config"]["env"]
    assert env == {"FITATU_SESSION": "${user_config.session_file}", "FITATU_MCP_READ_ONLY": "${user_config.read_only}"}
    assert {key: option["type"] for key, option in manifest["user_config"].items()} == {"session_file": "file", "read_only": "boolean"}


@pytest.mark.parametrize(("checkbox", "flags"), [("true", ["--read-only"]), ("false", []), (None, [])])
def test_the_mcpb_entry_point_runs_fitatu_mcp_with_the_checkbox(monkeypatch: pytest.MonkeyPatch, checkbox: str | None, flags: list[str]) -> None:
    pytest.importorskip("mcp")
    if checkbox is None:
        monkeypatch.delenv("FITATU_MCP_READ_ONLY", raising=False)
    else:
        monkeypatch.setenv("FITATU_MCP_READ_ONLY", checkbox)
    with patch("fitatu_api.mcp_server.main") as serve:
        runpy.run_path(str(ROOT / "mcpb" / "server" / "main.py"), run_name="__main__")
    serve.assert_called_once_with(flags)


@pytest.mark.parametrize(
    ("given", "expected"),
    [("${HOME}/.fitatu/session.json", "{home}/.fitatu/session.json"), ("/x/session.json", "/x/session.json"),
     ("", None), ("${user_config.session_file}", None), (None, None)],
)
def test_the_mcpb_entry_point_expands_the_session_path(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, given: str | None, expected: str | None
) -> None:
    pytest.importorskip("mcp")
    monkeypatch.setenv("HOME", str(tmp_path))
    if given is None:
        monkeypatch.delenv("FITATU_SESSION", raising=False)
    else:
        monkeypatch.setenv("FITATU_SESSION", given)
    with patch("fitatu_api.mcp_server.main"):
        runpy.run_path(str(ROOT / "mcpb" / "server" / "main.py"), run_name="__main__")
    assert os.environ.get("FITATU_SESSION") == (expected and expected.format(home=tmp_path))
