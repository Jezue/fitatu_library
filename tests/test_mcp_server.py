"""fitatu-mcp: the agent tools over MCP, in process against the fake server and over stdio."""

from __future__ import annotations

import json
import logging
import sys
import threading
import time
from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest

pytest.importorskip("mcp")

import anyio
from fake_server import RECORDED_USER_ID, FakeFitatuServer
from jsonschema import Draft202012Validator
from mcp import Client, StdioServerParameters
from test_agent import ARGUMENTS, DAY, SEED, fake_with_every_reply

from fitatu_api import (
    FitatuApiClient,
    FitatuAuthContext,
    FitatuTools,
    FitatuValidationError,
    __version__,
)
from fitatu_api.agent import SYSTEM_PROMPT, TOOLS
from fitatu_api.mcp_server import READ_ONLY_NOTE, build_server, main, output_schema

SRC = Path(__file__).resolve().parents[1] / "src"
READ_ONLY = [tool["name"] for tool in TOOLS if tool["annotations"]["readOnlyHint"]]


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
            # Claude Code then asks before every call, as a deleted recipe or product cannot be brought back
            **({"_meta": {"anthropic/requiresUserInteraction": True}} if t["name"] in ("delete_recipe", "delete_food") else {}),
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
