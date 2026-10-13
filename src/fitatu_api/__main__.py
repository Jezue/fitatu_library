"""Command line helpers.

``python -m fitatu_api login`` (or ``fitatu-api login`` once installed) logs in with an
e-mail and password and writes the session file - ``~/.fitatu/session.json`` unless
``-o`` or ``$FITATU_SESSION`` says otherwise - that ``FitatuApiClient.from_session()``
loads. ``fitatu-api import-session`` writes the same file from tokens copied out of the
web app, for accounts that have no password (Apple or Facebook sign-in).
"""

from __future__ import annotations

import argparse
import getpass
import json
import os
import sys
from collections.abc import Sequence

from .auth import FitatuAuthContext, resolve_session_path, write_private_json
from .client import FitatuApiClient, _new_correlation_id
from .exceptions import FitatuApiError, FitatuAuthError


def _save(client: FitatuApiClient, output: str | None) -> int:
    session = client.auth.to_session_data(include_tokens=True)
    path = resolve_session_path(output)
    write_private_json(path, session)
    print(f"Saved session for user {session.get('fitatu_user_id')} to {path}")
    return 0


def _login(args: argparse.Namespace) -> int:
    email = args.email or os.environ.get("FITATU_EMAIL") or input("Fitatu e-mail: ").strip()
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
    return _save(client, args.output)


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
        client._refresh(_new_correlation_id())
        client._read_profile(auth.fitatu_user_id, pick_host=args.base_url is None)
    except FitatuApiError as exc:
        advice = "; copy a fresh session from the web app" if isinstance(exc, FitatuAuthError) else ""
        print(f"Import failed: {exc}{advice}", file=sys.stderr)
        return 1
    return _save(client, args.output)


def _add_output_options(command: argparse.ArgumentParser) -> None:
    command.add_argument(
        "-o",
        "--output",
        help="where to write the session file (default: $FITATU_SESSION, $FITATU_SESSION_PATH "
        "or ~/.fitatu/session.json)",
    )
    command.add_argument(
        "--base-url",
        help="API host; by default the one for your account's region, "
        "e.g. https://de-de.fitatu.com/api for de_DE",
    )


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="fitatu-api", description="Helpers for the fitatu-api library.")
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

    args = parser.parse_args(argv)
    return _login(args) if args.command == "login" else _import_session(args)


if __name__ == "__main__":
    raise SystemExit(main())
