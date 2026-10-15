"""MCP server: the :data:`~fitatu_api.agent.TOOLS` over stdio, for Claude Desktop, Claude Code
and other MCP hosts, or over Streamable HTTP with ``--http`` (:func:`http_app`), for n8n and other
machines. Needs the ``mcp`` extra (``pip install "fitatu-api[mcp]"``); run it as
``fitatu-mcp`` or ``python -m fitatu_api.mcp_server``, with ``--read-only`` to serve only the
tools whose ``readOnlyHint`` is true (:data:`~fitatu_api.agent.READ_ONLY_TOOLS`) and ``--tools``
to serve only the tools named. Three prompts (:data:`PROMPTS`) give hosts Polish slash commands.

Every tool result is the :meth:`FitatuTools.call` envelope, as JSON text and as
``structuredContent``, with ``isError`` when ``ok`` is false; every tool's ``outputSchema``
describes that envelope (:func:`output_schema`). The server's instructions are
:data:`~fitatu_api.agent.SYSTEM_PROMPT`. The session is opened on the first tool call
(``$FITATU_SESSION_JSON``, else the file ``$FITATU_SESSION`` or ``$FITATU_SESSION_PATH``, else
``~/.fitatu/session.json``), so a missing session reaches the model as an ``auth`` error
instead of stopping the server. Logs go to stderr, from WARNING or the level of ``--log-level``
(else ``$FITATU_LOG_LEVEL``); stdout carries only the protocol. Under ``--http`` it also logs
uvicorn's start and stop lines and every request refused for its token, never the token.
"""

from __future__ import annotations

import argparse
import hmac
import json
import logging
import os
import re
import time
from collections.abc import Callable, Sequence
from pathlib import Path
from types import NoneType, UnionType
from typing import Any, NotRequired, Union, get_args, get_origin, get_type_hints, is_typeddict

try:
    import anyio
    import uvicorn
    from mcp import types
    from mcp.server import Server, ServerRequestContext
    from mcp.server.stdio import stdio_server
    from mcp.server.transport_security import TransportSecuritySettings
    from mcp.shared.exceptions import MCPError
    from starlette.responses import JSONResponse
    from starlette.types import ASGIApp, Receive, Scope, Send
except ImportError as exc:  # pragma: no cover
    raise ImportError('fitatu_api.mcp_server needs the mcp extra: pip install "fitatu-api[mcp]"') from exc

from . import __version__
from .__main__ import LOG_LEVELS, add_mcp_options, setup_logging
from .agent import READ_ONLY_TOOLS, SYSTEM_PROMPT, TOOLS, FitatuTools, _selected
from .auth import client_profile
from .exceptions import FitatuValidationError, describe_error, log_caught

logger = logging.getLogger(__name__)

READ_ONLY_NOTE = "- This server is read-only: it cannot change the diary. For changes, send the user to the Fitatu app.\n"
DEMO_NOTE = "- This is a demo: the diary is made up and kept in memory, and nothing reaches Fitatu. Say so when you show it.\n"
WEBSITE = "https://github.com/Jezue/fitatu_library"
LOOPBACK = ("127.0.0.1", "localhost", "[::1]")
# An HTTP token: printable ASCII without spaces, which also keeps it a valid header value.
_TOKEN_PATTERN = r"[!-~]{32,}"
# Claude Code asks before every call of such a tool, also where calls are otherwise allowed without
# asking (https://code.claude.com/docs/en/mcp, "Require approval for a specific tool"): a deleted
# recipe cannot be brought back.
_TOOL_META = {"delete_recipe": {"anthropic/requiresUserInteraction": True}}

# Slash commands for the user, in Polish like the app; the text tells the model which tools to use.
# A prompt is listed only when the server serves every tool its text (with its defaults) names.
PROMPTS: dict[str, dict[str, Any]] = {
    "podsumowanie_dnia": {
        "title": "Podsumowanie dnia",
        "description": "Summarize a diary day: what was eaten against the goals, what is left, the meals and water.",
        "arguments": [{"name": "date", "title": "Dzień", "description": "Day as YYYY-MM-DD; today when omitted."}],
        "text": "Podsumuj mój dzień w Fitatu: {date}. Wywołaj get_progress dla tego dnia (ile zjadłem wobec "
        "celów i ile zostało), get_day (co jest w których posiłkach) i get_water. Odpowiedz krótko po polsku: "
        "kalorie, białko, tłuszcze i węglowodany zjedzone wobec celu, ile zostało, posiłki z najważniejszymi "
        "produktami i woda. Jeśli nic nie jest odhaczone jako zjedzone, podaj to, co zapisane, i powiedz o tym.",
        "default": {"date": "dzisiaj (datę daje get_today)"},
    },
    "przeglad_tygodnia": {
        "title": "Przegląd tygodnia",
        "description": "Review this week, Monday to today: daily averages against the goals, the days that "
        "stood out, the weight change and, with Fitatu Premium, the top foods.",
        "arguments": [],
        "text": "Zrób przegląd mojego tygodnia w Fitatu, od poniedziałku do dzisiaj (dzisiejszą datę daje "
        "get_today). Wywołaj get_report dla tego zakresu i get_progress, żeby zobaczyć poszczególne dni. "
        "Napisz po polsku: średnie dzienne kalorii i makroskładników wobec celów, które dni odbiegały "
        "najbardziej, zmianę wagi, jeśli jest, i produkty, które dały najwięcej kalorii i białka (jeśli "
        "top_foods jest null, powiedz, że to wymaga Fitatu Premium). Zakończ jedną konkretną wskazówką na "
        "resztę tygodnia albo następny.",
        "default": {},
    },
    "zaloguj_posilek": {
        "title": "Zaloguj posiłek",
        "description": "Log a meal from a description, e.g. 'dwa jajka i kawa z mlekiem na śniadanie'.",
        "arguments": [{"name": "meal", "title": "Posiłek", "description": "What was eaten, and when or in which meal."}],
        "text": "Zapisz w moim dzienniku Fitatu: {meal}. Ustal dzień (get_today) i posiłek: dopasuj moje "
        "słowa do meal_name z get_day. Znajdź każdy produkt w get_recent_foods dla tego posiłku albo przez "
        "search_food i zapisz wszystko jednym add_foods z eaten true; to, czego nie ma w katalogu, zapisz "
        "przez add_custom_item. Gdy nie podałem ilości, przyjmij typową porcję i powiedz jaką. Na końcu "
        "wypisz, co zapisałeś i ile kcal to dało.",
        "default": {"meal": "to, co zaraz napiszę (najpierw zapytaj mnie, co zjadłem)"},
    },
}
_TOOL_NAMES = frozenset(tool["name"] for tool in TOOLS)
_SCALARS = {str: "string", int: "integer", float: "number", bool: "boolean", NoneType: "null"}
_ERROR_SCHEMA = {
    "type": "object",
    "description": "describe_error() of what went wrong; docs/AGENTS.md, Results and errors.",
    "properties": {
        "error_type": {
            "type": "string",
            "enum": ["auth", "rate_limit", "maintenance", "network", "sync_rejected", "validation", "not_found", "forbidden", "server", "unexpected"],
        },
        "message": {"type": "string"},
        "status_code": {"type": ["integer", "null"]},
        "codename": {"type": ["string", "null"]},
        "retryable": {"type": "boolean"},
        "retry_after": {"type": ["number", "null"]},
        "hint": {"type": ["string", "null"]},
        "retry_row_id": {"type": "string", "description": "An add that may have been saved: repeat it with this row_id."},
    },
    "required": ["error_type", "message", "retryable"],
}


def _json_schema(hint: Any) -> dict[str, Any]:
    """The JSON Schema of a tool method's return type: TypedDicts, lists, dicts, unions and scalars."""
    if is_typeddict(hint):
        fields = get_type_hints(hint, include_extras=True)  # keeps NotRequired, which marks the optional keys
        return {
            "type": "object",
            "properties": {key: _json_schema(value) for key, value in fields.items()},
            "required": [key for key, value in fields.items() if get_origin(value) is not NotRequired],
        }
    origin, args = get_origin(hint), get_args(hint)
    if origin is NotRequired:
        return _json_schema(args[0])
    if origin in (Union, UnionType):
        return {"anyOf": [_json_schema(arg) for arg in args]}
    if origin is list:
        return {"type": "array", "items": _json_schema(args[0])}
    if origin is dict:
        return {"type": "object", "additionalProperties": _json_schema(args[1])}
    return {"type": _SCALARS[hint]} if hint in _SCALARS else {}  # Any


def output_schema(name: str) -> dict[str, Any]:
    """The ``outputSchema`` of tool ``name``: the :meth:`FitatuTools.call` envelope, whose ``data``
    has the shape of the tool method's return type (a TypedDict for the main tools)."""
    data = _json_schema(get_type_hints(getattr(FitatuTools, name))["return"])
    return {
        "type": "object",
        "properties": {"ok": {"type": "boolean"}, "data": data, "error": _ERROR_SCHEMA},
        "required": ["ok"],
    }


def _tools_named(text: str) -> set[str]:
    return set(re.findall(r"\b[a-z]+(?:_[a-z]+)+\b", text)) & _TOOL_NAMES


def build_server(
    open_tools: Callable[[], FitatuTools] = FitatuTools.from_session,
    *,
    read_only: bool = False,
    tools: Sequence[str] | None = None,
    call_timeout: float | None = 45,
    demo: bool = False,
) -> Server[Any]:
    """An MCP server over the tools ``open_tools()`` returns; it is called on the first tool call.

    It serves the tools named in ``tools`` (all when ``None``; an unknown name raises
    ``FitatuValidationError``), only the read ones of them with ``read_only``, and refuses any
    other with a ``validation`` error before the session is opened. A tool that
    :data:`~fitatu_api.agent.SYSTEM_PROMPT` names but ``tools`` leaves out is logged as a warning.
    ``call_timeout`` sets the tools' ``call_timeout`` for each call to what is left of that many
    seconds since the host sent it, as the host's own timeout runs from then; ``None`` sets no limit.
    ``demo`` adds :data:`DEMO_NOTE` to the instructions, for ``open_tools=FitatuTools.demo``.
    """
    served = [tool for tool in _selected(tools) if not read_only or tool["name"] in READ_ONLY_TOOLS]
    names = [tool["name"] for tool in served]
    # Under --read-only the instructions naming a write tool is expected: READ_ONLY_NOTE explains it.
    left_out = sorted(name for name in _tools_named(SYSTEM_PROMPT) - set(names) if not read_only or name in READ_ONLY_TOOLS)
    if left_out:
        logger.warning("the instructions name tools this server does not serve (--tools): %s", ", ".join(left_out))
    # input_schema becomes inputSchema; the title also goes in the annotations, where older hosts look for it
    listed = [
        types.Tool.model_validate(
            {
                **tool,
                "annotations": {"title": tool["title"], **tool["annotations"]},
                "output_schema": output_schema(tool["name"]),
                "_meta": _TOOL_META.get(tool["name"]),
            }
        )
        for tool in served
    ]
    # The defaults count: podsumowanie_dnia's sends the model to get_today.
    prompts = {name: prompt for name, prompt in PROMPTS.items() if _tools_named(" ".join([prompt["text"], *prompt["default"].values()])) <= set(names)}
    opened: list[FitatuTools] = []
    # One call at a time, off the event loop: hosts call tools in parallel, and an instance is not meant for that.
    one_at_a_time = anyio.CapacityLimiter(1)

    def call(name: str, arguments: dict[str, Any] | None, sent: float) -> dict[str, Any]:
        try:
            if name not in names:
                server = "this read-only server" if read_only else "this server"
                raise FitatuValidationError(f"{name!r} is not a tool of {server}; tools: {', '.join(names)}")
            if not opened:
                opened.append(open_tools())
        except Exception as exc:  # e.g. no session file yet: the model tells the user to log in
            error = describe_error(exc)
            log_caught(logger, name, exc, error)
            return {"ok": False, "error": error}
        tools = opened[0]
        # The time spent waiting behind another call counts.
        tools.call_timeout = None if call_timeout is None else max(0.0, call_timeout - (time.monotonic() - sent))
        return tools.call(name, arguments)

    async def list_tools(ctx: ServerRequestContext[Any], params: types.PaginatedRequestParams | None) -> types.ListToolsResult:
        return types.ListToolsResult(tools=listed)

    async def call_tool(ctx: ServerRequestContext[Any], params: types.CallToolRequestParams) -> types.CallToolResult:
        result = await anyio.to_thread.run_sync(call, params.name, params.arguments, time.monotonic(), limiter=one_at_a_time)
        return types.CallToolResult(
            content=[types.TextContent(text=json.dumps(result, ensure_ascii=False))],
            structured_content=result,
            is_error=not result["ok"],
        )

    async def list_prompts(ctx: ServerRequestContext[Any], params: types.PaginatedRequestParams | None) -> types.ListPromptsResult:
        return types.ListPromptsResult(
            prompts=[
                types.Prompt(name=name, title=prompt["title"], description=prompt["description"], arguments=prompt["arguments"])
                for name, prompt in prompts.items()
            ]
        )

    async def get_prompt(ctx: ServerRequestContext[Any], params: types.GetPromptRequestParams) -> types.GetPromptResult:
        prompt = prompts.get(params.name)
        if prompt is None:
            raise MCPError(code=types.INVALID_PARAMS, message=f"unknown prompt {params.name!r}; prompts: {', '.join(prompts)}")
        given = {key: value.strip() for key, value in (params.arguments or {}).items() if value and value.strip()}
        text = prompt["text"].format(**{**prompt["default"], **given})
        return types.GetPromptResult(
            description=prompt["description"],
            messages=[types.PromptMessage(role="user", content=types.TextContent(text=text))],
        )

    return Server(
        "fitatu",
        version=__version__,
        title="Fitatu",
        description="Unofficial MCP server for the Fitatu calorie counter: food diary, meals, weight, body measurements and water.",
        website_url=WEBSITE,
        instructions=SYSTEM_PROMPT + (READ_ONLY_NOTE if read_only else "") + (DEMO_NOTE if demo else ""),
        on_list_tools=list_tools,
        on_call_tool=call_tool,
        on_list_prompts=list_prompts,
        on_get_prompt=get_prompt,
    )


def _http_token() -> str:
    """The token HTTP clients must send: ``$FITATU_MCP_TOKEN``, else the file ``$FITATU_MCP_TOKEN_FILE``."""
    token = os.environ.get("FITATU_MCP_TOKEN") or ""
    if not token and os.environ.get("FITATU_MCP_TOKEN_FILE"):
        try:
            token = Path(os.environ["FITATU_MCP_TOKEN_FILE"]).read_text(encoding="utf-8").strip()
        except OSError as exc:
            raise SystemExit(f"cannot read $FITATU_MCP_TOKEN_FILE: {exc.strerror}") from None
    if not re.fullmatch(_TOKEN_PATTERN, token):
        raise SystemExit(
            "fitatu-mcp --http needs FITATU_MCP_TOKEN (or FITATU_MCP_TOKEN_FILE): 32 or more printable "
            'ASCII characters, e.g. from python -c "import secrets; print(secrets.token_urlsafe(32))"'
        )
    return token


def http_app(server: Server[Any], token: str, *, hosts: Sequence[str] = ()) -> ASGIApp:
    """``server`` over Streamable HTTP at ``/mcp``, for requests with ``Authorization: Bearer <token>``.

    Stateless, so a restart leaves clients no session to lose, with JSON answers. The Host header must
    be 127.0.0.1, localhost, [::1] or one of ``hosts`` (any port, or none), else 421; an Origin, when
    sent, a local one, else 403 (the SDK's DNS rebinding check). Any request without the token gets
    the same 401, before its path is looked at. A ``token`` shorter than 32 printable ASCII characters
    raises :class:`ValueError`: an empty one would let ``Authorization: Bearer`` in.
    """
    if not re.fullmatch(_TOKEN_PATTERN, token):
        raise ValueError("http_app needs a token of 32 or more printable ASCII characters")
    names = [*LOOPBACK, *hosts]
    app = server.streamable_http_app(
        stateless_http=True,
        json_response=True,
        # The SDK's "name:*" needs a port, so the bare name is listed too.
        transport_security=TransportSecuritySettings(
            allowed_hosts=[*names, *(f"{name}:*" for name in names)],
            allowed_origins=[f"http://{name}:*" for name in LOOPBACK],
        ),
    )
    expected = token.encode()

    async def guarded(scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] == "http":
            # Bytes, so a non-ASCII header cannot raise; the scheme's case does not matter (RFC 7235).
            scheme, _, given = dict(scope["headers"]).get(b"authorization", b"").partition(b" ")
            if scheme.lower() != b"bearer" or not hmac.compare_digest(given, expected):
                logger.warning("refused a request from %s: no or wrong token", (scope.get("client") or ("?",))[0])
                await JSONResponse({"error": "unauthorized"}, 401, {"WWW-Authenticate": "Bearer"})(scope, receive, send)
                return
        await app(scope, receive, send)

    return guarded


def main(argv: Sequence[str] | None = None) -> None:
    """``fitatu-mcp``: serve the tools over stdio until the host closes the pipe, or over HTTP with ``--http``."""
    parser = argparse.ArgumentParser(
        prog="fitatu-mcp",
        description="Serve the Fitatu diary tools to an MCP host over stdio, or over HTTP with --http. The session is "
        "$FITATU_SESSION_JSON when set (refreshed tokens are then not saved), else the file "
        "$FITATU_SESSION (or $FITATU_SESSION_PATH), else ~/.fitatu/session.json; create it with "
        "`fitatu-api login`, and with `fitatu-api login -o <path>` for another path.",
    )
    add_mcp_options(parser)
    parser.add_argument(
        "--log-level",
        type=str.upper,
        choices=LOG_LEVELS,
        help="log the library from this level to stderr (default: $FITATU_LOG_LEVEL, else WARNING)",
    )
    args = parser.parse_args(argv)
    setup_logging(args.log_level)
    serve(args)


def serve(args: argparse.Namespace) -> None:
    """Serve over stdio, or over HTTP with ``--http``, with the options :func:`main` parsed
    (``fitatu-api mcp`` parses the same)."""
    client_profile()  # a broken $FITATU_CLIENT_PROFILE stops the server here, not every call
    if not args.http and (args.host or args.port or args.allow_host):
        raise SystemExit("--host, --port and --allow-host need --http")
    token = _http_token() if args.http else ""  # no token, no HTTP server
    server = build_server(
        FitatuTools.demo if args.demo else FitatuTools.from_session,
        read_only=args.read_only,
        tools=args.tools,
        call_timeout=args.call_timeout or None,
        demo=args.demo,
    )
    if args.http:
        host = args.host or "127.0.0.1"
        # Clients send an IPv6 address in brackets in the Host header.
        name = f"[{host}]" if ":" in host else host
        # No access log: it would print a line per tool call.
        uvicorn.run(http_app(server, token, hosts=[name, *args.allow_host]), host=host, port=args.port or 8765, log_config=None, log_level="info", access_log=False)
        return

    async def run() -> None:
        async with stdio_server() as (read_stream, write_stream):
            await server.run(read_stream, write_stream, server.create_initialization_options())

    anyio.run(run)


if __name__ == "__main__":
    main()
