#!/usr/bin/env python3
"""Fetch and normalize the Arena WebDev leaderboard.

This watcher owns only ``data/arena.json``.  Joining Arena names to OpenCode
model IDs belongs to ``build_mapping.py``; keeping that join out of this
script means an Arena refresh cannot partially rewrite the mapping workspace.

The parser keeps the historical matching helpers exported from this module
for callers that use them directly.  The generated JSON is keyed by the
normalized Arena model ID and includes the source metadata required for audit.
"""

from __future__ import annotations

import argparse
import json
import math
import os
import re
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, Tuple
from urllib.request import Request, urlopen

from name_matching import find_best_match, normalize_arena_name


ARENA_URL = "https://lmarena.ai/leaderboard/code/webdev"
SCHEMA_VERSION = 1


def fetch_url(url: str, timeout: int = 30) -> str:
    """Fetch text over HTTP with the standard library."""

    request = Request(url, headers={"User-Agent": "models-mapping/watch-arena"})
    with urlopen(request, timeout=timeout) as response:
        return response.read().decode("utf-8")


def _find_entries_array(html: str) -> list[Any]:
    """Extract the first ``entries`` JSON array from a page.

    Next.js currently serializes the payload inside escaped script strings,
    while a normal JSON response uses an unescaped key.  Support both forms
    and let JSONDecoder handle nested objects and brackets in string values.
    """

    markers = (r'entries\":', '"entries":', "entries:")
    for marker in markers:
        index = html.find(marker)
        if index < 0:
            continue
        start = html.find("[", index + len(marker))
        if start < 0:
            continue
        candidate = html[start:]
        # The escaped form is a JSON array represented inside a JSON string.
        if marker == r'entries\":':
            candidate = candidate.replace('\\"', '"').replace('\\\\', '\\')
        try:
            value, _ = json.JSONDecoder().raw_decode(candidate)
        except json.JSONDecodeError:
            continue
        if isinstance(value, list):
            return value
    raise ValueError("Could not find entries data in Arena page")


def _as_int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _as_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _required_float(value: Any, field: str) -> float:
    try:
        result = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"Arena entry has invalid {field}: {value!r}") from exc
    if not math.isfinite(result):
        raise ValueError(f"Arena entry has non-finite {field}: {value!r}")
    return result


def parse_arena_html(html: str, top_n: int = 0) -> List[dict]:
    """Parse leaderboard entries into normalized records.

    Each record contains the legacy keys used by ``name_matching`` as well as
    stable field names used by the JSON snapshot.
    """

    entries = _find_entries_array(html)
    result: List[dict] = []
    selected_entries = entries if top_n <= 0 else entries[:top_n]
    for entry in selected_entries:
        if not isinstance(entry, Mapping):
            continue
        model_name = str(entry.get("modelDisplayName", entry.get("model_id", "")))
        normalized, effort = normalize_arena_name(model_name)
        if not normalized:
            continue
        rating = round(
            _required_float(entry.get("rating", entry.get("arena_score")), "rating"),
            2,
        )
        rank = _as_int(entry.get("rank", entry.get("arena_rank", 0)))
        context = entry.get("contextLength", entry.get("arena_context"))
        organization = entry.get("modelOrganization", entry.get("organization", ""))
        result.append(
            {
                "model_id": normalized,
                "effort": effort,
                "rank": rank,
                "rating": rating,
                "context": context if context is not None else "-",
                "organization": str(organization or ""),
            }
        )
    return result


def build_arena_lookup(arena_data: List[dict]) -> Dict[str, dict]:
    """Build a normalized lookup, keeping the highest rating per model."""

    lookup: Dict[str, dict] = {}
    for entry in arena_data:
        model_id = entry.get("model_id")
        if not model_id:
            continue
        if model_id not in lookup or entry.get("rating", 0) > lookup[model_id].get("rating", 0):
            lookup[model_id] = entry
    return lookup


def get_confidence(match_type: str) -> str:
    """Return the user-facing confidence for an Arena fallback."""

    if match_type == "direct_match":
        return "high"
    if match_type in ("contributor_suffix", "version_downgrade"):
        return "medium"
    if match_type in ("prefix_match", "free_default"):
        return "low"
    return "none"


def _snapshot_entry(entry: Mapping[str, Any]) -> dict:
    """Convert a parsed entry to the schema field names."""

    return {
        "arena_score": round(_as_float(entry.get("rating", entry.get("arena_score", 0))), 2),
        "arena_rank": _as_int(entry.get("rank", entry.get("arena_rank", 0))),
        "arena_context": entry.get("context", entry.get("arena_context", "-")),
        "organization": entry.get("organization", ""),
        "effort": entry.get("effort"),
    }


def build_arena_snapshot(
    entries: List[Mapping[str, Any]],
    *,
    source_url: str = ARENA_URL,
    fetched_at: Optional[str] = None,
) -> dict:
    """Build a schema-versioned ``arena.json`` object."""

    models: Dict[str, dict] = {}
    for entry in entries:
        model_id = str(entry.get("model_id", "")).strip()
        if not model_id:
            continue
        current = _snapshot_entry(entry)
        previous = models.get(model_id)
        if previous is None or current["arena_score"] > previous["arena_score"]:
            models[model_id] = current

    return {
        "schema_version": SCHEMA_VERSION,
        "source": {
            "url": source_url,
            "fetched_at": fetched_at or datetime.now(timezone.utc).isoformat(),
        },
        "models": dict(sorted(models.items())),
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
    """Keep the daily snapshot byte-stable when leaderboard data is unchanged."""

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
        and old_source.get("fetched_at")
    ):
        new_source["fetched_at"] = old_source["fetched_at"]
    return snapshot


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Fetch and write Arena JSON")
    parser.add_argument(
        "--input",
        type=Path,
        help="Local HTML fixture; omit to fetch Arena",
    )
    parser.add_argument("--url", default=ARENA_URL, help="Arena leaderboard URL")
    parser.add_argument(
        "--top-n",
        type=int,
        default=0,
        help="Number of entries to keep; 0 keeps the complete leaderboard",
    )
    parser.add_argument(
        "--output",
        "-o",
        type=Path,
        default=Path("data/arena.json"),
        help="Output JSON path (default: data/arena.json)",
    )
    args = parser.parse_args(argv)

    try:
        html = args.input.read_text(encoding="utf-8") if args.input else fetch_url(args.url)
        entries = parse_arena_html(html, top_n=args.top_n)
        if not entries:
            raise ValueError("no leaderboard entries found")
        snapshot = build_arena_snapshot(entries, source_url=args.url)
        snapshot = preserve_timestamp_when_unchanged(args.output, snapshot)
        write_json_atomic(args.output, snapshot)
    except (OSError, ValueError) as exc:
        print(f"watch-arena: {exc}", file=sys.stderr)
        return 1

    print(f"watch-arena: wrote {len(snapshot['models'])} models to {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
