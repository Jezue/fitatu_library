"""Tests for ``python -m fitatu_api login`` and ``import-session``."""

from __future__ import annotations

import base64
import io
import json
import os
from pathlib import Path
from typing import Any
from unittest.mock import Mock, patch

import pytest

from fitatu_api import FitatuApiClient, FitatuAuthContext
from fitatu_api.__main__ import main

HTTP = "fitatu_api.client.requests.Session.request"


def _client() -> FitatuApiClient:
    auth = FitatuAuthContext(bearer_token="jwt", refresh_token="refresh", fitatu_user_id="42")
    return FitatuApiClient(auth=auth)


def test_login_saves_a_private_session_file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("FITATU_EMAIL", "me@example.com")
    monkeypatch.setenv("FITATU_PASSWORD", "secret")
    target = tmp_path / "nested" / "session_data.json"

    with patch("fitatu_api.__main__.FitatuApiClient.login", return_value=_client()) as login:
        assert main(["login", "-o", str(target)]) == 0

    login.assert_called_once()
    assert login.call_args.args == ("me@example.com", "secret")
    session = json.loads(target.read_text())
    assert (session["bearer_token"], session["refresh_token"], session["fitatu_user_id"]) == ("jwt", "refresh", "42")
    restored = FitatuAuthContext.from_session_data(session)
    assert restored.bearer_token == "jwt"
    assert restored.fitatu_user_id == "42"
    if os.name != "nt":
        assert target.stat().st_mode & 0o777 == 0o600


def test_login_prompts_when_env_is_not_set(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("FITATU_EMAIL", raising=False)
    monkeypatch.delenv("FITATU_PASSWORD", raising=False)
    monkeypatch.setattr("builtins.input", lambda prompt: "typed@example.com")
    monkeypatch.setattr("fitatu_api.__main__.getpass.getpass", lambda prompt: "typed-secret")

    with patch("fitatu_api.__main__.FitatuApiClient.login", return_value=_client()) as login:
        assert main(["login", "-o", str(tmp_path / "s.json")]) == 0

    assert login.call_args.args == ("typed@example.com", "typed-secret")


def test_login_failure_exits_non_zero_without_writing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("FITATU_EMAIL", "me@example.com")
    monkeypatch.setenv("FITATU_PASSWORD", "wrong")
    target = tmp_path / "s.json"
    rejected = Mock(status_code=422, text='{"code": 422, "message": "Username or password are invalid"}', headers={})

    with patch(HTTP, return_value=rejected):
        assert main(["login", "-o", str(target)]) == 1

    assert not target.exists()
    assert "`fitatu-api import-session --help`" in capsys.readouterr().err


def _jwt(payload: dict[str, Any]) -> str:
    return "h." + base64.urlsafe_b64encode(json.dumps(payload).encode()).decode().rstrip("=") + ".s"


def _reply(body: Any, status: int = 200) -> Mock:
    response = Mock(status_code=status, text=json.dumps(body), headers={})
    response.json.return_value = body
    return response


BEARER = _jwt({"id": 42, "exp": 4102444800})  # 2100-01-01
FRESH = _jwt({"id": 42, "exp": 4102448400})
EXPIRED = _jwt({"id": 42, "exp": 1})
REFRESH = "r" * 128
GERMAN_PROFILE = {"id": "42", "locale": "de_DE", "searchLocale": "de_DE", "storageLocale": "de_DE", "timezone": "Europe/Berlin"}


def _refreshed() -> Mock:
    # Fitatu answers a refresh with a new bearer and the same refresh token.
    return _reply({"token": FRESH, "refresh_token": REFRESH})


def _import(monkeypatch: pytest.MonkeyPatch, stdin: str, *args: str) -> int:
    monkeypatch.setattr("sys.stdin", io.StringIO(stdin))
    return main(["import-session", *args])


@pytest.mark.parametrize(
    "pasted",
    [
        # What the web app keeps in localStorage.user (the README snippet copies only the first three).
        {"token": BEARER, "refresh_token": REFRESH, "id": 42, "locale": "pl_PL", "email": "x@privaterelay.appleid.com", "nickname": "Ann"},
        # The library's own session file.
        {"bearer_token": BEARER, "refresh_token": REFRESH, "fitatu_user_id": "42", "base_url": "https://pl-pl.fitatu.com/api"},
    ],
    ids=["web_app_user", "session_file"],
)
def test_import_session_checks_the_tokens_and_saves_only_session_fields(
    pasted: dict[str, Any], tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    target = tmp_path / "s.json"

    with patch(HTTP, side_effect=[_refreshed(), _reply(GERMAN_PROFILE)]) as http:
        assert _import(monkeypatch, json.dumps(pasted), "-o", str(target)) == 0

    assert http.call_args_list[0].kwargs["json"] == {"refresh_token": REFRESH}
    assert http.call_args.kwargs["headers"]["authorization"] == f"Bearer {FRESH}"
    session = json.loads(target.read_text(encoding="utf-8"))
    assert session == {
        "session_context_schema_version": "1.1",
        "base_url": "https://de-de.fitatu.com/api",
        "app_locale": "de_DE",
        "app_searchlocale": "de_DE",
        "app_storagelocale": "de_DE",
        "app_timezone": "Europe/Berlin",
        "fitatu_user_id": "42",
        "bearer_token": FRESH,
        "refresh_token": REFRESH,
    }
    if os.name != "nt":
        assert target.stat().st_mode & 0o777 == 0o600
    out, err = capsys.readouterr()
    assert out == f"Saved session for user 42 to {target}\n"
    assert REFRESH not in out + err and BEARER not in out + err and FRESH not in out + err


def test_import_session_refreshes_an_expired_bearer(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    target = tmp_path / "s.json"

    with patch(HTTP, side_effect=[_refreshed(), _reply(GERMAN_PROFILE)]) as http:
        assert _import(monkeypatch, json.dumps({"token": EXPIRED, "refresh_token": REFRESH, "id": 42}), "-o", str(target)) == 0

    assert http.call_args_list[0].kwargs["json"] == {"refresh_token": REFRESH}
    assert json.loads(target.read_text(encoding="utf-8"))["bearer_token"] == FRESH


def test_import_session_writes_to_the_resolved_session_path(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    target = tmp_path / "from-env.json"
    monkeypatch.setenv("FITATU_SESSION", str(target))

    with patch(HTTP, side_effect=[_refreshed(), _reply(GERMAN_PROFILE)]):
        assert _import(monkeypatch, json.dumps({"token": BEARER, "refresh_token": REFRESH, "id": 42})) == 0

    assert FitatuApiClient.from_session().auth.fitatu_user_id == "42"


@pytest.mark.parametrize(
    ("stdin", "replies", "code"),
    [
        ("not json", [], 2),
        ('["a list"]', [], 2),
        (json.dumps({"token": BEARER, "id": 42}), [], 2),
        (json.dumps({"refresh_token": REFRESH, "id": 42}), [], 2),
        (json.dumps({"token": 123, "refresh_token": REFRESH, "id": 42}), [], 2),
        (json.dumps({"token": ["a"], "refresh_token": REFRESH}), [], 2),
        (json.dumps({"token": BEARER, "refresh_token": 5, "id": 42}), [], 2),
        ("[" * 200_000, [], 2),
        # The bearer still works, but Fitatu refuses the refresh token (its answer, checked live).
        (
            json.dumps({"token": BEARER, "refresh_token": REFRESH, "id": 42}),
            [_reply({"code": 401, "message": "JWT Refresh Token Not Found"}, status=401)],
            1,
        ),
    ],
    ids=[
        "bad_json",
        "not_an_object",
        "no_refresh_token",
        "no_token",
        "token_not_text",
        "token_not_text_no_id",
        "refresh_token_not_text",
        "nested_too_deep",
        "refresh_token_rejected",
    ],
)
def test_import_session_failures_exit_non_zero_without_writing(
    stdin: str,
    replies: list[Mock],
    code: int,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    target = tmp_path / "s.json"

    with patch(HTTP, side_effect=replies) as http:
        assert _import(monkeypatch, stdin, "-o", str(target)) == code

    assert http.call_count == len(replies)
    assert not target.exists()
    out, err = capsys.readouterr()
    assert out == "" and len(err.strip().splitlines()) == 1
    assert REFRESH not in err and BEARER not in err and EXPIRED not in err


def test_import_session_says_what_to_paste_on_a_terminal(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    class Terminal(io.StringIO):
        def isatty(self) -> bool:
            return True

    monkeypatch.setattr("sys.stdin", Terminal("not json"))

    assert main(["import-session"]) == 2
    assert capsys.readouterr().err.startswith("Paste the JSON the browser snippet copied, then press Enter and Ctrl-D")
