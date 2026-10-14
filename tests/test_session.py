"""Session file: location, loading, write-back of refreshed tokens, region host."""

from __future__ import annotations

import base64
import json
import os
from pathlib import Path
from typing import Any
from unittest.mock import Mock, patch

import pytest
from fake_server import RECORDED_USER_ID, recorded

from fitatu_api import (
    FitatuApiClient,
    FitatuAuthContext,
    FitatuAuthError,
    FitatuTokenStore,
    FitatuValidationError,
)
from fitatu_api.__main__ import main
from fitatu_api.constants import WEB_CLIENT_PROFILE

HTTP = "fitatu_api.client.requests.Session.request"


def _jwt(payload: dict[str, Any]) -> str:
    return "h." + base64.urlsafe_b64encode(json.dumps(payload).encode()).decode().rstrip("=") + ".s"


def _reply(body: Any, status: int = 200) -> Mock:
    response = Mock(status_code=status, text=json.dumps(body), headers={})
    response.json.return_value = body
    return response


@pytest.fixture(autouse=True)
def _isolated_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", home.as_posix())
    monkeypatch.setenv("USERPROFILE", home.as_posix())
    for name in ("FITATU_SESSION", "FITATU_SESSION_PATH", "FITATU_SESSION_JSON", "FITATU_CLIENT_PROFILE"):
        monkeypatch.delenv(name, raising=False)
    return home


def _write_session(path: Path, **extra: Any) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    session = {
        "bearer_token": "old-bearer",
        "refresh_token": "old-refresh",
        "fitatu_user_id": "1234567",
        "app_locale": "en_GB",
        "app_searchlocale": "de_DE",
        "app_storagelocale": "de_DE",
        **extra,
    }
    path.write_text(json.dumps(session), encoding="utf-8")
    return path


def test_from_session_resolves_argument_then_env_then_home(
    _isolated_home: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    default = _write_session(_isolated_home / ".fitatu" / "session.json", fitatu_user_id="1")
    alias = _write_session(tmp_path / "alias.json", fitatu_user_id="2")
    env = _write_session(tmp_path / "env.json", fitatu_user_id="3")
    explicit = _write_session(tmp_path / "explicit.json", fitatu_user_id="4")

    assert FitatuApiClient.from_session().auth.fitatu_user_id == "1"
    monkeypatch.setenv("FITATU_SESSION_PATH", str(alias))
    assert FitatuApiClient.from_session().auth.fitatu_user_id == "2"
    monkeypatch.setenv("FITATU_SESSION", str(env))
    assert FitatuApiClient.from_session().auth.fitatu_user_id == "3"
    client = FitatuApiClient.from_session(explicit)
    assert client.auth.fitatu_user_id == "4"
    assert client.token_store is not None and client.token_store.path == explicit
    assert default.exists()


def test_session_json_in_the_environment_comes_before_the_file_and_is_never_written(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    file = _write_session(tmp_path / "session.json", fitatu_user_id="1")
    monkeypatch.setenv("FITATU_SESSION", str(file))
    monkeypatch.setenv("FITATU_SESSION_JSON", json.dumps({"bearer_token": "env-bearer", "refresh_token": "env-refresh", "fitatu_user_id": "2"}))

    client = FitatuApiClient.from_session()
    with patch(HTTP, side_effect=[_reply({}, status=401), _reply({"token": "new-bearer"}), _reply({})]) as http:
        client.request("GET", "/users/2")

    assert http.call_args_list[1].kwargs["json"] == {"refresh_token": "env-refresh"}
    assert client.auth.bearer_token == "new-bearer" and client.token_store is None
    assert json.loads(file.read_text(encoding="utf-8"))["bearer_token"] == "old-bearer"
    assert FitatuApiClient.from_session(file).auth.fitatu_user_id == "1"  # a path given still wins


@pytest.mark.parametrize("value", ['{"bearer_token": "secret-tok', '["secret-tok"]'])
def test_a_broken_session_json_does_not_echo_it(value: str, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("FITATU_SESSION_JSON", value)
    with pytest.raises(FitatuAuthError, match=r"\$FITATU_SESSION_JSON") as excinfo:
        FitatuApiClient.from_session()
    assert "secret-tok" not in str(excinfo.value)


def test_client_profile_takes_the_keys_the_environment_overrides(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("FITATU_CLIENT_PROFILE", json.dumps({"app_version": "4.99.0", "api_secret": "new-secret"}))
    client = FitatuApiClient(FitatuAuthContext(bearer_token="t"))
    with patch(HTTP, return_value=_reply({})) as http:
        client.request("GET", "/ping")

    headers = http.call_args.kwargs["headers"]
    assert (headers["app-version"], headers["api-secret"], headers["api-key"]) == ("4.99.0", "new-secret", WEB_CLIENT_PROFILE["api_key"])
    assert WEB_CLIENT_PROFILE["app_version"] != "4.99.0"  # the constant itself is untouched


@pytest.mark.parametrize("value", ["not json", '["x"]', '{"api_secrt": "x"}', '{"api_secret": 5}', '{"api_secret": "x\\n"}'])
def test_a_broken_client_profile_is_refused_without_echoing_it(value: str, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("FITATU_CLIENT_PROFILE", value)
    with pytest.raises(FitatuValidationError, match="FITATU_CLIENT_PROFILE must be a JSON object") as excinfo:
        FitatuAuthContext()
    assert value not in str(excinfo.value)


def test_missing_session_tells_how_to_create_one() -> None:
    with pytest.raises(FitatuAuthError, match="fitatu-api login"):
        FitatuApiClient.from_session()


def test_refreshed_tokens_are_merged_into_the_session_file(tmp_path: Path) -> None:
    path = _write_session(tmp_path / "sessions" / "session.json", base_url="https://de-de.fitatu.com/api")
    client = FitatuApiClient.from_session(path)
    replies = [_reply({}, status=401), _reply({"token": "new-bearer", "refreshToken": "new-refresh"}), _reply({})]

    with patch(HTTP, side_effect=replies):
        client.request("GET", "/users/1234567")

    saved = json.loads(path.read_text(encoding="utf-8"))
    assert (saved["bearer_token"], saved["refresh_token"]) == ("new-bearer", "new-refresh")
    assert saved["fitatu_user_id"] == "1234567"
    assert saved["app_storagelocale"] == "de_DE"
    assert saved["base_url"] == "https://de-de.fitatu.com/api"
    if os.name != "nt":
        assert path.stat().st_mode & 0o777 == 0o600
    assert [p.name for p in path.parent.iterdir()] == ["session.json"]  # no temp file left behind


def _urls(http: Mock) -> list[str]:
    return [call.kwargs["url"].rsplit("/", 1)[-1] for call in http.call_args_list]


def test_tokens_another_process_saved_are_used_instead_of_a_spent_refresh_token(tmp_path: Path) -> None:
    path = _write_session(tmp_path / "session.json")
    first, second = FitatuApiClient.from_session(path), FitatuApiClient.from_session(path)
    rotated = _reply({"token": "new-bearer", "refreshToken": "new-refresh"})

    with patch(HTTP, side_effect=[_reply({}, status=401), rotated, _reply({})]):
        first.request("GET", "/users/1234567")
    with patch(HTTP, side_effect=[_reply({}, status=401), _reply({})]) as http:
        second.request("GET", "/users/1234567")

    assert _urls(http) == ["1234567", "1234567"]  # old-refresh is not sent again
    assert http.call_args.kwargs["headers"]["authorization"] == "Bearer new-bearer"
    assert second.auth.refresh_token == "new-refresh"


def test_refresh_rejected_because_another_process_won_the_race_uses_its_tokens(tmp_path: Path) -> None:
    path = _write_session(tmp_path / "session.json")
    client = FitatuApiClient.from_session(path)

    def server(**kwargs: Any) -> Mock:
        if kwargs["url"].endswith("/token/refresh"):
            FitatuTokenStore(path).save(bearer_token="their-bearer", refresh_token="their-refresh")
        return _reply({}, status=401 if kwargs["headers"].get("authorization") != "Bearer their-bearer" else 200)

    with patch(HTTP, side_effect=server) as http:
        client.request("GET", "/users/1234567")

    assert _urls(http) == ["1234567", "refresh", "1234567"]
    assert (client.auth.bearer_token, client.auth.refresh_token) == ("their-bearer", "their-refresh")


def test_tokens_of_another_account_in_the_file_are_neither_used_nor_overwritten(tmp_path: Path) -> None:
    mine = _jwt({"id": 1234567, "exp": 4102444800})
    path = _write_session(tmp_path / "session.json", bearer_token=mine)
    client = FitatuApiClient.from_session(path)
    # `fitatu-api login` for another account, to the same file, while this client runs.
    theirs = _jwt({"id": 7654321, "exp": 4102444800})
    FitatuTokenStore(path).save(bearer_token=theirs, refresh_token="their-refresh", fitatu_user_id="7654321")
    refreshed = _reply({"token": _jwt({"id": 1234567, "exp": 4102448400})})

    with patch(HTTP, side_effect=[_reply({}, status=401), refreshed, _reply({})]) as http:
        client.request("GET", "/users/1234567")

    assert http.call_args_list[1].kwargs["json"] == {"refresh_token": "old-refresh"}
    assert theirs not in str(http.call_args_list)
    saved = json.loads(path.read_text(encoding="utf-8"))
    assert (saved["bearer_token"], saved["fitatu_user_id"]) == (theirs, "7654321")


def test_a_client_that_does_not_persist_keeps_its_own_newer_tokens(tmp_path: Path) -> None:
    client = FitatuApiClient.from_session(_write_session(tmp_path / "session.json"), persist_tokens=False)
    refreshed = [_reply({"token": "b2", "refreshToken": "r2"}), _reply({"token": "b3", "refreshToken": "r3"})]

    with patch(HTTP, side_effect=[_reply({}, status=401), refreshed[0], _reply({})]):
        client.request("GET", "/users/1234567")
    with patch(HTTP, side_effect=[_reply({}, status=401), refreshed[1], _reply({})]) as http:
        client.request("GET", "/users/1234567")

    # The untouched file still holds old-bearer/old-refresh; they must not replace b2/r2.
    assert http.call_args_list[1].kwargs["json"] == {"refresh_token": "r2"}
    assert client.auth.bearer_token == "b3"


def test_token_store_path_expands_the_home_directory(_isolated_home: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)
    client = FitatuApiClient(
        auth=FitatuAuthContext(bearer_token="b", refresh_token="r"), token_store_path="~/.fitatu/tokens.json"
    )
    client._persist_tokens_to_store()

    assert (_isolated_home / ".fitatu" / "tokens.json").exists()
    assert not (tmp_path / "~").exists()


def test_token_store_supplies_the_user_id_the_context_lacks(tmp_path: Path) -> None:
    path = _write_session(tmp_path / "tokens.json")
    assert FitatuTokenStore(path).load()["fitatu_user_id"] == "1234567"

    client = FitatuApiClient(auth=FitatuAuthContext(), token_store_path=path)

    assert client.auth.fitatu_user_id == "1234567"
    assert client.auth.bearer_token == "old-bearer"


def test_base_url_comes_from_argument_then_session_then_storage_locale() -> None:
    assert FitatuApiClient(FitatuAuthContext()).base_url == "https://pl-pl.fitatu.com/api"
    assert FitatuApiClient(FitatuAuthContext(app_storage_locale="de_DE")).base_url == "https://de-de.fitatu.com/api"
    from_session = FitatuAuthContext.from_session_data(
        {"app_storagelocale": "de_DE", "base_url": "https://cz-cz.fitatu.com/api"}
    )
    assert FitatuApiClient(from_session).base_url == "https://cz-cz.fitatu.com/api"
    assert FitatuApiClient(from_session, base_url="https://es-es.fitatu.com/api/").base_url == "https://es-es.fitatu.com/api"


def test_session_export_carries_host_and_locales_but_not_identity() -> None:
    client = FitatuApiClient(FitatuAuthContext(app_storage_locale="de_DE", fitatu_user_id="1"))

    exported = client.auth.to_session_data()

    assert exported["session_context_schema_version"] == "1.1"
    assert exported["base_url"] == "https://de-de.fitatu.com/api"
    assert exported["app_storagelocale"] == "de_DE"
    assert not {"api_key", "api_secret", "app_uuid", "user_agent", "bearer_token"} & set(exported)


def test_describe_auth_state_does_not_claim_persistence_without_a_store() -> None:
    assert FitatuApiClient(FitatuAuthContext()).describe_auth_state()["persist_tokens"] is False


def test_login_reads_locales_from_the_profile_and_picks_the_region_host(tmp_path: Path) -> None:
    profile = recorded("user_info")
    login_reply = _reply({"token": _jwt({"id": int(RECORDED_USER_ID)}), "refresh_token": "r"})
    store = tmp_path / "session.json"

    with patch(HTTP, side_effect=[login_reply, _reply(profile)]) as http:
        client = FitatuApiClient.login("me@example.com", "secret", token_store_path=store)

    profile_call = http.call_args_list[1].kwargs
    assert (profile_call["method"], profile_call["url"]) == ("GET", f"https://pl-pl.fitatu.com/api/users/{RECORDED_USER_ID}")
    assert (client.auth.app_locale, client.auth.app_search_locale, client.auth.app_storage_locale) == (
        "en_GB",
        "pl_PL",
        "pl_PL",
    )
    assert client.base_url == "https://pl-pl.fitatu.com/api"
    saved = json.loads(store.read_text(encoding="utf-8"))
    assert saved["fitatu_user_id"] == RECORDED_USER_ID
    assert saved["app_locale"] == "en_GB"
    assert saved["base_url"] == "https://pl-pl.fitatu.com/api"


def test_login_switches_to_the_storage_locale_host_unless_one_was_given() -> None:
    login_reply = _reply({"token": _jwt({"id": 5})})
    german = _reply({"id": "5", "locale": "de_DE", "searchLocale": "de_DE", "storageLocale": "de_DE"})

    with patch(HTTP, side_effect=[login_reply, german]):
        assert FitatuApiClient.login("a@b.c", "pw").base_url == "https://de-de.fitatu.com/api"
    with patch(HTTP, side_effect=[login_reply, german]):
        pinned = FitatuApiClient.login("a@b.c", "pw", base_url="https://pl-pl.fitatu.com/api")
    assert pinned.base_url == "https://pl-pl.fitatu.com/api"


def test_cli_login_writes_to_the_resolved_session_path(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    target = tmp_path / "from-env.json"
    monkeypatch.setenv("FITATU_SESSION", str(target))
    monkeypatch.setenv("FITATU_EMAIL", "me@example.com")
    monkeypatch.setenv("FITATU_PASSWORD", "secret")
    client = FitatuApiClient(FitatuAuthContext(bearer_token="jwt", refresh_token="r", fitatu_user_id="42"))

    with patch("fitatu_api.__main__.FitatuApiClient.login", return_value=client) as login:
        assert main(["login"]) == 0

    assert login.call_args.kwargs["base_url"] is None
    assert json.loads(target.read_text(encoding="utf-8"))["base_url"] == "https://pl-pl.fitatu.com/api"
    assert FitatuApiClient.from_session().auth.bearer_token == "jwt"
