#!/usr/bin/env python3
"""Shared read-modify-write store for ``data/models_extra.json``.

The file merges channel-declared model facts (quotas, channel prices) from
every collector: each watcher owns exactly one
``channels`` section and updates only that section's contract fields, so
writers must be serialized (the CI graph orders the goat job after the go
job).  A record field outside the channel's contract belongs to the human
maintainers: merges refresh contract fields and leave every other field
untouched.  Public card data is not stored here — axonhub-admin's offline
scripts fill it from ``data/all_models.json``.
"""

from __future__ import annotations

import json
import os
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping

SCHEMA_VERSION = 1
DEFAULT_EXTRA_PATH = Path("data/models_extra.json")

# Channel-provided claude models are out of scope everywhere: Claude requests
# are served by self-built AxonHub models mapped by arena score, never by a
# channel's own claude list (ADR 0012).
EXCLUDED_ID_PREFIXES = ("claude",)


class ExtraStoreError(ValueError):
    """Raised when the store document has an unsupported shape."""


def is_excluded_model(model_id: str) -> bool:
    """Return True for channel claude models this pipeline must not collect."""

    return model_id.strip().lower().startswith(EXCLUDED_ID_PREFIXES)


def load_document(path: Path) -> dict[str, Any]:
    """Load the store document, tolerating a missing file; reject unknown shapes."""

    if not path.exists():
        return {"schema_version": SCHEMA_VERSION, "updated_at": None, "channels": {}}
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, Mapping):
        raise ExtraStoreError(f"{path} is not a JSON object")
    version = payload.get("schema_version")
    if version is not None and version != SCHEMA_VERSION:
        raise ExtraStoreError(f"{path} has unsupported schema_version {version!r}")
    channels = payload.get("channels", {})
    if not isinstance(channels, Mapping):
        raise ExtraStoreError(f"{path} channels must be an object")
    # Preserve hand-maintained keys (e.g. "aliases"): collectors rewrite
    # only their own channel section.
    document = dict(payload)
    document["schema_version"] = SCHEMA_VERSION
    document["channels"] = dict(channels)
    return document


def update_channel(
    path: Path,
    channel: str,
    models: Mapping[str, Mapping[str, Any]],
) -> None:
    """Merge one channel section atomically, leaving other sections alone.

    Scraped records carry the channel's contract fields only.  A model the
    channel still declares keeps its record's hand-maintained fields (any
    key the scrape does not produce) while its contract fields refresh; a
    model that leaves the channel declaration leaves the section with its
    whole record — which is why hand-maintained model decisions live in
    axonhub-admin's ``data/blocklist.json``, not on records.
    """

    document = load_document(path)
    section = document["channels"].get(channel, {})
    merged: dict[str, dict[str, Any]] = {}
    for model_id, record in sorted(models.items()):
        old = section.get(model_id)
        merged[model_id] = (
            {**old, **dict(record)} if isinstance(old, Mapping) else dict(record)
        )
    document["channels"][channel] = merged
    document["updated_at"] = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    write_json_atomic(path, document)


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
