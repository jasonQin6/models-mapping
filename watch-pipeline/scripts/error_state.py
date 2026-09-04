"""Persistent error-state helpers shared by the watch-pipeline scripts.

On failure a watcher writes ``watch-pipeline/reference/<channel>/last-error.json``
so agents can discover and fix breakage without reading CI logs. The file is
removed on the next successful run, so its existence means "needs fixing".
Dumps of upstream pages (failed-page.html) live in the same channel directory.
"""

from __future__ import annotations

import json
import os
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

REPO_ROOT = Path(__file__).resolve().parents[2]
REFERENCE_ROOT = REPO_ROOT / "watch-pipeline" / "reference"


def error_path(channel: str) -> Path:
    """Return the last-error.json path for a channel."""
    return REFERENCE_ROOT / channel / "last-error.json"


def error_dump_path(channel: str, filename: str = "failed-page.html") -> Path:
    """Return the default dump path for a channel without writing it."""
    return REFERENCE_ROOT / channel / filename


def dump_page(channel: str, content: str, filename: str = "failed-page.html") -> Path:
    """Persist an upstream page for offline inspection; returns the dump path."""
    path = REFERENCE_ROOT / channel / filename
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    return path


def persist_error(
    channel: str,
    script: str,
    error: str,
    dump_path: Optional[Path] = None,
) -> Path:
    """Atomically write last-error.json for a channel; returns the path written."""
    path = error_path(channel)
    payload = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "script": script,
        "error": error,
    }
    if dump_path is not None:
        payload["dump"] = str(dump_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(payload, fh, ensure_ascii=False, indent=2)
            fh.write("\n")
        os.replace(tmp, str(path))
    except Exception:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise
    return path


def clear_error(channel: str) -> None:
    """Remove last-error.json for a channel if present (marks success)."""
    try:
        error_path(channel).unlink()
    except FileNotFoundError:
        pass
