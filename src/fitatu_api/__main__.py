"""Command line helpers.

``python -m fitatu_api login`` (or ``fitatu-api login`` once installed) logs in with an
e-mail and password and writes the session file - ``~/.fitatu/session.json`` unless
``-o`` or ``$FITATU_SESSION`` says otherwise - that ``FitatuApiClient.from_session()``
loads. ``fitatu-api import-session`` writes the same file from tokens copied out of the
web app, for accounts that have no password (Apple or Facebook sign-in).

On that session file, ``fitatu-api tools`` lists the agent tools, ``fitatu-api call NAME
[JSON]`` runs one through :meth:`FitatuTools.call` and prints its envelope (only the read
tools, unless ``--allow-writes``), ``fitatu-api export`` saves the diary export to a
private file, and ``fitatu-api mcp`` runs the MCP server, like ``fitatu-mcp``.
``fitatu-api status`` describes the session without a request, ``doctor`` checks it with
Fitatu, and ``logout`` deletes the file. None of them prints a token.
"""

from __future__ import annotations

import argparse
import getpass
import json
import logging
import os
import platform
import re
import sys
import textwrap
from collections.abc import Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from . import __version__
from ._files import write_private_file
from ._shapes import as_dict
from .agent import READ_ONLY_TOOLS, TOOLS, FitatuTools, _selected, anthropic_tools
from .auth import FitatuAuthContext, jwt_payload, resolve_session_path, write_private_json
from .client import FitatuApiClient
from .exceptions import FitatuApiError, FitatuAuthError, FitatuValidationError, describe_error
from .service_modules import SummaryModule

LOG_LEVELS = ("DEBUG", "INFO", "WARNING", "ERROR")


def setup_logging(level: str | None) -> None:
    """Log to stderr: the library from ``level`` (else ``$FITATU_LOG_LEVEL``, else WARNING),
    other libraries from WARNING. Never stdout, which carries fitatu-mcp's protocol."""
    level = (level or os.environ.get("FITATU_LOG_LEVEL") or "").upper()
    if level and level not in LOG_LEVELS:
        raise SystemExit(f"FITATU_LOG_LEVEL must be one of {', '.join(LOG_LEVELS)}, got {level!r}")
    logging.basicConfig(stream=sys.stderr, level=logging.WARNING, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    if level:
        logging.getLogger("fitatu_api").setLevel(level)


def _tool_names(text: str) -> list[str]:
    names = [name.strip() for name in text.split(",") if name.strip()]
    try:
        _selected(names)
    except FitatuValidationError as exc:
        raise argparse.ArgumentTypeError(str(exc)) from None
    return names


def _seconds(text: str) -> float:
    seconds = float(text)
    if seconds < 0:
        raise argparse.ArgumentTypeError(f"must be 0 or more, got {text}")
    return seconds


def add_mcp_options(parser: argparse.ArgumentParser) -> None:
    """The MCP server's options, which fitatu-mcp and ``fitatu-api mcp`` both take."""
    parser.add_argument("--read-only", action="store_true", help="serve only the tools that read the diary")
    parser.add_argument(
        "--demo",
        action="store_true",
        help="serve a small made-up diary kept in memory instead of an account: no session file, nothing sent to Fitatu",
    )
    parser.add_argument(
        "--tools",
        type=_tool_names,
        metavar="NAME,...",
        help="serve only these tools, e.g. get_today,get_day,search_food,add_food (`fitatu-api tools` lists them); "
        "with --read-only, only the read ones of them",
    )
    parser.add_argument(
        "--call-timeout",
        type=_seconds,
        default=45,
        metavar="SECONDS",
        help="end every tool call within this many seconds from when the host sent it, retries and waits "
        "included, so that a host which gives up after 60 s still gets an answer; 0 for no limit (default: 45)",
    )


def _save(client: FitatuApiClient, output: str | None, *, to_stdout: bool = False) -> int:
    session = client.auth.to_session_data(include_tokens=True)
    if to_stdout:
        print(json.dumps(session, indent=2))
        print(
            "This is a full account credential: put it in a secret store (e.g. as FITATU_SESSION_JSON), "
            "not in a shared file, a log or shell history.",
            file=sys.stderr,
        )
        return 0
    path = resolve_session_path(output)
    write_private_json(path, session)
    print(f"Saved session for user {session.get('fitatu_user_id')} to {path}")
    return 0


def _login(args: argparse.Namespace) -> int:
    email = args.email or os.environ.get("FITATU_EMAIL")
    if not email:
        # On stderr, as getpass does: with --print, stdout must carry nothing but the session JSON.
        print("Fitatu e-mail: ", end="", file=sys.stderr, flush=True)
        email = input().strip()
    password = os.environ.get("FITATU_PASSWORD") or getpass.getpass("Fitatu password: ")
    if not email or not password:
        print("E-mail and password are required.", file=sys.stderr)
        return 2
    try:
        client = FitatuApiClient.login(email, password, base_url=args.base_url, persist_tokens=False)
    except FitatuApiError as exc:
        print(f"Login failed ({exc.status_code}): {exc}", file=sys.stderr)
        if isinstance(exc, FitatuAuthError) and exc.hint:
            print(exc.hint, file=sys.stderr)
        return 1
    return _save(client, args.output, to_stdout=args.print)


def _import_session(args: argparse.Namespace) -> int:
    if sys.stdin.isatty():
        print(
            "Paste the JSON the browser snippet copied, then press Enter and Ctrl-D "
            "(Windows: Enter, Ctrl-Z, Enter). Steps: fitatu-api import-session --help",
            file=sys.stderr,
        )
    try:
        data = json.loads(sys.stdin.read())
    except (ValueError, RecursionError):  # RecursionError: nested too deep
        print("Expected JSON on stdin: the object the browser snippet copied.", file=sys.stderr)
        return 2
    auth = FitatuAuthContext.from_session_data(data) if isinstance(data, dict) else FitatuAuthContext()
    if not (isinstance(auth.bearer_token, str) and isinstance(auth.refresh_token, str) and auth.fitatu_user_id):
        print("The JSON needs token and refresh_token as text, and id (or bearer_token, refresh_token, fitatu_user_id).", file=sys.stderr)
        return 2
    try:
        client = FitatuApiClient(auth, base_url=args.base_url, persist_tokens=False)
        # While the bearer works, only a refresh shows a dead refresh token. Safe: Fitatu does not
        # rotate refresh tokens and older bearers stay valid; the saved bearer gets a full hour.
        client.refresh_access_token()
        client._read_profile(auth.fitatu_user_id, pick_host=args.base_url is None)
    except FitatuApiError as exc:
        advice = "; copy a fresh session from the web app" if isinstance(exc, FitatuAuthError) else ""
        print(f"Import failed: {exc}{advice}", file=sys.stderr)
        return 1
    return _save(client, args.output, to_stdout=args.print)


def _tools(args: argparse.Namespace) -> int:
    if args.json:
        print(json.dumps(anthropic_tools(), indent=2))
        return 0
    width = max(len(tool["name"]) for tool in TOOLS)
    for tool in TOOLS:
        hints = tool["annotations"]
        kind = "read" if hints["readOnlyHint"] else "destructive" if hints["destructiveHint"] else "write"
        first_sentence = re.split(r"(?<!e\.g)\.(?:\s|$)", tool["description"], maxsplit=1)[0]
        print(f"{tool['name']:<{width}}  {kind:<11}  {textwrap.shorten(first_sentence, 100, placeholder='...')}")
    return 0


def _json_arguments(text: str) -> Any:
    try:
        return json.loads(text)
    except (ValueError, RecursionError) as exc:  # RecursionError: nested too deep
        raise argparse.ArgumentTypeError(f"not JSON: {exc}") from None


def _call(args: argparse.Namespace) -> int:
    try:
        tools = FitatuTools.from_session(tools=None if args.allow_writes else READ_ONLY_TOOLS)
    except FitatuApiError as exc:  # e.g. no session file yet
        result = {"ok": False, "error": describe_error(exc)}
    else:
        with tools:
            result = tools.call(args.name, args.arguments)
    print(json.dumps(result, indent=2))
    # call() reports a write tool it may not run as unknown, as it does to a model; a person gets the flag.
    if not args.allow_writes and args.name not in READ_ONLY_TOOLS and any(t["name"] == args.name for t in TOOLS):
        print(f"{args.name} changes the diary: run it with --allow-writes.", file=sys.stderr)
    return 0 if result["ok"] else 1


def _export(args: argparse.Namespace) -> int:
    if os.path.lexists(args.output) and not args.overwrite:  # checked again when the file is written
        print(f"{args.output} exists; add --overwrite to replace it.", file=sys.stderr)
        return 2
    # The file is written next to its target and swapped in, so neither can be found out after the download.
    if os.path.isdir(args.output) or not os.path.isdir(os.path.dirname(args.output) or "."):
        print(f"{args.output} is a directory or its directory does not exist; give a file path.", file=sys.stderr)
        return 2
    try:
        with FitatuApiClient.from_session() as client:
            user_id = client.auth.fitatu_user_id
            if not user_id:
                raise FitatuAuthError("the session has no Fitatu user id; log in again to get a complete session")
            content = client.summary.export_diary(user_id, args.from_date, args.to_date, fmt=args.format)
            if content is not None:
                write_private_file(args.output, content, overwrite=args.overwrite)
    except (FitatuApiError, OSError) as exc:  # e.g. no session file yet, or the file appeared meanwhile
        error = describe_error(exc)
        print(f"Export failed: {error['message']}", file=sys.stderr)
        if error["hint"]:
            print(error["hint"], file=sys.stderr)
        return 1
    if content is None:
        print(f"Nothing is logged from {args.from_date} to {args.to_date}; no file was written.", file=sys.stderr)
        return 1
    print(f"Saved the {args.format} export ({len(content)} bytes) to {args.output}")
    return 0


def _mcp(args: argparse.Namespace) -> int:
    try:
        from .mcp_server import serve
    except ImportError as exc:  # the mcp extra is not installed
        print(exc, file=sys.stderr)
        return 1
    serve(args)
    return 0


def _shown(text: object) -> str:
    """``text`` with the home directory as ``~``, so output pasted into an issue has no user name."""
    return str(text).replace(str(Path.home()), "~")


def _session_file(client: FitatuApiClient) -> tuple[str, bool]:
    """Where the session came from, and whether the file is private (always, off POSIX)."""
    if client.token_store is None:
        return "$FITATU_SESSION_JSON (refreshed tokens are not saved)", True
    path = client.token_store.path
    if os.name == "nt":  # no POSIX permissions to show
        return _shown(path), True
    mode = path.stat().st_mode & 0o777
    if mode & 0o077:
        return f"{_shown(path)} ({mode:04o}: not private, run chmod 600 {_shown(path)})", False
    return f"{_shown(path)} ({mode:04o})", True


def _bearer_expiry(auth: FitatuAuthContext) -> str:
    if not auth.bearer_token:
        return "missing"
    expires = auth.bearer_expires_at
    if expires is None:
        return "no expiry in the token"
    minutes = round((expires - datetime.now(UTC)).total_seconds() / 60)
    if minutes <= 0:
        return f"expired at {expires:%Y-%m-%d %H:%M} UTC; the next call refreshes it"
    return f"valid until {expires:%Y-%m-%d %H:%M} UTC ({minutes} min)"


def _status(args: argparse.Namespace) -> int:
    try:
        client = FitatuApiClient.from_session()
    except FitatuApiError as exc:
        print(_shown(exc), file=sys.stderr)
        return 1
    auth, code = client.auth, 0
    rows = [
        ("session", _session_file(client)[0]),
        ("user id", auth.fitatu_user_id or "missing: log in again"),
        ("API host", client.base_url),
        ("locales", f"{auth.app_locale}, search {auth.app_search_locale}, storage {auth.app_storage_locale}"),
        ("time zone", auth.app_timezone),
        ("bearer token", _bearer_expiry(auth)),
        ("refresh token", "present" if auth.refresh_token else "missing: log in again"),
        ("client", f"{auth.app_os} {auth.app_version}"),
    ]
    if args.check:
        with client:
            try:
                client.user_settings.get_profile(auth.fitatu_user_id or "")
                rows.append(("Fitatu", "the session works (profile read)"))
            except FitatuApiError as exc:
                rows.append(("Fitatu", _explained(exc)))
                code = 1
    for label, value in rows:
        print(f"{label + ':':<15}{value}")
    return code


def _logout(args: argparse.Namespace) -> int:
    if os.environ.get("FITATU_SESSION_JSON"):
        print("The session comes from $FITATU_SESSION_JSON: unset it, and delete it from your secret store.", file=sys.stderr)
        return 1
    path = resolve_session_path()
    if not os.path.lexists(path):
        print(f"No session file at {path}.")
        return 0
    if not args.yes:
        try:
            answer = input(f"Delete {path}? [y/N] ")
        except EOFError:  # no terminal to ask on
            answer = ""
        if answer.strip().lower() not in ("y", "yes"):
            print("Kept it; --yes deletes without asking.", file=sys.stderr)
            return 1
    path.unlink(missing_ok=True)
    print(f"Deleted {path}. The library knows no call that revokes the tokens at Fitatu, so a copy kept elsewhere still works.")
    return 0


def _explained(exc: FitatuApiError) -> str:
    error = describe_error(exc)
    return f"{error['error_type']}: {error['message']}" + (f"; {error['hint']}" if error["hint"] else "")


def _doctor(args: argparse.Namespace) -> int:
    if not (args.log_level or os.environ.get("FITATU_LOG_LEVEL")):
        # Every failure gets a line below with the user id masked; the library's warnings would quote it.
        logging.getLogger("fitatu_api").setLevel(logging.ERROR)
    print(f"fitatu-api {__version__}, Python {platform.python_version()}, {platform.system()}")
    failed, user_id = False, None

    def report(level: str, check: str, text: str) -> None:
        nonlocal failed
        failed = failed or level == "FAIL"
        line = _shown(text).replace(f"/{user_id}", "/<user id>") if user_id else _shown(text)  # e.g. GET /users/{id} -> 401
        print(f"{level:<5} {check + ':':<12}{line}")

    try:
        client = FitatuApiClient.from_session()
    except FitatuApiError as exc:
        report("FAIL", "session", _explained(exc))
        return 1
    with client:
        auth, user_id = client.auth, client.auth.fitatu_user_id
        session, private = _session_file(client)
        report("ok" if private else "WARN", "session", f"{session}, host {client.base_url}")
        if not user_id:
            report("FAIL", "user id", "the session has none; log in again")
        # A refresh checks the refresh token, which outlives the bearer, and gives a token whose iat is Fitatu's now.
        if not auth.refresh_token:
            report("FAIL", "refresh", "the session has no refresh token; log in again")
        else:
            try:
                client.refresh_access_token()
            except FitatuApiError as exc:
                report("FAIL", "refresh", f"failed: {_explained(exc)}")
            else:
                report("ok", "refresh", "the refresh token works")
                issued = jwt_payload(auth.bearer_token).get("iat")
                if isinstance(issued, int | float) and not isinstance(issued, bool):
                    skew = round(datetime.now(UTC).timestamp() - issued)
                    if abs(skew) > 60:
                        direction = "ahead of" if skew > 0 else "behind"
                        report("WARN", "clock", f"{abs(skew)} s {direction} Fitatu's; turn on automatic time")
                    else:
                        report("ok", "clock", "within a minute of Fitatu's")
        if user_id:
            try:
                client.user_settings.get_profile(user_id)
                report("ok", "profile", "read")
            except FitatuApiError as exc:
                report("FAIL", "profile", f"not read: {_explained(exc)}")
            try:
                plan = client.user_settings.get_premium(user_id)
            except FitatuApiError as exc:
                report("WARN", "plan", f"not read: {_explained(exc)}")
            else:  # whether, not until when: the output goes into public issues
                report("ok", "plan", "premium" if plan["active"] else "no premium plan: premium-only features answer 403")
        version = f"{auth.app_os} {auth.app_version}"
        try:
            # The catalog lists it under the app's unauthorized client, but the server wants the token (checked live).
            reply = as_dict(client.request("GET", f"/upgrade-application/{auth.app_os}/{auth.app_version}")) or {}
        except FitatuApiError as exc:
            report("WARN", "client", f"{version}, not checked: {_explained(exc)}")
        else:
            newest = reply.get("newestApplicationVersion")
            if newest == auth.app_version and not reply.get("upgradeApplication"):
                report("ok", "client", f"{version} is the newest web app version")
            else:
                report(
                    "WARN", "client", f"Fitatu's web app is at {newest or 'an unknown version'}, this library sends {version}; "
                    "if calls fail, update fitatu-api or set FITATU_CLIENT_PROFILE (see the README)",
                )
    return 1 if failed else 0


def _add_output_options(command: argparse.ArgumentParser) -> None:
    target = command.add_mutually_exclusive_group()
    target.add_argument(
        "-o",
        "--output",
        help="where to write the session file (default: $FITATU_SESSION, $FITATU_SESSION_PATH "
        "or ~/.fitatu/session.json)",
    )
    target.add_argument(
        "--print",
        action="store_true",
        help="print the session JSON to stdout instead of writing a file, e.g. for a secret store "
        "that sets FITATU_SESSION_JSON; it is a full account credential",
    )
    command.add_argument(
        "--base-url",
        help="API host; by default the one for your account's region, "
        "e.g. https://de-de.fitatu.com/api for de_DE",
    )


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="fitatu-api", description="Helpers for the fitatu-api library.")
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    parser.add_argument(
        "--log-level",
        type=str.upper,
        choices=LOG_LEVELS,
        help="log the library from this level to stderr (default: $FITATU_LOG_LEVEL, else WARNING); before the command",
    )
    commands = parser.add_subparsers(dest="command", required=True)

    login = commands.add_parser(
        "login",
        help="log in with e-mail and password and save the session file",
        description=(
            "Log in and save a session file (bearer token, refresh token, user id, locales "
            "and API host). Reads FITATU_EMAIL / FITATU_PASSWORD when set, otherwise prompts. "
            "Accounts created with Apple or Facebook have no password; for them, see "
            "`fitatu-api import-session --help`."
        ),
    )
    login.add_argument("--email", help="account e-mail (prompted when omitted)")
    _add_output_options(login)
    login.set_defaults(run=_login)

    import_session = commands.add_parser(
        "import-session",
        help="save the session file from tokens copied out of the web app (no password needed)",
        # Raw, so the snippet stays on one line: a line break after "u" breaks the arrow function.
        formatter_class=argparse.RawDescriptionHelpFormatter,
        description=(
            "For accounts without a password (Apple or Facebook sign-in). Log in at\n"
            "https://www.fitatu.com/app/, open the browser's developer console there and run:\n\n"
            "  copy(JSON.stringify((u => ({token: u.token, refresh_token: u.refresh_token, id: u.id}))"
            "(JSON.parse(localStorage.user))))\n\n"
            "It copies only the tokens and user id to the clipboard (localStorage.user also holds\n"
            "your e-mail, nickname and more). Then, on macOS:\n\n"
            "  pbpaste | fitatu-api import-session\n\n"
            "Elsewhere paste it into a file, run `fitatu-api import-session < FILE` (PowerShell:\n"
            "`Get-Content FILE | fitatu-api import-session`) and delete the file. The token is a\n"
            "full account credential: do not share it.\n\n"
            "Reads the JSON from stdin (the web app's localStorage.user, or a session file),\n"
            "checks both tokens with Fitatu by refreshing the bearer once, reads the account's\n"
            "locales and timezone like login does, and saves only the tokens, user id, locales,\n"
            "timezone and API host."
        ),
    )
    _add_output_options(import_session)
    import_session.set_defaults(run=_import_session)

    tools = commands.add_parser(
        "tools",
        help="list the agent tools: name, read / write / destructive, what it does",
        description="List the tools of fitatu_api.agent (FitatuTools) with their kind: read, write, or "
        "destructive (overwrites or deletes what is logged), and the first sentence of the description "
        "the model gets.",
    )
    tools.add_argument("--json", action="store_true", help="print the tools in the Anthropic API format (anthropic_tools())")
    tools.set_defaults(run=_tools)

    call = commands.add_parser(
        "call",
        help="run one agent tool on the session file and print its result as JSON",
        description="Run one tool through FitatuTools.call() and print the result envelope as JSON: "
        '{"ok": true, "data": ...} or {"ok": false, "error": ...}. Only the read tools run unless '
        "--allow-writes is given. The session file is $FITATU_SESSION (or $FITATU_SESSION_PATH), else "
        "~/.fitatu/session.json. Exit status: 0 when ok is true, 1 when it is false.",
        epilog="""example: fitatu-api call get_day '{"date": "2026-10-14"}'""",
    )
    call.add_argument("name", help="tool name, as `fitatu-api tools` lists it")
    call.add_argument(
        "arguments", nargs="?", default="{}", type=_json_arguments, metavar="JSON", help="the tool's arguments as a JSON object (default: {})"
    )
    call.add_argument("--allow-writes", action="store_true", help="also run the tools that change the diary")
    call.set_defaults(run=_call)

    export = commands.add_parser(
        "export",
        help="save the food diary of a date range as csv, xls, pdf or txt",
        description="Download Fitatu's export of the food diary for a date range and save it to a new file "
        "readable only by you (0600). An existing file is kept unless --overwrite is given; a range with "
        "nothing logged writes no file and exits with 1. The session file is $FITATU_SESSION (or "
        "$FITATU_SESSION_PATH), else ~/.fitatu/session.json.",
    )
    export.add_argument("--from", dest="from_date", required=True, metavar="DATE", help="first day, YYYY-MM-DD")
    export.add_argument("--to", dest="to_date", required=True, metavar="DATE", help="last day, YYYY-MM-DD")
    export.add_argument("--format", choices=SummaryModule.EXPORT_FORMATS, default="csv", help="file format (default: csv)")
    export.add_argument("-o", "--output", required=True, metavar="PATH", help="file to write")
    export.add_argument("--overwrite", action="store_true", help="replace the file if it exists")
    export.set_defaults(run=_export)

    status = commands.add_parser(
        "status",
        help="describe the session file without sending anything; never prints a token",
        description="Show where the session comes from (the file and who can read it, or $FITATU_SESSION_JSON), "
        "the user id, API host, locales and time zone, when the bearer token expires, whether there is a "
        "refresh token, and the client version sent. Sends nothing unless --check. Exit status: 1 when there "
        "is no session or the check fails.",
    )
    status.add_argument("--check", action="store_true", help="also read the profile once, to see that Fitatu takes the session")
    status.set_defaults(run=_status)

    logout = commands.add_parser(
        "logout",
        help="delete the session file",
        description="Delete the session file ($FITATU_SESSION, $FITATU_SESSION_PATH or ~/.fitatu/session.json) "
        "after asking. Stop fitatu-mcp and other processes that use it first: they keep their tokens and "
        "write refreshed ones back. The library knows no call that revokes tokens at Fitatu.",
    )
    logout.add_argument("-y", "--yes", action="store_true", help="delete without asking")
    logout.set_defaults(run=_logout)

    doctor = commands.add_parser(
        "doctor",
        help="check the session, the clock and the client version with Fitatu",
        description="Check the setup and print one line per check (ok, WARN, FAIL): the session file and its "
        "permissions, the refresh token (by refreshing the bearer token once, saved like any refresh), this "
        "computer's clock against the new token's issue time, a profile read, whether a premium plan runs, and "
        "whether Fitatu's web app is still at the version this library sends. Prints no token, user id or "
        "personal data, so the output can go into a bug report; the library's own log lines only with "
        "--log-level. Exit status: 1 when a check failed.",
    )
    doctor.set_defaults(run=_doctor)

    mcp = commands.add_parser(
        "mcp",
        help="serve the agent tools to an MCP host over stdio, like fitatu-mcp",
        description="The same server as fitatu-mcp, for hosts that start the package by its name "
        '(e.g. uvx with an MCP Registry entry). Needs the mcp extra: pip install "fitatu-api[mcp]".',
    )
    add_mcp_options(mcp)
    mcp.set_defaults(run=_mcp)

    args = parser.parse_args(argv)
    setup_logging(args.log_level)
    return args.run(args)


if __name__ == "__main__":
    raise SystemExit(main())
