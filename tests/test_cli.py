"""Tests for ``python -m fitatu_api``: login, import-session, tools, call, export and mcp."""

from __future__ import annotations

import base64
import io
import json
import logging
import os
import re
import subprocess
import sys
import time
from collections.abc import Iterator
from pathlib import Path
from typing import Any
from unittest.mock import Mock, patch

import pytest
from fake_server import RECORDED_USER_ID, FakeFitatuServer, recorded

import fitatu_api
from fitatu_api import FitatuApiClient, FitatuAuthContext
from fitatu_api.__main__ import main
from fitatu_api.agent import TOOLS, anthropic_tools
from fitatu_api.auth import write_private_json

HTTP = "fitatu_api.client.requests.Session.request"
SRC = Path(__file__).resolve().parents[1] / "src"


@pytest.fixture(autouse=True)
def _library_log_level() -> Iterator[None]:
    """main() sets the level of the fitatu_api logger; put it back for the other tests."""
    yield
    logging.getLogger("fitatu_api").setLevel(logging.NOTSET)


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


def test_login_print_writes_no_file_and_prints_the_session_with_a_warning(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("FITATU_EMAIL", "me@example.com")
    monkeypatch.setenv("FITATU_PASSWORD", "secret")
    monkeypatch.setenv("FITATU_SESSION", str(tmp_path / "session.json"))

    with patch("fitatu_api.__main__.FitatuApiClient.login", return_value=_client()):
        assert main(["login", "--print"]) == 0

    out, err = capsys.readouterr()
    assert FitatuAuthContext.from_session_data(json.loads(out)).refresh_token == "refresh"
    assert "full account credential" in err and "jwt" not in err
    assert list(tmp_path.iterdir()) == []
    with pytest.raises(SystemExit):
        main(["login", "--print", "-o", str(tmp_path / "s.json")])


def test_login_prompts_on_stderr_when_env_is_not_set(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.delenv("FITATU_EMAIL", raising=False)
    monkeypatch.delenv("FITATU_PASSWORD", raising=False)
    monkeypatch.setenv("FITATU_SESSION", str(tmp_path / "session.json"))
    monkeypatch.setattr("builtins.input", lambda prompt="": prompt + "typed@example.com")
    monkeypatch.setattr("fitatu_api.__main__.getpass.getpass", lambda prompt: "typed-secret")

    with patch("fitatu_api.__main__.FitatuApiClient.login", return_value=_client()) as login:
        assert main(["login", "--print"]) == 0

    assert login.call_args.args == ("typed@example.com", "typed-secret")
    out, err = capsys.readouterr()
    assert json.loads(out)["refresh_token"] == "refresh"  # `login --print | ...` gets the JSON alone
    assert err.startswith("Fitatu e-mail: ")


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


DAY = "2027-01-03"  # the recorded day
CSV = b"Date,Meal\n2027-01-03,Lunch\n"


def _use_fake(monkeypatch: pytest.MonkeyPatch, server: FakeFitatuServer) -> None:
    """Make every from_session() (tools, export) load a client that talks to ``server``."""
    client = FitatuApiClient(FitatuAuthContext(bearer_token="t", fitatu_user_id=RECORDED_USER_ID))
    client.request = server.request  # type: ignore[method-assign]
    monkeypatch.setattr(FitatuApiClient, "from_session", lambda *args, **kwargs: client)


def test_tools_lists_every_tool_with_its_kind(capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["tools"]) == 0
    kinds = {line.split()[0]: line.split()[1] for line in capsys.readouterr().out.splitlines()}
    assert list(kinds) == [tool["name"] for tool in TOOLS]
    assert (kinds["get_day"], kinds["add_food"], kinds["remove_items"]) == ("read", "write", "destructive")

    assert main(["tools", "--json"]) == 0
    assert json.loads(capsys.readouterr().out) == anthropic_tools()


def test_call_prints_the_envelope_of_a_read_tool(monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    _use_fake(monkeypatch, FakeFitatuServer(recorded("day_three_rows")))

    assert main(["call", "get_day", json.dumps({"date": DAY})]) == 0
    result = json.loads(capsys.readouterr().out)
    assert result["ok"] is True and result["data"]["date"] == DAY

    assert main(["call", "get_day", '{"date": "today"}']) == 1
    assert json.loads(capsys.readouterr().out)["error"]["error_type"] == "validation"


def test_call_runs_a_write_tool_only_with_allow_writes(monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    server = FakeFitatuServer(recorded("day_three_rows"))
    _use_fake(monkeypatch, server)
    args = ["call", "add_custom_item", json.dumps({"date": DAY, "meal": "snack", "name": "Test item", "energy_kcal": 100})]

    assert main(args) == 1
    out, err = capsys.readouterr()
    error = json.loads(out)["error"]
    assert error["error_type"] == "validation" and "add_custom_item" not in error["message"].split("tools: ")[1]
    assert err == "add_custom_item changes the diary: run it with --allow-writes.\n"
    assert server.requests == []

    assert main([*args, "--allow-writes"]) == 0
    assert json.loads(capsys.readouterr().out)["data"]["ok"] is True
    assert server.sync_payloads


def test_call_without_a_session_file_reports_an_auth_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("FITATU_SESSION", str(tmp_path / "missing.json"))

    assert main(["call", "get_today"]) == 1
    assert json.loads(capsys.readouterr().out)["error"]["error_type"] == "auth"


def test_call_refuses_arguments_that_are_not_json(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit) as exit_info:
        main(["call", "get_day", "{date: 2027-01-03}"])
    assert exit_info.value.code == 2
    assert "argument JSON: not JSON" in capsys.readouterr().err


def test_export_writes_a_private_file_and_replaces_one_only_with_overwrite(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    server = FakeFitatuServer()
    server.routes[("GET", f"/diet-plan/{RECORDED_USER_ID}/export")] = CSV
    _use_fake(monkeypatch, server)
    target = tmp_path / "diary.csv"
    args = ["export", "--from", DAY, "--to", "2027-01-04", "-o", str(target)]

    assert main(args) == 0
    assert target.read_bytes() == CSV
    if os.name != "nt":
        assert target.stat().st_mode & 0o777 == 0o600
    assert server.requests[-1]["params"] == {"format": "csv", "fromDate": DAY, "toDate": "2027-01-04"}
    assert capsys.readouterr().out == f"Saved the csv export ({len(CSV)} bytes) to {target}\n"

    target.write_bytes(b"kept")
    sent = len(server.requests)
    assert main(args) == 2
    assert target.read_bytes() == b"kept" and len(server.requests) == sent  # refused before downloading
    assert "--overwrite" in capsys.readouterr().err

    assert main([*args, "--overwrite", "--format", "txt"]) == 0
    assert target.read_bytes() == CSV and server.requests[-1]["params"]["format"] == "txt"
    assert sorted(path.name for path in tmp_path.iterdir()) == ["diary.csv"]  # no temporary file left


@pytest.mark.parametrize("output", ["missing/diary.csv", "."])
def test_export_refuses_a_path_it_cannot_write_before_downloading(
    output: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    server = FakeFitatuServer()
    _use_fake(monkeypatch, server)
    target = str(tmp_path / output)

    assert main(["export", "--from", DAY, "--to", DAY, "-o", target, "--overwrite"]) == 2
    assert server.requests == [] and list(tmp_path.iterdir()) == []
    assert capsys.readouterr().err == f"{target} is a directory or its directory does not exist; give a file path.\n"


def test_export_of_an_empty_range_writes_nothing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    server = FakeFitatuServer()
    server.routes[("GET", f"/diet-plan/{RECORDED_USER_ID}/export")] = None  # the API answers 204
    _use_fake(monkeypatch, server)
    target = tmp_path / "diary.csv"

    assert main(["export", "--from", DAY, "--to", DAY, "-o", str(target)]) == 1
    assert not target.exists()
    assert capsys.readouterr().err == f"Nothing is logged from {DAY} to {DAY}; no file was written.\n"


def test_export_reports_an_api_error(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    _use_fake(monkeypatch, FakeFitatuServer())

    assert main(["export", "--from", "2027-01-04", "--to", DAY, "-o", str(tmp_path / "x.csv")]) == 1
    assert capsys.readouterr().err == "Export failed: from_date 2027-01-04 is after to_date 2027-01-03\n"
    assert not (tmp_path / "x.csv").exists()


def test_export_without_premium_says_so_in_one_line(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    _use_fake(monkeypatch, FakeFitatuServer(free=True))

    assert main(["export", "--from", DAY, "--to", DAY, "-o", str(tmp_path / "x.csv")]) == 1
    assert capsys.readouterr().err == "Export failed: the diary export needs Fitatu Premium; `fitatu-api doctor` says whether this account has it.\n"
    assert not (tmp_path / "x.csv").exists()


@pytest.mark.parametrize(
    ("flags", "options"),
    [([], {"read_only": False, "call_timeout": 45}), (["--read-only", "--call-timeout", "20"], {"read_only": True, "call_timeout": 20})],
)
def test_mcp_runs_the_mcp_server_with_its_options(flags: list[str], options: dict[str, Any]) -> None:
    pytest.importorskip("mcp")
    with patch("fitatu_api.mcp_server.serve") as serve:
        assert main(["mcp", *flags]) == 0
    args = serve.call_args.args[0]
    assert {key: getattr(args, key) for key in options} == options


def test_mcp_refuses_an_unknown_tool_in_tools(capsys: pytest.CaptureFixture[str]) -> None:
    with patch("fitatu_api.mcp_server.serve") as serve, pytest.raises(SystemExit):
        main(["mcp", "--tools", "get_day,get_week"])
    serve.assert_not_called()
    assert "unknown tool(s): get_week" in capsys.readouterr().err


def test_mcp_without_the_extra_says_how_to_install_it(capsys: pytest.CaptureFixture[str]) -> None:
    with patch.dict(sys.modules, {"mcp": None}):  # None makes `import mcp` fail
        sys.modules.pop("fitatu_api.mcp_server", None)  # imported fresh; patch.dict puts it back
        assert main(["mcp"]) == 1
    assert "fitatu-api[mcp]" in capsys.readouterr().err


@pytest.mark.parametrize(("flags", "env", "level"), [([], "info", logging.INFO), (["--log-level", "debug"], "error", logging.DEBUG)])
def test_log_level_comes_from_the_flag_else_the_environment(
    flags: list[str], env: str, level: int, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("FITATU_LOG_LEVEL", env)
    assert main([*flags, "tools"]) == 0
    assert logging.getLogger("fitatu_api").level == level


def test_an_unknown_log_level_in_the_environment_stops_the_command(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("FITATU_LOG_LEVEL", "loud")
    with pytest.raises(SystemExit, match="FITATU_LOG_LEVEL must be one of DEBUG, INFO, WARNING, ERROR, got 'LOUD'"):
        main(["tools"])


def test_mcp_gets_the_log_level() -> None:
    pytest.importorskip("mcp")
    with patch("fitatu_api.mcp_server.serve") as serve, patch("fitatu_api.__main__.setup_logging") as setup:
        assert main(["--log-level", "info", "mcp", "--read-only"]) == 0
    setup.assert_called_once_with("INFO")  # before the server starts, which logs from then on
    assert serve.call_args.args[0].read_only


def test_log_lines_go_to_stderr_and_stdout_keeps_only_the_result(tmp_path: Path) -> None:
    session = tmp_path / "session.json"
    session.write_text(json.dumps({"bearer_token": "t", "refresh_token": "r", "fitatu_user_id": "42"}), encoding="utf-8")
    env = {**os.environ, "FITATU_SESSION": str(session), "PYTHONPATH": str(SRC)}
    env.pop("FITATU_SESSION_JSON", None)  # it would come before the file
    # A refused date: call() answers before anything is sent, so no request leaves the machine.
    run = subprocess.run(
        [sys.executable, "-m", "fitatu_api", "--log-level", "DEBUG", "call", "get_day", '{"date": "today"}'],
        capture_output=True, text=True, env=env, timeout=60, check=False,
    )
    assert run.returncode == 1
    assert json.loads(run.stdout)["error"]["error_type"] == "validation"
    assert " DEBUG fitatu_api.agent: get_day failed (validation): date must be " in run.stderr


USER = "1234567"


def _session(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, **extra: Any) -> Path:
    path = tmp_path / "session.json"
    bearer = _jwt({"id": int(USER), "exp": 4102444800})
    write_private_json(path, {"bearer_token": bearer, "refresh_token": REFRESH, "fitatu_user_id": USER, **extra})
    monkeypatch.setenv("FITATU_SESSION", str(path))
    monkeypatch.delenv("FITATU_SESSION_JSON", raising=False)
    monkeypatch.delenv("FITATU_CLIENT_PROFILE", raising=False)
    return path


def test_version(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit) as exit_info:
        main(["--version"])
    assert exit_info.value.code == 0
    assert capsys.readouterr().out == f"fitatu-api {fitatu_api.__version__}\n"


def test_status_describes_the_session_without_a_request_or_a_token(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    path = _session(tmp_path, monkeypatch)
    # status shows the home directory as ~, and on Windows tmp_path is under it.
    for name in ("HOME", "USERPROFILE"):
        monkeypatch.setenv(name, str(tmp_path / "home"))

    with patch(HTTP, side_effect=AssertionError("status sends nothing")):
        assert main(["status"]) == 0

    out = capsys.readouterr().out
    assert f"session:       {path}" in out and ("(0600)" in out or os.name == "nt")
    assert "user id:       1234567\n" in out and "API host:      https://pl-pl.fitatu.com/api\n" in out
    assert "bearer token:  valid until 2100-01-01 00:00 UTC" in out and "refresh token: present\n" in out
    assert "client:        WEB 4.13.1\n" in out
    assert REFRESH not in out and "h.ey" not in out


@pytest.mark.skipif(os.name == "nt", reason="no POSIX permissions")
def test_status_flags_a_session_file_others_can_read(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    _session(tmp_path, monkeypatch).chmod(0o644)
    assert main(["status"]) == 0
    assert "(0644: not private, run chmod 600 " in capsys.readouterr().out


def test_status_check_reads_the_profile_once(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    _session(tmp_path, monkeypatch)
    with patch(HTTP, return_value=_reply(GERMAN_PROFILE)) as http:
        assert main(["status", "--check"]) == 0
    assert http.call_args.kwargs["url"].endswith(f"/users/{USER}")
    assert "Fitatu:        the session works (profile read)\n" in capsys.readouterr().out

    rejected = [_reply({}, status=401), _reply({"code": 401, "message": "JWT Refresh Token Not Found"}, status=401)]
    with patch(HTTP, side_effect=rejected):
        assert main(["status", "--check"]) == 1
    assert "Fitatu:        auth: " in capsys.readouterr().out


def test_status_names_the_environment_or_says_there_is_no_session(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("FITATU_SESSION", str(tmp_path / "missing.json"))
    assert main(["status"]) == 1
    assert "fitatu-api login" in capsys.readouterr().err

    monkeypatch.setenv("FITATU_SESSION_JSON", json.dumps({"bearer_token": BEARER, "fitatu_user_id": "42"}))
    assert main(["status"]) == 0
    out = capsys.readouterr().out
    assert "session:       $FITATU_SESSION_JSON (refreshed tokens are not saved)\n" in out
    assert "refresh token: missing: log in again\n" in out


def test_logout_deletes_the_session_file_only_when_confirmed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    path = _session(tmp_path, monkeypatch)
    for answer in ("n", EOFError()):
        monkeypatch.setattr("builtins.input", Mock(side_effect=[answer]))
        assert main(["logout"]) == 1
        assert path.exists()

    monkeypatch.setattr("builtins.input", lambda prompt: "y")
    assert main(["logout"]) == 0
    assert not path.exists()
    assert main(["logout", "--yes"]) == 0  # nothing left to delete
    assert capsys.readouterr().out.endswith(f"No session file at {path}.\n")

    _session(tmp_path, monkeypatch)
    assert main(["logout", "--yes"]) == 0 and not path.exists()


def test_logout_cannot_delete_a_session_from_the_environment(monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    monkeypatch.setenv("FITATU_SESSION_JSON", "{}")
    assert main(["logout", "--yes"]) == 1
    assert "$FITATU_SESSION_JSON" in capsys.readouterr().err


def _doctor_replies(
    *, issued_at: float, newest: str = "4.13.1", refresh: Mock | None = None, profile: Mock | None = None, plans: Mock | None = None
) -> list[Mock]:
    token = _jwt({"id": int(USER), "iat": int(issued_at), "exp": int(issued_at) + 3600})
    return [
        refresh or _reply({"token": token, "refresh_token": REFRESH}),
        profile or _reply(GERMAN_PROFILE),
        plans or _reply({"subscriptions": [{"startDate": "2020-01-01", "endDate": "2099-01-31", "amount": "9.99", "lastOriginalTransactionId": "7"}]}),
        _reply({"newestApplicationVersion": newest, "upgradeApplication": 0}),
    ]


def test_doctor_passes_a_healthy_setup_and_prints_nothing_personal(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    path = _session(tmp_path, monkeypatch)

    with patch(HTTP, side_effect=_doctor_replies(issued_at=time.time())) as http:
        assert main(["doctor"]) == 0

    refresh, profile, plans, upgrade = http.call_args_list
    assert refresh.kwargs["url"].endswith("/token/refresh") and profile.kwargs["url"].endswith(f"/users/{USER}")
    assert plans.kwargs["url"].endswith(f"/users/{USER}/subscriptions")
    assert upgrade.kwargs["url"] == "https://pl-pl.fitatu.com/api/upgrade-application/WEB/4.13.1"
    assert "authorization" in upgrade.kwargs["headers"]  # without the token Fitatu answers 401
    out = capsys.readouterr().out
    assert [line.split()[0] for line in out.splitlines()[1:]] == ["ok"] * 6
    assert "client:     WEB 4.13.1 is the newest web app version" in out
    assert "ok    plan:       premium\n" in out and "2099" not in out and "9.99" not in out
    assert USER not in out and REFRESH not in out and "h.ey" not in out
    assert json.loads(path.read_text(encoding="utf-8"))["bearer_token"] != _jwt({"id": int(USER), "exp": 4102444800})  # saved


def test_doctor_reports_each_problem_and_fails(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    _session(tmp_path, monkeypatch)
    replies = _doctor_replies(
        issued_at=time.time(),
        newest="4.14.0",
        refresh=_reply({"code": 401, "message": "JWT Refresh Token Not Found"}, status=401),
        profile=_reply({"message": "Forbidden"}, status=403),
        plans=_reply({"subscriptions": []}),
    )

    with patch(HTTP, side_effect=replies):
        assert main(["doctor"]) == 1

    out = capsys.readouterr().out
    assert "FAIL  refresh:    failed: auth: POST /token/refresh -> 401" in out
    assert "FAIL  profile:    not read: forbidden: GET /users/<user> -> 403: Forbidden" in out
    assert "WARN  client:     Fitatu's web app is at 4.14.0, this library sends WEB 4.13.1;" in out
    assert "ok    plan:       no premium plan: premium-only features answer 403" in out
    assert USER not in out


def test_doctor_keeps_the_user_id_off_stderr_when_fitatu_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], caplog: pytest.LogCaptureFixture
) -> None:
    _session(tmp_path, monkeypatch)
    refresh, _, _, upgrade = _doctor_replies(issued_at=time.time())

    def reply(**request: Any) -> Mock:
        if "/users/" in request["url"]:  # the profile and the plan, each retried
            return _reply({"message": "busy"}, status=503)
        return refresh if request["url"].endswith("/token/refresh") else upgrade

    with patch(HTTP, side_effect=reply), patch("fitatu_api.client.time.sleep"):
        assert main(["doctor"]) == 1

    captured = capsys.readouterr()
    assert "FAIL  profile:    not read: server: GET /users/<user> -> 503" in captured.out
    # The client's warnings name the path with the id: the output goes into public issues.
    assert USER not in captured.out + captured.err and not any(USER in record.getMessage() for record in caplog.records)


@pytest.mark.parametrize(("offset", "direction"), [(-7200, "ahead of"), (300, "behind")])
def test_doctor_compares_the_clock_with_a_fresh_token(
    offset: int, direction: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    _session(tmp_path, monkeypatch)
    with patch(HTTP, side_effect=_doctor_replies(issued_at=time.time() + offset)):
        assert main(["doctor"]) == 0  # a warning, not a failure
    found = re.search(rf"WARN  clock:      (\d+) s {direction} Fitatu's", capsys.readouterr().out)
    assert found and abs(int(found[1]) - abs(offset)) <= 2


def test_doctor_without_a_session_hides_the_home_directory(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    for name in ("FITATU_SESSION", "FITATU_SESSION_PATH", "FITATU_SESSION_JSON"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("USERPROFILE", str(tmp_path))

    assert main(["doctor"]) == 1
    out = capsys.readouterr().out
    assert f"FAIL  session:    auth: no Fitatu session at ~{os.sep}.fitatu{os.sep}session.json" in out
    assert str(tmp_path) not in out
