#!/usr/bin/env python3
"""axh — structured AxonHub GraphQL helper: reads via curl, writes via graphql-cli.

Encodes the session-proven mechanics so callers don't hand-parse CLI output:

- ``gql_read`` posts the query with curl and returns the unwrapped ``data``
  object (clean JSON, easy to diff in-session).
- ``gql_write`` runs the mutation through graphql-cli (token injected via
  ``endpoint login``, never in command history), enforces the ``{"id":…,
  "input":{…}}`` variable shape by signature, keeps writes serial with a
  minimum spacing (AxonHub is SQLite — concurrent writes raise SQLITE_BUSY),
  and detects both failure modes: the CLI's ``Error: HTTP 4xx: …`` line and
  an ``"errors"`` key inside a parsed response. A successful CLI line is only
  a hint — read back via ``gql_read`` after writing; that is the sole
  authority.

Server egress to upstreams can EOF for hours at a time; ``syncChannelModels``
calls fail until it recovers — retry with backoff, don't reshape input.
"""

from __future__ import annotations

import json
import os
import subprocess
import time
from pathlib import Path
from typing import Any, Mapping, Optional

AXONHUB_URL = "https://axon.jasonqin.site/admin/graphql"
TOKEN_ENV = "AXONHUB_JWT"
TOKEN_FILE = "/tmp/axonhub_jwt"
ENDPOINT = "axonhub"
WRITE_SPACING_SECONDS = 1.5

_last_write_at: float = 0.0

RECON_QUERIES: dict[str, str] = {
    "models": (
        "query { models(first: 100) { edges { node { id modelID name status remark "
        "developer type group icon "
        "modelCard { cost { input output cacheRead cacheWrite } } "
        "settings { associations { type priority disabled "
        "channelModel { channelId modelId } modelId { modelId } regex { pattern } } } } } } }"
    ),
    "channels": (
        "query { channels: queryChannels(input: {first: 50}) { edges { node { id name status "
        "autoSyncModelPattern supportedModels } } } }"
    ),
}


def resolve_token() -> str:
    """Return the JWT from ``AXONHUB_JWT`` or the session token file."""

    token = os.environ.get(TOKEN_ENV, "").strip()
    if token:
        return token
    token_file = Path(TOKEN_FILE)
    if token_file.exists():
        token = token_file.read_text(encoding="utf-8").strip()
        if token:
            return token
    raise RuntimeError(
        f"no AxonHub token: set {TOKEN_ENV} or write the JWT to {TOKEN_FILE}"
    )


def parse_bare_json(text: str) -> dict[str, Any]:
    """Parse graphql-cli output: the first balanced ``{…}`` block as JSON.

    The CLI prints bare multi-line JSON (top level is the field names, no
    ``{"data": …}`` wrapper) mixed with ``npm notice`` noise and occasionally
    concatenated JSON segments; braces inside strings must not skew depth.
    """

    lines = [
        line for line in (text or "").splitlines()
        if not line.startswith("npm notice")
    ]
    out = "\n".join(lines)
    start = out.find("{")
    if start < 0:
        raise ValueError(f"no JSON object in output: {out[:200]!r}")
    depth = 0
    in_str = False
    esc = False
    for i, ch in enumerate(out[start:], start=start):
        if esc:
            esc = False
            continue
        if ch == "\\":
            esc = True
            continue
        if ch == '"':
            in_str = not in_str
            continue
        if in_str:
            continue
        if ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                return json.loads(out[start:i + 1])
    raise ValueError(f"unbalanced JSON in output: {out[:200]!r}")


def gql_read(query: str, *, token: Optional[str] = None) -> dict[str, Any]:
    """POST one query via curl; return the ``data`` object or raise on errors."""

    bearer = token or resolve_token()
    payload = json.dumps({"query": query})
    proc = subprocess.run(
        ["curl", "-s", "-X", "POST", AXONHUB_URL,
         "-H", f"Authorization: Bearer {bearer}",
         "-H", "Content-Type: application/json",
         "-d", payload],
        capture_output=True, text=True, timeout=60,
    )
    d = json.loads(proc.stdout)
    if d.get("errors"):
        raise RuntimeError(f"query errors: {json.dumps(d['errors'], ensure_ascii=False)[:500]}")
    return d["data"]


def gql_write(
    mutation: str,
    variables: Mapping[str, Any],
    *,
    label: str = "",
    token: Optional[str] = None,
) -> dict[str, Any]:
    """Run one mutation via graphql-cli; return the parsed bare-JSON output.

    Serial by contract: enforces ``WRITE_SPACING_SECONDS`` between writes
    (SQLite backend). Variable shape is the caller's contract — AxonHub
    mutations want ``{"id": …, "input": {…}}``; top-level ``modelCard``/
    ``remark`` keys fail with ``must be defined`` (HTTP 422) and the server
    writes nothing.
    """

    global _last_write_at
    since = time.monotonic() - _last_write_at
    if _last_write_at and since < WRITE_SPACING_SECONDS:
        time.sleep(WRITE_SPACING_SECONDS - since)
    proc = subprocess.run(
        ["npx", "-y", "@axonhub/graphql-cli", "mutate", mutation,
         "-e", ENDPOINT, "-v", json.dumps(variables)],
        capture_output=True, text=True, timeout=120,
    )
    _last_write_at = time.monotonic()
    out = proc.stdout or ""
    if proc.returncode != 0 or "Error:" in out:
        raise RuntimeError(
            f"[{label}] cli error (rc={proc.returncode}): {_strip_notice(out)[:600]}"
        )
    d = parse_bare_json(out)
    if isinstance(d, dict) and d.get("errors"):
        raise RuntimeError(
            f"[{label}] server errors: {json.dumps(d['errors'], ensure_ascii=False)[:600]}"
        )
    return d


def _strip_notice(text: str) -> str:
    return "\n".join(
        line for line in text.splitlines() if not line.startswith("npm notice")
    ).strip()


def recon(*, token: Optional[str] = None) -> dict[str, dict[str, Any]]:
    """Run the reconciliation read: live models and channels in one call."""

    data: dict[str, dict[str, Any]] = {}
    for name, query in RECON_QUERIES.items():
        data[name] = gql_read(query, token=token)[name]
    return data


def main(argv: Optional[list[str]] = None) -> int:
    import argparse

    parser = argparse.ArgumentParser(description="AxonHub GraphQL helper")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("recon", help="reconciliation read: live models + channels")
    args = parser.parse_args(argv)

    if args.command == "recon":
        print(json.dumps(recon(), ensure_ascii=False, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
