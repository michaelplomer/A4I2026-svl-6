"""Google's managed BigQuery MCP server (https://bigquery.googleapis.com/mcp), consumed by ADK.

This is the challenge's "managed MCP server" requirement: we consume Google's server rather
than authoring our own. It gives the agent generic, READ-ONLY access to our tables for questions
no purpose-built tool covers ("hottest blocks in Bed-Stuy with no street trees", "how many
measured blocks are in district 36?"). Shaped questions still go through data_tools.py.

Only read-only tools are exposed (tool_filter): the server also offers execute_sql, which can
write, and job-control tools; the agent gets neither.

Auth: every request carries a fresh OAuth token from Application Default Credentials (the
Cloud Run service account in production; `gcloud auth application-default login` locally),
plus x-goog-user-project so usage is billed to the team project.
"""
from __future__ import annotations

import os
import subprocess
import threading

from google.adk.tools.mcp_tool import McpToolset, StreamableHTTPConnectionParams

PROJECT = os.environ.get("GOOGLE_CLOUD_PROJECT", "qwiklabs-gcp-03-941c9ce79592")
MCP_URL = os.environ.get("BIGQUERY_MCP_URL", "https://bigquery.googleapis.com/mcp")
READ_ONLY_TOOLS = ["list_table_ids", "get_table_info", "execute_sql_readonly"]

_lock = threading.Lock()
_creds = None


def _token() -> str:
    """A valid access token, refreshed when it's about to expire."""
    global _creds
    with _lock:
        try:
            import google.auth
            import google.auth.transport.requests

            if _creds is None:
                _creds, _ = google.auth.default(scopes=["https://www.googleapis.com/auth/cloud-platform"])
            if not _creds.valid:
                _creds.refresh(google.auth.transport.requests.Request())
            return _creds.token
        except Exception:
            # Local fallback: the gcloud CLI login (often the right account when ADC isn't).
            return subprocess.run(["gcloud", "auth", "print-access-token"], check=True,
                                  capture_output=True, text=True).stdout.strip()


def _headers(_ctx=None) -> dict[str, str]:
    return {"Authorization": f"Bearer {_token()}", "x-goog-user-project": PROJECT}


bigquery_mcp = McpToolset(
    connection_params=StreamableHTTPConnectionParams(url=MCP_URL, timeout=30, sse_read_timeout=60),
    tool_filter=READ_ONLY_TOOLS,
    header_provider=_headers,
)
