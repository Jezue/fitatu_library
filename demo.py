"""Try the agent tools on your own account, one call at a time.

The menu lists fitatu_api.agent.TOOLS. Pick a tool, answer its arguments (each prompt
shows the description, allowed values and default from the tool's input schema) and see
the JSON that FitatuTools.call() returns: exactly what an AI agent gets back. A tool that
writes asks before it runs, and a write to today or an earlier day, which changes your
real diary, needs its date typed twice.

    python demo.py

The session comes from example.init_api(): ~/.fitatu/session.json (or $FITATU_SESSION),
or a login when there is none.
"""

from __future__ import annotations

import json
from datetime import date
from typing import Any

from example import init_api
from fitatu_api import FitatuApiError, FitatuTools
from fitatu_api.agent import TOOLS


def kind(tool: dict[str, Any]) -> str:
    hints = tool["annotations"]
    if hints["readOnlyHint"]:
        return "read"
    return "write, destructive" if hints["destructiveHint"] else "write"


def print_tools() -> None:
    for number, tool in enumerate(TOOLS, 1):
        print(f"{number:>3}  {tool['name']:<20} {kind(tool)}")


def ask(name: str, schema: dict[str, Any], required: bool) -> Any:
    """Prompt for one argument until the answer parses; None leaves an optional one out."""
    notes = [schema["description"]]
    if "enum" in schema:
        notes.append("Allowed: " + ", ".join(schema["enum"]) + ".")
    if schema["type"] == "boolean":
        notes.append("Type true or false.")
    if schema["type"] == "array":
        notes.append("Separate values with spaces." if schema["items"]["type"] == "string" else "Type a JSON array.")
    if "default" in schema:
        notes.append(f"Default: {json.dumps(schema['default'])}.")
    print(f"{name}{'' if required else ' (optional, Enter skips it)'}: {' '.join(notes)}")
    while True:
        text = input("> ").strip()
        if not text:
            if not required:
                return None
            print("This one is required.")
        elif schema["type"] == "string":
            return text
        elif schema["type"] == "array" and schema["items"]["type"] == "string":
            return text.replace(",", " ").split()
        else:
            try:
                return json.loads(text)  # integer, number, boolean or a JSON array of objects
            except ValueError:
                print(f"Not a JSON {schema['type']}.")


def confirm_write(tool: dict[str, Any], arguments: dict[str, Any]) -> bool:
    """Ask before a tool writes; a date on or before today has to be typed twice."""
    if tool["annotations"]["destructiveHint"]:
        print(f"{tool['name']} changes your Fitatu diary and can overwrite or delete what is logged.")
    else:
        print(f"{tool['name']} adds to your Fitatu diary.")
    for name, schema in tool["input_schema"]["properties"].items():
        if schema.get("format") != "date" or name not in arguments:
            continue
        value = arguments[name]
        try:
            future = date.fromisoformat(value) > date.today()
        except ValueError:
            print(f"{name} {value!r} is not a YYYY-MM-DD date; nothing was sent.")
            return False
        if not future:
            again = input(f"{name} {value} is not after today, so this changes your real diary. Type the date again to go on: ")
            if again.strip() != value:
                print("Not the same date; nothing was sent.")
                return False
    return input("Send it? [y/N] ").strip().lower() in ("y", "yes")


def run(tools: FitatuTools) -> None:
    by_name = {tool["name"]: tool for tool in TOOLS}
    print_tools()
    while True:
        choice = input("\nTool number or name (Enter lists them, q quits): ").strip()
        if choice == "q":
            return
        if choice.isdigit() and 1 <= int(choice) <= len(TOOLS):
            choice = TOOLS[int(choice) - 1]["name"]
        tool = by_name.get(choice)
        if tool is None:
            print_tools()
            continue
        print(tool["description"])
        schema = tool["input_schema"]
        arguments: dict[str, Any] = {}
        for name, prop in schema["properties"].items():
            value = ask(name, prop, name in schema["required"])
            if value is not None:
                arguments[name] = value
        if not tool["annotations"]["readOnlyHint"] and not confirm_write(tool, arguments):
            continue
        print(json.dumps(tools.call(tool["name"], arguments), indent=2, ensure_ascii=False))


def main() -> None:
    try:
        client = init_api()
    except FitatuApiError as exc:
        raise SystemExit(f"Fitatu is not available right now: {exc}") from None
    with FitatuTools(client) as tools:
        try:
            run(tools)
        except (EOFError, KeyboardInterrupt):
            print()


if __name__ == "__main__":
    main()
