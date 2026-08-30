#!/usr/bin/env python3
"""Fetch and normalize the OpenCode Go document.

``watch-go`` is deliberately a source watcher, not a mapping step.  It reads
the OpenCode Go ``go.mdx`` document and writes one versioned JSON snapshot to
``data/go.json``.  The mapping builder consumes this snapshot together with
the model cache and Arena snapshot.

The parser keeps incomplete rows.  Missing values are useful information to
the AxonHub sync step and must not make a model disappear from the source
snapshot.  Free models retain the historical ``fix_free`` behaviour: missing
quota values are filled from the largest non-free value and their prices are
zero.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Mapping, Optional
from urllib.request import Request, urlopen

from parse_opencode_mdx import parse_mdx


GO_MDX_URL = (
    "https://raw.githubusercontent.com/anomalyco/opencode/"
    "dev/packages/web/src/content/docs/go.mdx"
)
GO_COMMIT_API_URL = (
    "https://api.github.com/repos/anomalyco/opencode/commits"
    "?path=packages/web/src/content/docs/go.mdx&sha=dev&per_page=1"
)
SCHEMA_VERSION = 1


def fetch_url(url: str, timeout: int = 30) -> str:
    """Fetch text over HTTP using only the Python standard library."""

    request = Request(url, headers={"User-Agent": "models-mapping/watch-go"})
    with urlopen(request, timeout=timeout) as response:
        return response.read().decode("utf-8")


def fetch_latest_commit(url: str = GO_COMMIT_API_URL) -> Optional[str]:
    """Return the latest source commit, or ``None`` when unavailable.

    Commit metadata is helpful for auditing but should not prevent parsing a
    document supplied by a caller (for example, an offline fixture).
    """

    try:
        payload = json.loads(fetch_url(url))
    except (OSError, ValueError):
        return None

    if isinstance(payload, list) and payload:
        first = payload[0]
        if isinstance(first, Mapping):
            commit = first.get("sha")
            if commit:
                return str(commit)
    return None


def _missing(value: Any) -> bool:
    return value is None or value == ""


def fix_free_models(models: Dict[str, dict]) -> Dict[str, dict]:
    """Fill missing values for free models without changing explicit values.

    ``rp5h`` and ``usage_quota`` are derived from the largest non-free model.
    The OpenCode Go document treats free models as zero-priced, so all price
    fields are explicitly set to zero.  The function mutates and returns the
    supplied mapping for convenient use by callers and tests.
    """

    max_rp5h = 0
    max_usage = 0
    for model_id, model in models.items():
        if "free" in model_id.lower():
            continue
        rp5h = _as_number(model.get("rp5h"))
        usage = _as_number(model.get("usage_quota"))
        if rp5h is not None:
            max_rp5h = max(max_rp5h, rp5h)
        if usage is not None:
            max_usage = max(max_usage, usage)

    for model_id, model in models.items():
        if "free" not in model_id.lower():
            continue
        if _missing(model.get("rp5h")):
            model["rp5h"] = _number_for_json(max_rp5h)
        if _missing(model.get("usage_quota")):
            model["usage_quota"] = _number_for_json(max_usage)
        for field in (
            "price_input",
            "price_output",
            "max_price_output",
            "price_cached_read",
            "price_cached_write",
        ):
            model[field] = 0

    return models


def _as_number(value: Any) -> Optional[float]:
    if _missing(value):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if number < 0:
        return None
    return number


def _number_for_json(value: float | int) -> int | float:
    numeric = float(value)
    return int(numeric) if numeric.is_integer() else numeric


def _json_value(value: Any) -> Any:
    """Convert parser values to stable JSON values.

    The old parser uses ``-`` for display-only unavailable values.  In the
    source snapshot, ``null`` makes absence machine-readable and avoids
    accidentally treating a dash as a real number.
    """

    if value in ("", "-"):
        return None
    return value


def parse_go_mdx(content: str) -> Dict[str, dict]:
    """Parse ``go.mdx`` into a model-id keyed source mapping.

    Model IDs come from the document's endpoint table when available.  For a
    row that has no endpoint entry yet, the normalized document key is used so
    that the model is retained and can be reported as incomplete later.
    """

    parsed = parse_mdx(content, include_incomplete=True)
    models: Dict[str, dict] = {}
    for key, source in parsed.items():
        model_id = str(source.get("model_id") or key).strip()
        if not model_id:
            continue

        # Keep all useful source details.  The mapping CSV deliberately
        # contains only its five calculation/result columns; the sync skill
        # can use the remaining fields from this JSON snapshot.
        record = {
            "model_id": model_id,
            "name": source.get("name") or model_id,
            "protocol": source.get("protocol"),
            "endpoint": source.get("endpoint"),
            "rp5h": _json_value(source.get("rp5h")),
            "rpw": _json_value(source.get("rpw")),
            "rpm": _json_value(source.get("rpm")),
            "usage_quota": _json_value(source.get("usage_quota")),
            "price_input": _json_value(source.get("price_input")),
            "price_output": _json_value(source.get("price_output")),
            "max_price_output": _json_value(source.get("max_price_output")),
            "price_cached_read": _json_value(source.get("price_cached_read")),
            "price_cached_write": _json_value(source.get("price_cached_write")),
            "context_threshold": _json_value(source.get("context_threshold")),
            "peak_hours": _json_value(source.get("peak_hours")),
            "retention": _json_value(source.get("retention")),
        }
        models[model_id] = record

    return fix_free_models(models)


def build_go_snapshot(
    models: Mapping[str, Mapping[str, Any]],
    *,
    source_url: str = GO_MDX_URL,
    source_commit: Optional[str] = None,
    fetched_at: Optional[str] = None,
) -> dict:
    """Build a schema-versioned ``go.json`` object."""

    timestamp = fetched_at or datetime.now(timezone.utc).isoformat()
    normalized = {str(model_id): dict(model) for model_id, model in models.items()}
    return {
        "schema_version": SCHEMA_VERSION,
        "source": {
            "url": source_url,
            "commit": source_commit,
            "fetched_at": timestamp,
        },
        "models": normalized,
    }


def write_json_atomic(path: Path, payload: Mapping[str, Any]) -> None:
    """Write JSON atomically in the destination directory."""

    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, ensure_ascii=False, indent=2)
            handle.write("\n")
        os.replace(temporary, path)
    except Exception:
        try:
            os.unlink(temporary)
        except OSError:
            pass
        raise


def preserve_timestamp_when_unchanged(path: Path, snapshot: dict) -> dict:
    """Keep snapshots stable when source content and identity did not change."""

    if not path.exists():
        return snapshot
    try:
        existing = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return snapshot
    old_source = existing.get("source", {}) if isinstance(existing, Mapping) else {}
    new_source = snapshot.get("source", {})
    if (
        existing.get("schema_version") == snapshot.get("schema_version")
        and existing.get("models") == snapshot.get("models")
        and old_source.get("url") == new_source.get("url")
        and old_source.get("commit") == new_source.get("commit")
        and old_source.get("fetched_at")
    ):
        new_source["fetched_at"] = old_source["fetched_at"]
    return snapshot


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Fetch and write OpenCode Go JSON")
    parser.add_argument(
        "input",
        nargs="?",
        help="Local go.mdx file; omit to fetch the upstream document",
    )
    parser.add_argument("--url", default=GO_MDX_URL, help="go.mdx source URL")
    parser.add_argument(
        "--source-commit",
        help="Source commit to store in metadata (fetched from GitHub when omitted)",
    )
    parser.add_argument(
        "--output",
        "-o",
        type=Path,
        default=Path("data/go.json"),
        help="Output JSON path (default: data/go.json)",
    )
    args = parser.parse_args(argv)

    try:
        if args.input:
            content = Path(args.input).read_text(encoding="utf-8")
            commit = args.source_commit
        else:
            content = fetch_url(args.url)
            commit = args.source_commit or fetch_latest_commit()
        models = parse_go_mdx(content)
        if not models:
            raise ValueError("no models found in go.mdx")
        snapshot = build_go_snapshot(
            models,
            source_url=args.url,
            source_commit=commit,
        )
        snapshot = preserve_timestamp_when_unchanged(args.output, snapshot)
        write_json_atomic(args.output, snapshot)
    except (OSError, ValueError) as exc:
        print(f"watch-go: {exc}", file=sys.stderr)
        return 1

    print(f"watch-go: wrote {len(models)} models to {args.output}")
    if snapshot["source"]["commit"]:
        print(f"watch-go: source commit {snapshot['source']['commit']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
