"""Build dist/fitatu-<version>.mcpb, the Claude Desktop bundle of fitatu-mcp, from mcpb/.

    python scripts/build_mcpb.py [--out DIR]

The bundle is a zip of mcpb/manifest.json (MCPB manifest 0.4, server type ``uv``) with this
release's version and the tool list added, mcpb/server/main.py, and a pyproject.toml that pins
``fitatu-api[mcp]`` to this version: the host runs ``uv run`` on it, which installs that version
from PyPI. So build it only for a version that is on PyPI. Installed through the Claude Desktop UI;
the host does not replace an installed extension of the same version, so uninstall it first.
"""

from __future__ import annotations

import argparse
import json
import sys
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from fitatu_api import __version__  # noqa: E402
from fitatu_api.agent import TOOLS  # noqa: E402

PYPROJECT = f"""\
# Written by scripts/build_mcpb.py: the host's `uv run` installs these.
[project]
name = "fitatu-mcpb"
version = "{__version__}"
requires-python = ">=3.11"
dependencies = ["fitatu-api[mcp]=={__version__}"]
"""


def build(out_dir: Path) -> Path:
    manifest = json.loads((ROOT / "mcpb" / "manifest.json").read_text(encoding="utf-8"))
    manifest["version"] = __version__
    # Shown on the install page; the server lists the same tools at run time.
    manifest["tools"] = [{"name": tool["name"], "description": tool["title"]} for tool in TOOLS]
    out_dir.mkdir(parents=True, exist_ok=True)
    bundle = out_dir / f"fitatu-{__version__}.mcpb"
    with zipfile.ZipFile(bundle, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("manifest.json", json.dumps(manifest, ensure_ascii=False, indent=2) + "\n")
        archive.writestr("pyproject.toml", PYPROJECT)
        archive.write(ROOT / "mcpb" / "server" / "main.py", "server/main.py")
        archive.write(ROOT / "LICENSE", "LICENSE")
    return bundle


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--out", type=Path, default=ROOT / "dist", help="directory to write it to (default: dist/)")
    print(build(parser.parse_args().out))
