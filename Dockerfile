# fitatu-mcp over Streamable HTTP, for n8n or another computer: docs/AGENTS.md#remote-http-and-docker.
# docker build -t fitatu-mcp . && docker run -e FITATU_MCP_TOKEN -v fitatu-data:/data -p 127.0.0.1:8765:8765 fitatu-mcp
# The base image is pinned to a digest; Dependabot (.github/dependabot.yml) proposes updates.
FROM python:3.14-slim@sha256:a2b82f3c48559aa0a8446d9af49826b6e2b2016f4cd2afabfe6013ec53729170
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1 PIP_DISABLE_PIP_VERSION_CHECK=1 \
    FITATU_SESSION=/data/session.json
WORKDIR /src
COPY pyproject.toml README.md LICENSE ./
COPY src ./src
RUN pip install --root-user-action=ignore ".[mcp]" \
    && useradd --uid 10001 --create-home fitatu \
    && mkdir /data && chown fitatu /data && chmod 700 /data
USER fitatu
# A directory, not the file: a refreshed token replaces session.json, which a mounted file cannot take.
VOLUME /data
EXPOSE 8765
HEALTHCHECK --interval=30s --timeout=3s --start-period=10s \
    CMD ["python", "-c", "import socket; socket.create_connection(('127.0.0.1', 8765), 2)"]
ENTRYPOINT ["fitatu-mcp", "--http", "--host", "0.0.0.0"]
