#!/usr/bin/env python3
"""Fetch the OpenCode Go document and build ``data/opencode-go-models.json``.

``watch-go`` is the source watcher for the opencode-go channel.  The
``go.mdx`` document is the channel's model-list fact source: the snapshot's
``models`` keys are exactly the go.mdx model ids, and an id that leaves the
document leaves the snapshot (ADR 0011).  models.dev is a card-data filler
only: fields a go.mdx record does not carry (description, cost, limit,
modalities, ...) are filled from the models.dev ``opencode-go`` provider
when available, and go-exclusive ids are kept with go.mdx-claimed fields
only — the snapshot push precedent of ADR 0010.

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
# candidate scoring and remarks; the four price fields calibrate models.dev
# cost, which has drifted (2x variants, missing cache_write). Everything
# else mdx offers has no downstream consumer and is not stored.
GO_EXTRA_FIELDS = (
    "rp5h",
    "usage_quota",
    "price_input",
    "price_output",
    "price_cached_read",
    "price_cached_write",
)

# Remark mirror nested under ``extra`` in goat shape; sync_models.py reads
# extra first, so this must stay in lockstep with the top-level values.
REMARK_EXTRA_FIELDS = ("rp5h", "usage_quota")

# Card fields filled from models.dev when a go.mdx record has no value for
# them. go.mdx owns the quota/price fields; models.dev never overwrites.
CARD_FILL_FIELDS = (
    "name",
    "description",
    "family",
    "attachment",
    "reasoning",
    "reasoning_options",
    "tool_call",
    "structured_output",
    "temperature",
    "interleaved",
    "knowledge",
    "release_date",
    "last_updated",
    "modalities",
    "open_weights",
    "limit",
    "cost",
    "status",
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


def load_models_dev_provider(path: Optional[Path]) -> Dict[str, dict]:
    """Load the models.dev card source into a model-id keyed mapping.

    Accepts the full models.dev ``api.json``, a ``{"opencode-go": ...}``
    extract, or a bare provider node with a ``models`` mapping.  ``None``
    means no filler is available and every model keeps go.mdx-claimed
    fields only.
    """

    if path is None:
        return {}
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, Mapping):
        raise ValueError(f"{path} is not a JSON object")
    node = payload.get("opencode-go")
    if not isinstance(node, Mapping):
        if isinstance(payload.get("models"), Mapping):
            node = payload
        else:
            raise ValueError(f"{path} has no opencode-go provider")
    raw = node.get("models")
    if not isinstance(raw, Mapping):
        raise ValueError(f"{path} opencode-go provider has no models mapping")
    return {str(key): dict(value) for key, value in raw.items() if isinstance(value, Mapping)}


def build_opencode_go_snapshot(
    go_models: Mapping[str, Mapping[str, Any]],
    models_dev: Optional[Mapping[str, Mapping[str, Any]]] = None,
    previous: Optional[Mapping[str, Any]] = None,
) -> tuple[dict, int]:
    """Build the snapshot payload with go.mdx as the model-list authority.

    Models are exactly the go.mdx ids; ids only known to models.dev are
    never added.  Card fields come from models.dev when a record exists
    there, otherwise the model keeps go.mdx-claimed fields (id, name,
    quota/price).  Returns the payload and the number of models.dev-enriched
    records.
    """

    models_dev = models_dev or {}
    models: Dict[str, dict] = {}
    enriched = 0
    for model_id, go_record in go_models.items():
        record: Dict[str, Any] = {"id": model_id}
        dev = models_dev.get(model_id)
        if dev:
            enriched += 1
            for field in CARD_FILL_FIELDS:
                value = dev.get(field)
                if value is not None:
                    record[field] = value
        if "name" not in record:
            name = _json_value(go_record.get("name"))
            if name:
                record["name"] = name
        for field in GO_EXTRA_FIELDS:
            record[field] = _json_value(go_record.get(field))
        record["extra"] = {
            field: _json_value(go_record.get(field)) for field in REMARK_EXTRA_FIELDS
        }
        models[model_id] = record

    provider: Dict[str, Any] = {}
    previous_provider = (previous or {}).get("opencode-go")
    if isinstance(previous_provider, Mapping):
        provider.update(
            {key: value for key, value in previous_provider.items() if key != "models"}
        )
    provider.setdefault("id", "opencode-go")
    provider["models"] = models
    return {"opencode-go": provider}, enriched


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
    parser = argparse.ArgumentParser(
        description="Build opencode-go-models.json from go.mdx, filling card data from models.dev"
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
        "--models-dev",
        type=Path,
        default=None,
        help=(
            "models.dev card-data filler: full api.json, a "
            '{"opencode-go": ...} extract, or a bare provider node; '
            "omit to keep go.mdx-claimed fields only"
        ),
    )
    parser.add_argument(
        "--opencode-go",
        type=Path,
        default=DEFAULT_OPENCODE_GO,
        help="Path to data/opencode-go-models.json; an existing file supplies the provider envelope",
    )
    parser.add_argument(
        "--output",
        "-o",
        type=Path,
        help="Output path (default: same as --opencode-go)",
    )
    args = parser.parse_args(argv)
    output = args.output or args.opencode_go

    content = ""
    try:
        if args.input:
            content = Path(args.input).read_text(encoding="utf-8")
        else:
            content = fetch_url(args.url)
        go_models = parse_go_mdx(content)
        if not go_models:
            raise ValueError("no models found in go.mdx")

        models_dev = load_models_dev_provider(args.models_dev)
        previous = None
        if args.opencode_go.exists():
            previous = json.loads(args.opencode_go.read_text(encoding="utf-8"))
            if not isinstance(previous, Mapping):
                raise ValueError(f"{args.opencode_go} is not a JSON object")

        payload, enriched = build_opencode_go_snapshot(go_models, models_dev, previous)
        write_json_atomic(output, payload)
    except (OSError, ValueError) as exc:
        dump = dump_page(CHANNEL, content) if "content" in locals() else None
        persist_error(CHANNEL, "watch_go.py", str(exc), dump)
        print(f"watch-go: {exc}", file=sys.stderr)
        return 1

    clear_error(CHANNEL)
    print(
        f"watch-go: built {len(payload['opencode-go']['models'])} Go models "
        f"({enriched} enriched from models.dev) -> {output}"
    )
    if args.source_commit:
        print(f"watch-go: source commit {args.source_commit}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
