"""Entry point of the Fitatu .mcpb bundle: fitatu-mcp with the options picked in the host's settings.

The manifest passes the read-only checkbox as $FITATU_MCP_READ_ONLY ("true" or "false") and the
session file as $FITATU_SESSION, which fitatu-mcp reads itself. Claude Desktop passes the
setting's default ``${HOME}/.fitatu/session.json`` without expanding it, so it is expanded here;
a value that is empty or still unexpanded falls back to fitatu-mcp's own default path.
"""

import os

from fitatu_api.mcp_server import main

session = os.path.expandvars(os.environ.get("FITATU_SESSION", ""))
if session and "$" not in session:
    os.environ["FITATU_SESSION"] = session
else:
    os.environ.pop("FITATU_SESSION", None)

main(["--read-only"] if os.environ.get("FITATU_MCP_READ_ONLY", "").lower() == "true" else [])
