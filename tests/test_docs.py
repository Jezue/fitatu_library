"""The docs link only to files that exist, AGENTS.md lists exactly the agent tools, its offline example runs,
and server.json matches the release."""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

import pytest

from fitatu_api import __version__
from fitatu_api.agent import TOOLS

ROOT = Path(__file__).resolve().parents[1]
DOCS = [ROOT / "README.md", ROOT / "CONTRIBUTING.md", ROOT / "SECURITY.md", *sorted((ROOT / "docs").glob("*.md"))]
BLOB = "https://github.com/Jezue/fitatu_library/blob/main/"
LINK = re.compile(r"\]\(([^)\s]+)\)")
TOOL_ROW = re.compile(r"^\| `(\w+)` \|.*\| (read|write) \| (yes|no) \| (yes|no) \|$", re.MULTILINE)


def _local_links(doc: Path) -> list[tuple[str, Path]]:
    """``(link, file it points to)`` for relative links and links into this repo on GitHub."""
    links = []
    for target in LINK.findall(doc.read_text(encoding="utf-8")):
        path = target.split("#", 1)[0]
        if path.startswith(BLOB):
            links.append((target, ROOT / path.removeprefix(BLOB)))
        elif path and ":" not in path:
            links.append((target, doc.parent / path))
    return links


@pytest.mark.parametrize("doc", DOCS, ids=lambda doc: doc.relative_to(ROOT).as_posix())
def test_links_point_to_files_in_the_repo(doc: Path) -> None:
    missing = [target for target, path in _local_links(doc) if not path.exists()]
    assert not missing, f"{doc.relative_to(ROOT)} links to files that do not exist: {missing}"


def test_readme_shows_the_agent_and_session_entry_points() -> None:
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    assert "FitatuTools" in readme
    assert "from_session" in readme
    assert _local_links(ROOT / "README.md"), "the link check found no links in the README"


def test_agents_md_lists_every_tool_with_its_annotations() -> None:
    documented = {
        name: (kind, destructive, idempotent)
        for name, kind, destructive, idempotent in TOOL_ROW.findall((ROOT / "docs" / "AGENTS.md").read_text(encoding="utf-8"))
    }
    yes_no = {True: "yes", False: "no"}
    expected = {
        tool["name"]: (
            "read" if tool["annotations"]["readOnlyHint"] else "write",
            yes_no[tool["annotations"]["destructiveHint"]],
            yes_no[tool["annotations"]["idempotentHint"]],
        )
        for tool in TOOLS
    }
    assert documented == expected


def test_the_offline_example_in_agents_md_runs(monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    section = (ROOT / "docs" / "AGENTS.md").read_text(encoding="utf-8").split("## Try it without an account", 1)[1]
    code = section.split("```python\n", 1)[1].split("```", 1)[0]
    monkeypatch.chdir(ROOT)
    monkeypatch.setattr(sys, "path", list(sys.path))  # the example adds tests/ to it
    exec(compile(code, "docs/AGENTS.md", "exec"), {})
    out = capsys.readouterr().out
    assert "'ok': False" not in out and "'energy': 350.0" in out


def test_server_json_is_this_version_and_the_readme_claims_its_name() -> None:
    server = json.loads((ROOT / "server.json").read_text(encoding="utf-8"))
    assert (server["version"], [package["version"] for package in server["packages"]]) == (__version__, [__version__])
    # The MCP Registry accepts the PyPI package only when its README carries this line.
    assert f"<!-- mcp-name: {server['name']} -->" in (ROOT / "README.md").read_text(encoding="utf-8")
