"""FitatuLibrary end to end: HTTP-level token handling and planner flows on the fake server."""

from __future__ import annotations

import base64
import json
import os
import stat
import time
from pathlib import Path
from typing import Any
from unittest.mock import Mock, patch

import pytest
from fake_server import RECORDED_USER_ID, FakeFitatuServer

from fitatu_api import FitatuApiClient, FitatuAuthError, FitatuLibrary

HTTP = "fitatu_api.client.requests.Session.request"
DAY = "2027-01-03"
ROW_ID = "8f0c2a1e-8f3b-11ef-9c8e-0242ac120002"  # a UUID v1, as the server requires


def _library(*args: Any, **kwargs: Any) -> FitatuLibrary:
    """``FitatuLibrary(...)``: deprecated, so the warning is asserted rather than silenced."""
    with pytest.warns(DeprecationWarning, match="FitatuTools"):
        return FitatuLibrary(*args, **kwargs)


def _jwt(payload: dict[str, Any]) -> str:
    return "h." + base64.urlsafe_b64encode(json.dumps(payload).encode()).decode().rstrip("=") + ".s"


def _reply(body: Any, status: int = 200) -> Mock:
    response = Mock(status_code=status, text=json.dumps(body), headers={})
    response.json.return_value = body
    return response


EXPIRED = _jwt({"id": int(RECORDED_USER_ID), "exp": int(time.time()) - 60})
FRESH = _jwt({"id": int(RECORDED_USER_ID), "exp": int(time.time()) + 3600})


def _server(**request: Any) -> Mock:
    if request["url"].endswith("/token/refresh"):
        assert request["json"] == {"refresh_token": "r1"}, "the refresh token was sent twice"
        return _reply({"token": FRESH, "refreshToken": "r2"})
    return _reply({"id": 1})


def test_an_expired_bearer_is_refreshed_once_and_the_new_pair_is_kept(tmp_path: Path) -> None:
    session_path = tmp_path / "session.json"
    session = {"bearer_token": EXPIRED, "refresh_token": "r1", "fitatu_user_id": RECORDED_USER_ID}
    lib = _library(session, session_path=session_path)

    with patch(HTTP, side_effect=_server) as http:
        assert lib.get_recipe_via_api(recipe_id=1)["status"] == "ok"
        assert lib.get_recipe_via_api(recipe_id=1)["status"] == "ok"

    assert [call.kwargs["url"].rsplit("/", 2)[-2:] for call in http.call_args_list] == [
        ["token", "refresh"],
        ["recipes", "1"],
        ["recipes", "1"],
    ]
    assert http.call_args.kwargs["headers"]["authorization"] == f"Bearer {FRESH}"
    exported = lib.export_session_context(include_tokens=True)
    assert (exported["bearer_token"], exported["refresh_token"]) == (FRESH, "r2")
    assert (session["bearer_token"], session["refresh_token"]) == (FRESH, "r2")
    saved = json.loads(session_path.read_text(encoding="utf-8"))
    assert (saved["bearer_token"], saved["refresh_token"]) == (FRESH, "r2")
    if os.name != "nt":
        assert stat.S_IMODE(session_path.stat().st_mode) == 0o600


def test_a_camel_case_session_does_not_get_its_old_pair_back_after_a_refresh() -> None:
    session = {"bearerToken": EXPIRED, "refreshToken": "r1", "fitatu_user_id": RECORDED_USER_ID}
    lib = _library(session)

    with patch(HTTP, side_effect=_server):
        assert lib.get_recipe_via_api(recipe_id=1)["status"] == "ok"

    assert "bearerToken" not in session and "refreshToken" not in session
    reloaded = _library(session).export_session_context(include_tokens=True)
    assert (reloaded["bearer_token"], reloaded["refresh_token"]) == (FRESH, "r2")


def test_session_path_tokens_win_over_session_data(tmp_path: Path) -> None:
    session_path = tmp_path / "session.json"
    session_path.write_text(json.dumps({"bearer_token": FRESH, "refresh_token": "r2"}), encoding="utf-8")
    session = {"bearer_token": EXPIRED, "refresh_token": "r1", "fitatu_user_id": RECORDED_USER_ID}

    with patch(HTTP, return_value=_reply({"id": 1})) as http:
        assert _library(session, session_path=session_path).get_recipe_via_api(recipe_id=1)["status"] == "ok"

    assert http.call_args.kwargs["headers"]["authorization"] == f"Bearer {FRESH}"
    assert (session["bearer_token"], session["refresh_token"]) == (FRESH, "r2")


def test_from_session_finds_the_file_like_the_client_and_writes_tokens_back(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    session_path = tmp_path / "session.json"
    session_path.write_text(
        json.dumps({"bearer_token": EXPIRED, "refresh_token": "r1", "fitatu_user_id": RECORDED_USER_ID, "app_locale": "en_GB"}),
        encoding="utf-8",
    )
    monkeypatch.setenv("FITATU_SESSION", str(session_path))

    with pytest.warns(DeprecationWarning, match="FitatuTools"):
        lib = FitatuLibrary.from_session()
    with patch(HTTP, side_effect=_server), lib:
        assert lib.get_recipe_via_api(recipe_id=1)["status"] == "ok"

    saved = json.loads(session_path.read_text(encoding="utf-8"))
    assert (saved["bearer_token"], saved["refresh_token"], saved["app_locale"]) == (FRESH, "r2", "en_GB")
    assert lib.session_data["app_locale"] == "en_GB"


def test_one_client_built_from_the_constructor_options_serves_every_call() -> None:
    session = {"bearer_token": FRESH, "fitatu_user_id": RECORDED_USER_ID}
    lib = _library(session, base_url="https://de-de.fitatu.com/api", timeout_seconds=5)

    with patch("fitatu_api.facade.FitatuApiClient", wraps=FitatuApiClient) as built, patch(
        HTTP, return_value=_reply({"id": 1})
    ) as http:
        lib.get_recipe_via_api(recipe_id=1)
        lib.get_daily_targets_via_api()

    built.assert_called_once()
    assert [(call.kwargs["url"].split("/api/")[0], call.kwargs["timeout"]) for call in http.call_args_list] == [
        ("https://de-de.fitatu.com", 5),
        ("https://de-de.fitatu.com", 5),
    ]


def test_search_uses_the_session_search_locale_unless_one_is_given() -> None:
    session = {"bearer_token": FRESH, "fitatu_user_id": RECORDED_USER_ID, "app_searchlocale": "de_DE"}
    lib = _library(session)

    with patch(HTTP, return_value=_reply([])) as http:
        lib.search_food_via_api(phrase="apfel")
        lib.search_food_via_api(phrase="jablko", locale="pl_PL")

    assert [call.kwargs["params"]["locale"] for call in http.call_args_list] == ["de_DE", "pl_PL"]


def test_a_missing_session_file_raises_like_the_client(tmp_path: Path) -> None:
    with pytest.raises(FitatuAuthError, match="fitatu-api login"):
        FitatuLibrary.from_session(tmp_path / "missing.json")


def test_add_read_and_remove_a_row_on_the_fake_server() -> None:
    server = FakeFitatuServer()
    server.auth.bearer_token = server.auth.refresh_token = None  # mirrored into session_data after each call
    with patch("fitatu_api.facade.FitatuApiClient", return_value=server):
        lib = _library({})
        added = lib.add_custom_item_to_day_meal_via_api(
            target_date=DAY, meal_type="snack", name="Apple", calories=52, protein_g=0.3, fat_g=0.2, carbs_g=14,
            row_id=ROW_ID,
        )
    retried = lib.add_custom_item_to_day_meal_via_api(
        target_date=DAY, meal_type="snack", name="Apple", calories=52, protein_g=0.3, fat_g=0.2, carbs_g=14, row_id=ROW_ID
    )
    row_id = added["result"]["row_id"]
    summary = lib.get_day_summary_via_api(target_date=DAY)["result"]
    removed = lib.remove_day_item_via_api(target_date=DAY, meal_type="snack", item_id=row_id)
    again = lib.remove_day_item_via_api(target_date=DAY, meal_type="snack", item_id=row_id)

    assert (added["status"], retried["status"], row_id) == ("ok", "ok", ROW_ID)
    snack = next(meal for meal in summary["meals"] if meal["meal_key"] == "snack")
    assert [item["row_id"] for item in snack["items"]] == [row_id]
    assert removed["status"] == "ok"
    assert (again["status"], again["result"]["reason"]) == ("partial", "item_not_found")
    json.dumps([added, summary, removed, again])
