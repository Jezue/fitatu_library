"""Tests for ``python -m fitatu_api login``."""

from __future__ import annotations

import json
import os
from pathlib import Path
from unittest.mock import patch

import pytest

from fitatu_api import FitatuApiClient, FitatuAuthContext, FitatuAuthError
from fitatu_api.__main__ import main


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


def test_login_failure_exits_non_zero_without_writing(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("FITATU_EMAIL", "me@example.com")
    monkeypatch.setenv("FITATU_PASSWORD", "wrong")
    target = tmp_path / "s.json"
    error = FitatuAuthError("Login failed with status 401", status_code=401)

    with patch("fitatu_api.__main__.FitatuApiClient.login", side_effect=error):
        assert main(["login", "-o", str(target)]) == 1

    assert not target.exists()
