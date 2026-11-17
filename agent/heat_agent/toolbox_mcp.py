"""MCP Toolbox for Databases: our second MCP server (agent/toolbox/tools.yaml on Cloud Run).

bigquery_mcp.py consumes Google's managed BigQuery MCP server for generic, read-only SQL.
This file consumes MCP Toolbox, where we DEFINED shaped, parameterized queries
(neighbourhood / council-district summaries, trees near a point, block profile) that the
agent can call by name. Deploy with agent/toolbox/deploy.sh, then set TOOLBOX_URL.

Disabled (returns None) when TOOLBOX_URL is unset, so adk web and the portal keep working.

Auth: the Toolbox service is private (Cloud Run IAM). Every request carries an ID token for
the service URL: from the Cloud Run service account in production, or
`gcloud auth print-identity-token` locally (set TOOLBOX_ID_TOKEN to override).
"""
from __future__ import annotations

import os
import subprocess
import threading
import time

from google.adk.tools.mcp_tool import McpToolset, StreamableHTTPConnectionParams

TOOLBOX_URL = os.environ.get("TOOLBOX_URL", "").rstrip("/")
TOOLSET = os.environ.get("TOOLBOX_TOOLSET", "heatrx")

_lock = threading.Lock()
_token: dict = {"value": None, "at": 0.0}


def _id_token() -> str | None:
    if os.environ.get("TOOLBOX_ID_TOKEN"):
        return os.environ["TOOLBOX_ID_TOKEN"]
    with _lock:
        if _token["value"] and time.time() - _token["at"] < 45 * 60:
            return _token["value"]
        tok = None
        try:
            import google.auth.transport.requests
            from google.oauth2 import id_token

            tok = id_token.fetch_id_token(google.auth.transport.requests.Request(), TOOLBOX_URL)
        except Exception:
            try:
                tok = subprocess.run(["gcloud", "auth", "print-identity-token"], check=True,
                                     capture_output=True, text=True).stdout.strip()
            except Exception:
                tok = None
        _token.update(value=tok, at=time.time())
        return tok


def _headers(_ctx=None) -> dict[str, str]:
    tok = _id_token()
    return {"Authorization": f"Bearer {tok}"} if tok else {}


def make_toolbox_mcp() -> McpToolset | None:
    """A fresh toolset (ADK agents should not share one instance), or None if not configured."""
    if not TOOLBOX_URL:
        return None
    return McpToolset(
        # Toolbox serves every tool at /mcp and one toolset at /mcp/<toolset>.
        connection_params=StreamableHTTPConnectionParams(url=f"{TOOLBOX_URL}/mcp" + (f"/{TOOLSET}" if TOOLSET else ""),
                                                         timeout=30, sse_read_timeout=60),
        header_provider=_headers,
    )


toolbox_mcp = make_toolbox_mcp()
