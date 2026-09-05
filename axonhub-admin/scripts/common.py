#!/usr/bin/env python3
"""
Shared logic for the axonhub-admin skill.
Single source of truth for: GraphQL transport, Relay pagination, value
fingerprints, and channel config.
"""

import hashlib
import json
import urllib.error
import urllib.request
from typing import Any, Callable, Optional

# ---------------------------------------------------------------------------
# Channel configuration (unified)
# ---------------------------------------------------------------------------

# Keyed by exact channel name — the server-assigned numeric ID drifts when
# channels are recreated, the name is what the operator recognizes.
CHANNELS: dict[str, dict] = {
    "Ali-Coding":    {"weight": 10, "tags": ["quota6000"], "quota": 6000,  "billing": "count"},
    "Sensenova":     {"weight": 9,  "tags": ["quota1500"], "quota": 1500,  "billing": "count"},
    "opencode-go":   {"weight": 8,  "tags": ["quota3000"], "quota": 45300, "billing": "token"},
    "opencode-luna": {"weight": 7,  "tags": ["quota2000"], "quota": 2000,  "billing": "token"},
    "GLM":           {"weight": 3,  "tags": ["quota80"],   "quota": 80,    "billing": "token"},
    "Ali-Token":     {"weight": 2,  "tags": ["quota100"],  "quota": 100,   "billing": "token"},
    "deepseek":      {"weight": 1,  "tags": ["quota0"],    "quota": 0,     "billing": "token"},
}

# Night-only models for Ali-Token (22:00-08:00) — priority boost in configure_models
ALI_TOKEN_NIGHT_MODELS = {"deepseek-v4-pro-0813", "qwen3.8-max"}

# ---------------------------------------------------------------------------
# GraphQL transport
# ---------------------------------------------------------------------------

def fetch_graphql(
    axonhub_url: str,
    token: str,
    query: str,
    variables: Optional[dict] = None,
    *,
    timeout: int = 30,
    opener: Callable[..., Any] = urllib.request.urlopen,
) -> dict:
    """Execute one GraphQL request without exposing the token in process argv.

    Returns the ``data`` object only; transport failures, GraphQL errors, and
    malformed responses raise RuntimeError so callers cannot ignore them.
    """

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
        with opener(request, timeout=timeout) as response:
            body = response.read().decode("utf-8")
    except (OSError, urllib.error.URLError) as exc:
        raise RuntimeError("AxonHub GraphQL request failed") from exc
    try:
        decoded = json.loads(body)
    except (TypeError, json.JSONDecodeError) as exc:
        raise RuntimeError("AxonHub returned invalid GraphQL JSON") from exc
    if decoded.get("errors"):
        detail = json.dumps(decoded["errors"], ensure_ascii=False)
        raise RuntimeError(f"AxonHub GraphQL returned an error: {detail}")
    data = decoded.get("data")
    if not isinstance(data, dict):
        raise RuntimeError("AxonHub GraphQL response has no data")
    return data


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
    nodes: list[dict] = []
    after: Optional[str] = None
    seen: set[str] = set()
    while True:
        data = fetch_graphql(axonhub_url, token, query, {"first": page_size, "after": after})
        connection = data.get(field)
        if not isinstance(connection, dict):
            raise RuntimeError(f"AxonHub {field} response is not paginated")
        for edge in connection.get("edges") or []:
            if isinstance(edge, dict) and isinstance(edge.get("node"), dict):
                nodes.append(edge["node"])
        page_info = connection.get("pageInfo") or {}
        if not page_info.get("hasNextPage"):
            return nodes
        cursor = page_info.get("endCursor")
        if not cursor or str(cursor) == str(after) or str(cursor) in seen:
            raise RuntimeError(f"AxonHub {field} pagination did not advance")
        seen.add(str(cursor))
        after = str(cursor)


# ---------------------------------------------------------------------------
# Fingerprints
# ---------------------------------------------------------------------------

def value_fingerprint(value: Any) -> str:
    """Stable sha256 over canonical JSON — the one optimistic-concurrency hash."""

    encoded = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()
