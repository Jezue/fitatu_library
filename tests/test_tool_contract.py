"""The agent tools' contract: names, parameters with their types, required arguments, annotations.

Models, host allowlists and saved MCP permissions refer to tools by these, so an unplanned change
here breaks them (docs/RELEASING.md, "Compatibility policy"). After a deliberate change, rewrite
the snapshot with ``python tests/test_tool_contract.py`` and review its diff.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from fitatu_api.agent import TOOLS

SNAPSHOT = Path(__file__).parent / "fixtures" / "tool_contract.json"


def contract() -> dict[str, Any]:
    return {
        tool["name"]: {
            "parameters": {name: schema["type"] for name, schema in sorted(tool["input_schema"]["properties"].items())},
            "required": sorted(tool["input_schema"].get("required", [])),
            "annotations": tool["annotations"],
        }
        for tool in TOOLS
    }


def test_the_tools_match_the_snapshot() -> None:
    assert contract() == json.loads(SNAPSHOT.read_text(encoding="utf-8")), (
        "the agent tools changed: check docs/RELEASING.md (Compatibility policy), then rewrite "
        "tests/fixtures/tool_contract.json with: python tests/test_tool_contract.py"
    )


if __name__ == "__main__":
    SNAPSHOT.write_text(json.dumps(contract(), indent=2) + "\n", encoding="utf-8")
