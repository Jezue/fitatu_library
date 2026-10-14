"""Entry point of the Fitatu .mcpb bundle: fitatu-mcp with the options picked in the host's settings.

The manifest passes the read-only checkbox as $FITATU_MCP_READ_ONLY ("true" or "false") and the
session file as $FITATU_SESSION, which fitatu-mcp reads itself.
"""

import os

from fitatu_api.mcp_server import main

main(["--read-only"] if os.environ.get("FITATU_MCP_READ_ONLY", "").lower() == "true" else [])
