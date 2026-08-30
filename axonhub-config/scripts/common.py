#!/usr/bin/env python3
"""
Shared logic for axonhub-config skill (server-side).
Single source of truth for: GraphQL client, channel config, constants.
"""

import json
import urllib.error
import urllib.request
from typing import Optional

# ---------------------------------------------------------------------------
# Channel configuration (unified)
# ---------------------------------------------------------------------------

CHANNELS = {
    4: {"name": "Ali-Coding",   "weight": 10, "tags": ["quota6000"],  "quota": 6000,  "billing": "count"},
    3: {"name": "Sensenova",    "weight": 9,  "tags": ["quota1500"],  "quota": 1500,  "billing": "count"},
    6: {"name": "opencode-go",  "weight": 8,  "tags": ["quota3000"],  "quota": 45300, "billing": "token", "peak": "09:00-12:00,14:00-18:00"},
    7: {"name": "opencode-luna","weight": 7,  "tags": ["quota2000"],  "quota": 2000,  "billing": "token", "peak": "09:00-12:00,14:00-18:00"},
    2: {"name": "GLM",          "weight": 3,  "tags": ["quota80"],    "quota": 80,    "billing": "token", "peak": "14:00-18:00"},
    5: {"name": "Ali-Token",    "weight": 2,  "tags": ["quota100"],   "quota": 100,   "billing": "token", "peak": "08:00-22:00"},
    8: {"name": "deepseek",     "weight": 1,  "tags": ["quota0"],     "quota": 0,     "billing": "token", "peak": "09:00-12:00,14:00-18:00"},
}

# Night-only models for Ali-Token (22:00-08:00) — priority boost in configure_models
ALI_TOKEN_NIGHT_MODELS = {"deepseek-v4-pro-0813", "qwen3.8-max"}

# ---------------------------------------------------------------------------
# GraphQL client
# ---------------------------------------------------------------------------

def fetch_graphql(axonhub_url: str, token: str, query: str, variables: Optional[dict] = None) -> dict:
    """Execute GraphQL without exposing the bearer token in process argv."""

    request = urllib.request.Request(
        f"{axonhub_url.rstrip('/')}/admin/graphql",
        data=json.dumps({"query": query, "variables": variables or {}}).encode(),
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {token}",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            result = json.load(response)
    except (OSError, urllib.error.URLError) as exc:
        raise RuntimeError("AxonHub GraphQL request failed") from exc
    if result.get("errors"):
        raise RuntimeError("AxonHub GraphQL returned an error")
    return result


def fetch_connection(
    axonhub_url: str,
    token: str,
    field: str,
    node_selection: str,
    *,
    page_size: int = 100,
) -> list[dict]:
    """Fetch every Relay page and reject repeated cursors."""

    query = (
        f"query Paged{field.title()}($first: Int!, $after: Cursor) {{ "
        f"{field}(first: $first, after: $after) {{ "
        f"edges {{ node {{ {node_selection} }} }} "
        "pageInfo { hasNextPage endCursor } } }"
    )
    nodes = []
    after = None
    seen = set()
    while True:
        response = fetch_graphql(
            axonhub_url, token, query, {"first": page_size, "after": after}
        )
        connection = response.get("data", {}).get(field, {})
        for edge in connection.get("edges") or []:
            if isinstance(edge, dict) and isinstance(edge.get("node"), dict):
                nodes.append(edge["node"])
        page_info = connection.get("pageInfo") or {}
        if not page_info.get("hasNextPage"):
            return nodes
        cursor = page_info.get("endCursor")
        if not cursor or cursor == after or cursor in seen:
            raise RuntimeError(f"AxonHub {field} pagination did not advance")
        seen.add(cursor)
        after = cursor
