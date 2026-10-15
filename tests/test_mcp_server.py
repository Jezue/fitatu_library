"""fitatu-mcp: the agent tools over MCP, in process against the fake server, over stdio and over HTTP."""

from __future__ import annotations

import contextlib
import json
import logging
import os
import socket
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest

pytest.importorskip("mcp")

import anyio
import httpx2
from fake_server import RECORDED_USER_ID, FakeFitatuServer
from jsonschema import Draft202012Validator
from mcp import Client, StdioServerParameters
from mcp.client.streamable_http import streamable_http_client
from starlette.testclient import TestClient
from test_agent import ARGUMENTS, DAY, SEED, fake_with_every_reply

from fitatu_api import (
    FitatuApiClient,
    FitatuAuthContext,
    FitatuTools,
    FitatuValidationError,
    __version__,
)
from fitatu_api.agent import SYSTEM_PROMPT, TOOLS
from fitatu_api.mcp_server import READ_ONLY_NOTE, build_server, http_app, main, output_schema

SRC = Path(__file__).resolve().parents[1] / "src"
READ_ONLY = [tool["name"] for tool in TOOLS if tool["annotations"]["readOnlyHint"]]
TOKEN = "test-token-" + "x" * 32
BEARER = {"Authorization": f"Bearer {TOKEN}"}


@pytest.fixture()
def tools() -> FitatuTools:
    client = FitatuApiClient(FitatuAuthContext(bearer_token="t", fitatu_user_id=RECORDED_USER_ID))
    client.request = FakeFitatuServer(SEED).request  # type: ignore[method-assign]
    return FitatuTools(client)


def _session(server: Any, calls: dict[str, dict[str, Any]], **client: Any) -> tuple[Any, list[Any], dict[str, Any]]:
    """``(instructions, listed tools, {tool: result})`` from one client session."""

    async def run() -> tuple[Any, list[Any], dict[str, Any]]:
        async with Client(server, **client) as session:
            listed = (await session.list_tools()).tools
            results = {name: await session.call_tool(name, args) for name, args in calls.items()}
            return session.instructions, listed, results

    return anyio.run(run)


def _envelope(result: Any) -> dict[str, Any]:
    assert json.loads(result.content[0].text) == result.structured_content
    assert result.is_error is not result.structured_content["ok"]
    return result.structured_content  # type: ignore[no-any-return]


@pytest.mark.parametrize("mode", ["auto", "legacy"])  # legacy: the initialize handshake of older hosts
def test_tools_are_listed_as_defined_and_results_carry_the_envelope(tools: FitatuTools, mode: str) -> None:
    calls = {"get_day": {"date": DAY}, "add_custom_item": ARGUMENTS["add_custom_item"], "update_item": {"date": DAY}, "get_week": {}}
    instructions, listed, results = _session(build_server(lambda: tools), calls, mode=mode)
    assert instructions == SYSTEM_PROMPT
    assert [tool.model_dump(by_alias=True, exclude_none=True) for tool in listed] == [
        {
            "name": t["name"],
            "title": t["title"],
            "description": t["description"],
            "inputSchema": t["input_schema"],
            "annotations": {"title": t["title"], **t["annotations"]},
            "outputSchema": output_schema(t["name"]),
            # Claude Code then asks before every call, as a deleted recipe cannot be brought back
            **({"_meta": {"anthropic/requiresUserInteraction": True}} if t["name"] == "delete_recipe" else {}),
        }
        for t in TOOLS
    ]
    day = _envelope(results["get_day"])["data"]
    assert day["date"] == DAY and day["meals"]
    assert _envelope(results["add_custom_item"])["data"]["ok"] is True
    assert "Kanapka" in json.dumps(tools.get_day(DAY))
    assert _envelope(results["update_item"])["error"]["error_type"] == "validation"  # no row_id
    assert "'get_week' is not a tool of this server" in _envelope(results["get_week"])["error"]["message"]


def test_the_server_names_itself_and_its_instructions_fit_the_hosts_limit(tools: FitatuTools) -> None:
    async def run() -> Any:
        async with Client(build_server(lambda: tools, read_only=True)) as session:
            return session.server_info, session.instructions

    info, instructions = anyio.run(run)
    assert (info.name, info.title, info.version, info.website_url) == ("fitatu", "Fitatu", __version__, "https://github.com/Jezue/fitatu_library")
    assert [icon.src for icon in info.icons or []] == [json.loads((Path(__file__).parents[1] / "server.json").read_text())["icons"][0]["src"]]
    assert "calorie counter" in info.description and len(instructions) < 4000


def _prompts(server: Any, calls: dict[str, dict[str, str]]) -> tuple[list[Any], dict[str, str]]:
    """``(listed prompts, {prompt: text of its one message})``."""

    async def run() -> tuple[list[Any], dict[str, str]]:
        async with Client(server) as session:
            listed = (await session.list_prompts()).prompts
            return listed, {name: (await session.get_prompt(name, args)).messages[0].content.text for name, args in calls.items()}

    return anyio.run(run)


def test_prompts_are_polish_slash_commands_that_name_the_tools_to_use(tools: FitatuTools) -> None:
    calls = {"podsumowanie_dnia": {"date": "2027-01-03"}, "przeglad_tygodnia": {}, "zaloguj_posilek": {"meal": "dwa jajka na śniadanie"}}
    listed, texts = _prompts(build_server(lambda: tools), calls)
    assert [(p.name, p.title) for p in listed] == [
        ("podsumowanie_dnia", "Podsumowanie dnia"),
        ("przeglad_tygodnia", "Przegląd tygodnia"),
        ("zaloguj_posilek", "Zaloguj posiłek"),
    ]
    assert [[(a.name, bool(a.required)) for a in p.arguments or []] for p in listed] == [[("date", False)], [], [("meal", False)]]
    assert "2027-01-03" in texts["podsumowanie_dnia"] and "get_progress" in texts["podsumowanie_dnia"]
    assert "get_report" in texts["przeglad_tygodnia"] and "poniedziałku" in texts["przeglad_tygodnia"] and "Premium" in texts["przeglad_tygodnia"]
    assert "dwa jajka na śniadanie" in texts["zaloguj_posilek"] and "add_foods" in texts["zaloguj_posilek"]
    _, today = _prompts(build_server(lambda: tools), {"podsumowanie_dnia": {}, "zaloguj_posilek": {"meal": " "}})
    assert "get_today" in today["podsumowanie_dnia"] and "zapytaj mnie" in today["zaloguj_posilek"]
    # A prompt that needs a write tool is not offered where the model cannot write.
    listed, _ = _prompts(build_server(lambda: tools, read_only=True), {})
    assert [p.name for p in listed] == ["podsumowanie_dnia", "przeglad_tygodnia"]
    # Nor one whose default text names a tool that is not served (get_today, for the day).
    listed, _ = _prompts(build_server(lambda: tools, tools=["get_day", "get_progress", "get_water"]), {})
    assert listed == []


def test_tools_serves_only_the_tools_named_and_warns_about_the_ones_the_instructions_need(
    tools: FitatuTools, caplog: pytest.LogCaptureFixture
) -> None:
    with caplog.at_level(logging.WARNING, "fitatu_api.mcp_server"):
        server = build_server(lambda: tools, tools=["get_day", "add_custom_item", "search_food"])
    assert "get_today" in caplog.text and "add_custom_item" not in caplog.text
    _, listed, results = _session(server, {"get_day": {"date": DAY}, "get_today": {}})
    assert [tool.name for tool in listed] == ["get_day", "search_food", "add_custom_item"]  # TOOLS order
    assert _envelope(results["get_day"])["ok"]
    error = _envelope(results["get_today"])["error"]
    assert error["error_type"] == "validation" and "get_day, search_food, add_custom_item" in error["message"]
    caplog.clear()
    with caplog.at_level(logging.WARNING, "fitatu_api.mcp_server"):
        build_server(lambda: tools, read_only=True)  # the write tools it names are left out on purpose
    assert not caplog.text
    with pytest.raises(FitatuValidationError, match="unknown tool.*get_week"):
        build_server(lambda: tools, tools=["get_week"])


@pytest.mark.parametrize("name", list(ARGUMENTS))
def test_every_result_validates_against_its_output_schema(name: str) -> None:
    client = FitatuApiClient(FitatuAuthContext(bearer_token="t", fitatu_user_id=RECORDED_USER_ID))
    client.request = fake_with_every_reply().request  # type: ignore[method-assign]
    # The SDK client checks a result against the tool's outputSchema and raises when it does not fit.
    _, listed, results = _session(build_server(lambda: FitatuTools(client)), {name: ARGUMENTS[name]})
    (schema,) = [tool.output_schema for tool in listed if tool.name == name]
    Draft202012Validator.check_schema(schema)
    assert _envelope(results[name])["ok"], results[name]
    assert not [error.message for error in Draft202012Validator(schema).iter_errors(results[name].structured_content)]


def test_an_error_envelope_fits_the_output_schema_too(tools: FitatuTools) -> None:
    # Hosts on the TypeScript SDK may check results with isError as well.
    _, listed, results = _session(build_server(lambda: tools), {"update_item": {"date": DAY}, "add_food": {**ARGUMENTS["add_food"], "food_id": 1}})
    schemas = {tool.name: tool.output_schema for tool in listed}
    for name, result in results.items():
        assert not _envelope(result)["ok"]
        assert not [error.message for error in Draft202012Validator(schemas[name]).iter_errors(result.structured_content)], name


def test_an_error_envelope_does_not_show_the_user_id(tools: FitatuTools) -> None:
    fake = FakeFitatuServer(SEED)
    fake.free = True  # get_top_foods is Premium-only: 403 on GET /diet-plan/{user id}/summary-product/...
    fake.routes[("GET", f"/users/{RECORDED_USER_ID}/subscriptions")] = {"subscriptions": []}
    tools.client.request = fake.request  # type: ignore[method-assign]
    result = _session(build_server(lambda: tools), {"get_top_foods": ARGUMENTS["get_top_foods"]})[2]["get_top_foods"]
    assert _envelope(result)["error"]["status_code"] == 403 and "/diet-plan/<user>/" in result.content[0].text
    assert RECORDED_USER_ID not in result.content[0].text


def test_read_only_lists_and_runs_only_the_read_tools(tools: FitatuTools) -> None:
    calls = {"get_day": {"date": DAY}, "add_custom_item": ARGUMENTS["add_custom_item"]}
    instructions, listed, results = _session(build_server(lambda: tools, read_only=True), calls)
    assert instructions == SYSTEM_PROMPT + READ_ONLY_NOTE
    assert [tool.name for tool in listed] == READ_ONLY
    assert _envelope(results["get_day"])["ok"]
    error = _envelope(results["add_custom_item"])["error"]
    assert error["error_type"] == "validation" and "read-only" in error["message"]
    assert "Kanapka" not in json.dumps(tools.get_day(DAY))


def test_a_missing_session_is_an_auth_error_and_a_later_login_needs_no_restart(tools: FitatuTools, tmp_path: Path) -> None:
    session = tmp_path / "session.json"
    server = build_server(lambda: tools if session.exists() else FitatuTools.from_session(session))
    _, listed, results = _session(server, {"get_today": {}})
    assert len(listed) == len(TOOLS)  # listing needs no session
    error = _envelope(results["get_today"])["error"]
    assert error["error_type"] == "auth" and "fitatu-api login" in error["message"]
    session.write_text("{}", encoding="utf-8")
    assert _envelope(_session(server, {"get_today": {}})[2]["get_today"])["ok"]


class _SlowTools:
    """Stands in for FitatuTools and records how many calls ran at once."""

    def __init__(self) -> None:
        self.lock = threading.Lock()
        self.running = self.peak = self.calls = 0
        self.call_timeout: float | None = None
        self.budgets: list[float | None] = []

    def call(self, name: str, arguments: dict[str, Any] | None) -> dict[str, Any]:
        with self.lock:
            self.running += 1
            self.calls += 1
            self.peak = max(self.peak, self.running)
            self.budgets.append(self.call_timeout)
        time.sleep(0.05)
        with self.lock:
            self.running -= 1
        return {"ok": True, "data": {}}


def test_parallel_calls_run_one_at_a_time_on_one_instance() -> None:
    stub, opened = _SlowTools(), []

    def open_tools() -> Any:
        opened.append(stub)
        return stub

    async def run() -> None:
        async with Client(build_server(open_tools)) as session, anyio.create_task_group() as group:
            for _ in range(4):  # hosts send tool calls in parallel
                group.start_soon(session.call_tool, "get_today", {})

    anyio.run(run)
    assert (stub.calls, stub.peak, len(opened)) == (4, 1, 1)


def test_the_time_a_call_waited_behind_others_comes_off_its_time_limit() -> None:
    stub = _SlowTools()

    async def run() -> None:
        async with Client(build_server(lambda: stub, call_timeout=10)) as session, anyio.create_task_group() as group:
            for _ in range(4):
                group.start_soon(session.call_tool, "get_today", {})

    anyio.run(run)
    budgets = [budget for budget in stub.budgets if budget is not None]
    assert len(budgets) == 4 and budgets == sorted(budgets, reverse=True)
    assert budgets[0] <= 10 and budgets[-1] <= 10 - 3 * 0.05  # each waited for the 50 ms calls before it


def test_call_timeout_0_serves_every_call_without_a_time_limit(capsys: pytest.CaptureFixture[str]) -> None:
    with patch("fitatu_api.mcp_server.build_server") as build, patch("fitatu_api.mcp_server.anyio.run"):
        main(["--demo", "--call-timeout", "0"])
    assert build.call_args.kwargs["call_timeout"] is None
    stub = _SlowTools()
    stub.call_timeout = 45  # FitatuTools.from_session's default, which None must not leave in place

    async def run() -> None:
        async with Client(build_server(lambda: stub, call_timeout=None)) as session:
            await session.call_tool("get_today", {})

    anyio.run(run)
    assert stub.budgets == [None]
    with pytest.raises(SystemExit):
        main(["--call-timeout", "-1"])
    assert "--call-timeout: must be 0 or more, got -1" in capsys.readouterr().err


@pytest.mark.parametrize("flags", [[], ["--read-only"]])
def test_fitatu_mcp_over_stdio_lists_the_tools_and_answers_calls(tmp_path: Path, flags: list[str]) -> None:
    server = StdioServerParameters(
        command=sys.executable,
        args=["-m", "fitatu_api.mcp_server", *flags],
        # DEBUG: log lines must go to stderr, as anything on stdout would break the protocol.
        env={"FITATU_SESSION": str(tmp_path / "session.json"), "PYTHONPATH": str(SRC), "FITATU_LOG_LEVEL": "DEBUG"},
    )
    calls = {"get_today": {}, "add_food": ARGUMENTS["add_food"], "remove_items": {"date": DAY, "row_ids": ["x"]}}
    _, listed, results = _session(server, calls)
    assert [tool.name for tool in listed] == (READ_ONLY if flags else [tool["name"] for tool in TOOLS])
    today = _envelope(results["get_today"])["error"]  # main() opens $FITATU_SESSION, which is not there
    assert today["error_type"] == "auth" and "fitatu-api login" in today["message"]
    for name in ("add_food", "remove_items"):
        error = _envelope(results[name])["error"]
        assert error["error_type"] == ("validation" if flags else "auth"), name
        assert ("read-only" in error["message"]) is bool(flags), name


def test_a_broken_client_profile_stops_the_server_at_start(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("FITATU_CLIENT_PROFILE", '{"app_versoin": "4.14.0"}')
    with patch("fitatu_api.mcp_server.anyio.run") as serve, pytest.raises(FitatuValidationError, match="FITATU_CLIENT_PROFILE"):
        main([])
    serve.assert_not_called()


def _post(client: TestClient, method: str, params: dict[str, Any] | None = None, **headers: str) -> Any:
    body = {"jsonrpc": "2.0", "id": 1, "method": method, **({"params": params} if params else {})}
    return client.post("/mcp", json=body, headers={**BEARER, "MCP-Protocol-Version": "2025-06-18", **headers})


def _http(**server: Any) -> TestClient:
    # The base URL sets the Host header, which the server checks. No GET through it: a GET opens an
    # event stream that never ends, and TestClient waits for the whole body.
    return TestClient(http_app(build_server(FitatuTools.demo, demo=True, **server), TOKEN, hosts=["fitatu-mcp", "[fd7a::1]"]), base_url="http://127.0.0.1:8765")


@pytest.mark.parametrize("read_only", [False, True])
def test_http_lists_the_tools_and_answers_calls_without_a_session(read_only: bool) -> None:
    with _http(read_only=read_only) as client:
        listed = _post(client, "tools/list")
        called = _post(client, "tools/call", {"name": "get_today", "arguments": {}})
    assert listed.status_code == 200 and listed.headers["content-type"] == "application/json"
    assert "mcp-session-id" not in listed.headers  # stateless: no session to lose when the server restarts
    assert [tool["name"] for tool in listed.json()["result"]["tools"]] == (READ_ONLY if read_only else [tool["name"] for tool in TOOLS])
    result = called.json()["result"]
    assert result["isError"] is False and result["structuredContent"]["ok"] is True


def test_http_refuses_a_request_without_the_token_and_says_nothing_more(caplog: pytest.LogCaptureFixture) -> None:
    with _http() as client, caplog.at_level(logging.WARNING):
        answers = [
            client.post("/mcp", json={}),
            client.post("/mcp", json={}, headers={"Authorization": "Bearer " + "y" * 43}),
            client.post("/mcp", json={}, headers={"Authorization": TOKEN}),  # no scheme
            client.get("/other"),
        ]
        assert _post(client, "tools/list", Authorization=f"bearer {TOKEN}").status_code == 200  # the scheme's case does not matter
    assert [(answer.status_code, answer.json(), answer.headers["www-authenticate"]) for answer in answers] == [(401, {"error": "unauthorized"}, "Bearer")] * 4
    assert "refused a request from testclient: no or wrong token" in caplog.text and TOKEN not in caplog.text


def test_http_answers_only_to_its_own_host_names_and_local_origins() -> None:
    with _http() as client:
        hosts = {
            host: _post(client, "tools/list", Host=host).status_code
            for host in ["evil.example:8765", "evil.example", "fitatu-mcp:8765", "fitatu-mcp", "localhost:8765", "127.0.0.1:8765", "[fd7a::1]:8765", "[fd7a::1]"]
        }
        origins = {origin: _post(client, "tools/list", Origin=origin).status_code for origin in ["https://evil.example", "http://localhost:6274"]}
    assert hosts == {"evil.example:8765": 421, "evil.example": 421, "fitatu-mcp:8765": 200, "fitatu-mcp": 200, "localhost:8765": 200, "127.0.0.1:8765": 200, "[fd7a::1]:8765": 200, "[fd7a::1]": 200}
    assert origins == {"https://evil.example": 403, "http://localhost:6274": 200}


def test_http_needs_a_strong_token_before_it_starts(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.delenv("FITATU_MCP_TOKEN", raising=False)
    monkeypatch.delenv("FITATU_MCP_TOKEN_FILE", raising=False)
    with patch("fitatu_api.mcp_server.uvicorn.run") as run:
        for env in [{}, {"FITATU_MCP_TOKEN": "short"}, {"FITATU_MCP_TOKEN_FILE": str(tmp_path / "missing")}]:
            with monkeypatch.context() as scoped, pytest.raises(SystemExit, match="FITATU_MCP_TOKEN"):
                for key, value in env.items():
                    scoped.setenv(key, value)
                main(["--http", "--demo"])
        run.assert_not_called()
        (tmp_path / "token").write_text(TOKEN + "\n", encoding="utf-8")  # a secret file ends with a newline
        monkeypatch.setenv("FITATU_MCP_TOKEN_FILE", str(tmp_path / "token"))
        main(["--http", "--demo"])
    for weak in ["", "x" * 31, TOKEN[:-1] + " "]:  # a library caller has no CLI check: "" would let "Bearer" with no value in
        with pytest.raises(ValueError, match="32 or more"):
            http_app(build_server(FitatuTools.demo, demo=True), weak)
    assert run.call_args.kwargs == {"host": "127.0.0.1", "port": 8765, "log_config": None, "log_level": "info", "access_log": False}


def test_http_options_reach_the_server_and_need_http(monkeypatch: pytest.MonkeyPatch) -> None:
    from fitatu_api.__main__ import main as fitatu_api

    monkeypatch.setenv("FITATU_MCP_TOKEN", TOKEN)
    with (
        patch("fitatu_api.mcp_server.uvicorn.run") as run,
        patch("fitatu_api.mcp_server.build_server") as build,
        patch("fitatu_api.mcp_server.http_app") as app,
    ):
        main(["--http", "--host", "0.0.0.0", "--port", "9000", "--allow-host", "fitatu-mcp", "--allow-host", "nas.local", "--read-only", "--tools", "get_today"])
        with pytest.raises(SystemExit, match="need --http"):
            fitatu_api(["mcp", "--port", "9000"])  # else a stdio server would start, quietly
    assert (run.call_args.kwargs["host"], run.call_args.kwargs["port"]) == ("0.0.0.0", 9000)
    assert app.call_args.kwargs["hosts"] == ["0.0.0.0", "fitatu-mcp", "nas.local"]
    with patch("fitatu_api.mcp_server.uvicorn.run") as run, patch("fitatu_api.mcp_server.http_app") as app:
        main(["--http", "--host", "fd7a:115c:a1e0::1"])
    # uvicorn listens on the bare address; the Host header carries it in brackets.
    assert (run.call_args.kwargs["host"], app.call_args.kwargs["hosts"]) == ("fd7a:115c:a1e0::1", ["[fd7a:115c:a1e0::1]"])
    assert build.call_args.kwargs["read_only"] is True and build.call_args.kwargs["tools"] == ["get_today"]


def test_fitatu_mcp_over_http_answers_with_the_token_and_never_logs_it() -> None:
    with socket.socket() as probe:  # a free port; another process taking it in between is unlikely
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]
    env = {**os.environ, "PYTHONPATH": str(SRC), "FITATU_MCP_TOKEN": TOKEN, "FITATU_LOG_LEVEL": "DEBUG"}
    process = subprocess.Popen([sys.executable, "-m", "fitatu_api.mcp_server", "--http", "--demo", "--port", str(port)], env=env, stderr=subprocess.PIPE, text=True)
    url = f"http://127.0.0.1:{port}/mcp"

    async def run() -> tuple[list[Any], Any]:
        async with httpx2.AsyncClient(headers=BEARER) as http, Client(streamable_http_client(url, http_client=http)) as session:
            return (await session.list_tools()).tools, await session.call_tool("get_today", {})

    try:
        for _ in range(100):  # up to 20 s for the interpreter and uvicorn to start
            with contextlib.suppress(OSError), socket.create_connection(("127.0.0.1", port), 0.2):
                break
            time.sleep(0.2)
        listed, today = anyio.run(run)
    finally:
        process.terminate()
        err = process.communicate(timeout=10)[1]
    assert [tool.name for tool in listed] == [tool["name"] for tool in TOOLS]
    assert _envelope(today)["ok"] is True
    assert f"Uvicorn running on http://127.0.0.1:{port}" in err and TOKEN not in err
