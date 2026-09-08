#!/usr/bin/env python3
"""Fetch the OpenCode Go document and update ``data/models_extra.json``.

``watch-go`` is the source watcher for the opencode-go channel.  The
``go.mdx`` document is the channel's model-list fact source: the channel
section's keys are exactly the go.mdx model ids, and an id that leaves the
document leaves the section (ADR 0011).  The section carries channel-declared
facts only (quotas, prices, remark thresholds); public card data is filled at
planning time from ``data/all_models.json``, never here.

Channel-provided ``claude-*`` models are not collected (ADR 0012).  Free
models keep the parser's zero prices; quota backfill is re-derived at
planning time from the model's owning channel so the rule lives in one place.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Dict, Optional
from urllib.request import Request, urlopen

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT / "scripts"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from parse_opencode_mdx import parse_mdx  # noqa: E402
from error_state import clear_error, dump_page, persist_error  # noqa: E402
from models_extra import DEFAULT_EXTRA_PATH, is_excluded_model, update_channel  # noqa: E402


GO_MDX_URL = (
    "https://raw.githubusercontent.com/anomalyco/opencode/"
    "dev/packages/web/src/content/docs/go.mdx"
)
GO_COMMIT_API_URL = (
    "https://api.github.com/repos/anomalyco/opencode/commits"
    "?path=packages/web/src/content/docs/go.mdx&sha=dev&per_page=1"
)
CHANNEL = "go"


def fetch_url(url: str, timeout: int = 30) -> str:
    """Fetch text over HTTP using only the Python standard library."""

    request = Request(url, headers={"User-Agent": "models-mapping/watch-go"})
    with urlopen(request, timeout=timeout) as response:
        return response.read().decode("utf-8")


def fetch_latest_commit(url: str = GO_COMMIT_API_URL) -> Optional[str]:
    """Return the latest source commit, or ``None`` when unavailable."""

    try:
        payload = json.loads(fetch_url(url))
    except (OSError, ValueError):
        return None

    if isinstance(payload, list) and payload:
        first = payload[0]
        if isinstance(first, dict):
            commit = first.get("sha")
            if commit:
                return str(commit)
    return None


def _json_value(value: Any) -> Any:
    """Convert parser placeholder values to stable JSON values."""

    if value in ("", "-"):
        return None
    return value


def build_go_section(content: str) -> Dict[str, dict]:
    """Parse go.mdx into the opencode-go section of models_extra.json.

    Keys are exactly the go.mdx model ids (claude-* dropped); values carry
    channel-declared facts only: display name, quotas, the four prices
    assembled into ``cost`` (``cache_*`` keys align with models.dev and the
    goat section), and the remark thresholds the document states.
    """

    parsed = parse_mdx(content, include_incomplete=True)
    models: Dict[str, dict] = {}
    for key, source in parsed.items():
        model_id = str(source.get("model_id") or key).strip()
        if not model_id or is_excluded_model(model_id):
            continue
        models[model_id] = {
            "name": _json_value(source.get("name")) or model_id,
            "rp5h": _json_value(source.get("rp5h")),
            "usage_quota": _json_value(source.get("usage_quota")),
            "cost": {
                "input": _json_value(source.get("price_input")),
                "output": _json_value(source.get("price_output")),
                "cache_read": _json_value(source.get("price_cached_read")),
                "cache_write": _json_value(source.get("price_cached_write")),
            },
            "context_threshold": _json_value(source.get("context_threshold")),
            "peak_hours": _json_value(source.get("peak_hours")),
            "retention": _json_value(source.get("retention")),
        }
    return models


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        description="Update the opencode-go section of data/models_extra.json from go.mdx"
    )
    parser.add_argument(
        "input",
        nargs="?",
        help="Local go.mdx file; omit to fetch the upstream document",
    )
    parser.add_argument("--url", default=GO_MDX_URL, help="go.mdx source URL")
    parser.add_argument(
        "--source-commit",
        help="Source commit (stored nowhere; for logging only)",
    )
    parser.add_argument(
        "--extra",
        type=Path,
        default=DEFAULT_EXTRA_PATH,
        help="Path to data/models_extra.json (default: data/models_extra.json)",
    )
    args = parser.parse_args(argv)

    content = ""
    try:
        if args.input:
            content = Path(args.input).read_text(encoding="utf-8")
        else:
            content = fetch_url(args.url)
        models = build_go_section(content)
        if not models:
            raise ValueError("no models found in go.mdx")
        update_channel(args.extra, "opencode-go", models)
    except (OSError, ValueError) as exc:
        dump = dump_page(CHANNEL, content) if "content" in locals() else None
        persist_error(CHANNEL, "watch_go.py", str(exc), dump)
        print(f"watch-go: {exc}", file=sys.stderr)
        return 1

    clear_error(CHANNEL)
    print(f"watch-go: {len(models)} Go models -> {args.extra}")
    if args.source_commit:
        print(f"watch-go: source commit {args.source_commit}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
