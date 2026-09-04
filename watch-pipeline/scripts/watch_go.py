#!/usr/bin/env python3
"""Fetch and normalize the OpenCode Go document.

``watch-go`` is deliberately a source watcher, not a mapping step.  It reads
the OpenCode Go ``go.mdx`` document and merges its quota/retention fields
into ``data/opencode-go-models.json`` in place.  No separate ``go.json``
file is produced; ``opencode-go-models.json`` is the single source of
truth for model + Go extension data.

The parser keeps incomplete rows.  Free models retain the historical
``fix_free`` behaviour: missing quota values are filled from the largest
non-free value and their prices are zero.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile
from pathlib import Path
from typing import Any, Dict, Mapping, Optional
from urllib.request import Request, urlopen

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT / "scripts"))

from parse_opencode_mdx import parse_mdx  # noqa: E402
from error_state import clear_error, dump_page, persist_error  # noqa: E402


GO_MDX_URL = (
    "https://raw.githubusercontent.com/anomalyco/opencode/"
    "dev/packages/web/src/content/docs/go.mdx"
)
GO_COMMIT_API_URL = (
    "https://api.github.com/repos/anomalyco/opencode/commits"
    "?path=packages/web/src/content/docs/go.mdx&sha=dev&per_page=1"
)
DEFAULT_OPENCODE_GO = Path("data/opencode-go-models.json")
CHANNEL = "go"

# Fields injected from go.mdx into each model record. rp5h/usage_quota feed
# candidate scoring; the four price fields calibrate models.dev cost, which
# has drifted (2x variants, missing cache_write). Everything else mdx offers
# has no downstream consumer and is not stored.
GO_EXTRA_FIELDS = (
    "rp5h",
    "usage_quota",
    "price_input",
    "price_output",
    "price_cached_read",
    "price_cached_write",
)


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
        if isinstance(first, Mapping):
            commit = first.get("sha")
            if commit:
                return str(commit)
    return None


def _missing(value: Any) -> bool:
    return value is None or value == ""


def fix_free_models(models: Dict[str, dict]) -> Dict[str, dict]:
    """Fill missing values for free models without changing explicit values."""

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
    """Convert parser values to stable JSON values."""

    if value in ("", "-"):
        return None
    return value


def parse_go_mdx(content: str) -> Dict[str, dict]:
    """Parse ``go.mdx`` into a model-id keyed source mapping."""

    parsed = parse_mdx(content, include_incomplete=True)
    models: Dict[str, dict] = {}
    for key, source in parsed.items():
        model_id = str(source.get("model_id") or key).strip()
        if not model_id:
            continue

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


def merge_into_opencode_go(
    opencode_path: Path,
    go_models: Mapping[str, Mapping[str, Any]],
) -> dict:
    """Merge Go fields into ``opencode-go-models.json`` shape in place.

    Structure: ``{ "opencode-go": { ..., "models": { id: { ... } } } }``.
    For each model that exists in both, inject Go extension fields.
    Models only in Go are ignored (no upstream record to enrich).
    Existing non-Go fields (cost, limit, modalities, etc.) are preserved.
    """

    payload = json.loads(opencode_path.read_text(encoding="utf-8"))
    if not isinstance(payload, Mapping):
        raise ValueError(f"{opencode_path} is not a JSON object")

    provider = payload.get("opencode-go")
    if not isinstance(provider, Mapping):
        raise ValueError(f"{opencode_path} has no opencode-go provider")

    models = provider.get("models")
    if not isinstance(models, Mapping):
        raise ValueError(f"{opencode_path} opencode-go has no models mapping")

    for model_id, go_record in go_models.items():
        base = models.get(model_id)
        if not isinstance(base, Mapping):
            # Go-only model: nothing to enrich, skip
            continue
        # Update in place with Go extension fields
        base_dict = dict(base)
        for field in GO_EXTRA_FIELDS:
            base_dict[field] = _json_value(go_record.get(field))
        models[model_id] = base_dict

    # Clear stale Go fields for models no longer in Go
    for model_id, record in list(models.items()):
        if model_id not in go_models and isinstance(record, Mapping):
            cleaned = dict(record)
            for field in GO_EXTRA_FIELDS:
                cleaned.pop(field, None)
            models[model_id] = cleaned

    return payload


def write_json_atomic(path: Path, payload: Mapping[str, Any]) -> None:
    """Write JSON atomically in the destination directory."""

    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, ensure_ascii=False, indent=2, sort_keys=True)
            handle.write("\n")
        os.replace(temporary, path)
    except Exception:
        try:
            os.unlink(temporary)
        except OSError:
            pass
        raise


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Merge OpenCode Go MDX into opencode-go-models.json")
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
        "--opencode-go",
        type=Path,
        default=DEFAULT_OPENCODE_GO,
        help="Path to data/opencode-go-models.json to enrich in place",
    )
    parser.add_argument(
        "--output",
        "-o",
        type=Path,
        help="Output path (default: same as --opencode-go, enrich in place)",
    )
    args = parser.parse_args(argv)
    output = args.output or args.opencode_go

    try:
        if args.input:
            content = Path(args.input).read_text(encoding="utf-8")
        else:
            content = fetch_url(args.url)
        go_models = parse_go_mdx(content)
        if not go_models:
            raise ValueError("no models found in go.mdx")

        if not args.opencode_go.exists():
            raise ValueError(f"opencode-go snapshot not found: {args.opencode_go}")

        payload = merge_into_opencode_go(args.opencode_go, go_models)
        write_json_atomic(output, payload)
    except (OSError, ValueError) as exc:
        dump = dump_page(CHANNEL, content) if "content" in locals() else None
        persist_error(CHANNEL, "watch_go.py", str(exc), dump)
        print(f"watch-go: {exc}", file=sys.stderr)
        return 1

    clear_error(CHANNEL)
    print(f"watch-go: enriched {len(go_models)} Go records into {output}")
    if args.source_commit:
        print(f"watch-go: source commit {args.source_commit}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
