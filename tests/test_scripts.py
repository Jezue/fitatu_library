"""example.py, demo.py and the fixture recorder's scrub, run offline."""

from __future__ import annotations

import base64
import builtins
import importlib.util
import json
import sys
from datetime import date, timedelta
from pathlib import Path
from typing import Any

import pytest
from fake_server import RECORDED_USER_ID, FakeFitatuServer, recorded

from fitatu_api import FitatuApiClient, FitatuAuthContext, FitatuAuthError, FitatuMaintenanceError
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
    assert "meal: Meal of the day. Allowed: breakfast, second_breakfast, lunch, dinner, snack, supper." in out
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
