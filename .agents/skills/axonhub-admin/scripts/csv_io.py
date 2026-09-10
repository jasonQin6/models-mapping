#!/usr/bin/env python3
"""Atomic I/O for the generated five-column mapping workspace."""

import csv
import os
import tempfile
from pathlib import Path
from typing import Iterable, Mapping


MAPPING_COLUMNS = ["model_id", "role", "arena_score", "rp5h", "mapping"]


def read_mapping(path: Path) -> list[dict[str, str]]:
    """Read a mapping workspace and require its exact schema."""

    with path.open(encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle)
        if reader.fieldnames != MAPPING_COLUMNS:
            raise ValueError(
                f"mapping CSV columns must be {MAPPING_COLUMNS}, got {reader.fieldnames}"
            )
        return list(reader)


def write_mapping(path: Path, rows: Iterable[Mapping[str, object]]) -> None:
    """Atomically write the canonical mapping workspace."""

    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(
        prefix=f".{path.name}.", suffix=".tmp", dir=path.parent
    )
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="") as handle:
            writer = csv.DictWriter(
                handle,
                fieldnames=MAPPING_COLUMNS,
                extrasaction="ignore",
                lineterminator="\n",
            )
            writer.writeheader()
            writer.writerows(rows)
        os.replace(temporary, path)
    except Exception:
        try:
            os.unlink(temporary)
        except OSError:
            pass
        raise
