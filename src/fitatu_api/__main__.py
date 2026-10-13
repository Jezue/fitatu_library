"""Command line helpers.

``python -m fitatu_api login`` (or ``fitatu-api login`` once installed) logs in with an
e-mail and password and writes a ``session_data.json`` that ``FitatuAuthContext``,
``FitatuLibrary``, ``example.py`` and ``demo.py`` can load.
"""

from __future__ import annotations

import argparse
import getpass
import os
import sys
from collections.abc import Sequence
from pathlib import Path

from .auth import write_private_json
from .client import FitatuApiClient
from .constants import DEFAULT_FITATU_API_BASE_URL
from .exceptions import FitatuApiError


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
        return 1

    session = client.auth.to_session_data(include_tokens=True)
    output = Path(args.output).expanduser()
    write_private_json(output, session)
    print(f"Saved session for user {session.get('fitatu_user_id')} to {output}")
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="fitatu-api", description="Helpers for the fitatu-api library.")
    commands = parser.add_subparsers(dest="command", required=True)

    login = commands.add_parser(
        "login",
        help="log in with e-mail and password and save session_data.json",
        description=(
            "Log in and save a session file (bearer token, refresh token, user id). "
            "Reads FITATU_EMAIL / FITATU_PASSWORD when set, otherwise prompts. "
            "Accounts created with Apple or Facebook have no password; see the README "
            "for copying the session from the web app instead."
        ),
    )
    login.add_argument("--email", help="account e-mail (prompted when omitted)")
    login.add_argument("-o", "--output", default="session_data.json", help="where to write the session file")
    login.add_argument(
        "--base-url",
        default=DEFAULT_FITATU_API_BASE_URL,
        help="API host for your account's region, e.g. https://de-de.fitatu.com/api",
    )

    args = parser.parse_args(argv)
    return _login(args)


if __name__ == "__main__":
    raise SystemExit(main())
