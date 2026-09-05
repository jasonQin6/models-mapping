#!/usr/bin/env python3
"""Tests for the generated mapping CSV contract."""

import csv
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "scripts"))

from csv_io import MAPPING_COLUMNS, read_mapping, write_mapping


def test_mapping_columns_are_exact() -> None:
    assert MAPPING_COLUMNS == [
        "model_id",
        "role",
        "arena_score",
        "rp5h",
        "mapping",
    ]


def test_write_and_read_mapping(tmp_path: Path) -> None:
    path = tmp_path / "models.csv"
    write_mapping(
        path,
        [
            {
                "model_id": "candidate-a",
                "role": "candidate",
                "arena_score": 1500,
                "rp5h": 100,
                "mapping": "",
                "ignored": "value",
            },
            {
                "model_id": "gpt-request",
                "role": "request",
                "arena_score": 1510,
                "rp5h": "",
                "mapping": "candidate-a",
            },
        ],
    )

    assert read_mapping(path) == [
        {
            "model_id": "candidate-a",
            "role": "candidate",
            "arena_score": "1500",
            "rp5h": "100",
            "mapping": "",
        },
        {
            "model_id": "gpt-request",
            "role": "request",
            "arena_score": "1510",
            "rp5h": "",
            "mapping": "candidate-a",
        },
    ]
    assert not list(tmp_path.glob("*.tmp"))


def test_read_rejects_legacy_schema(tmp_path: Path) -> None:
    path = tmp_path / "models.csv"
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(["model_id", "provider", "arena_score"])
        writer.writerow(["candidate-a", "opencode", "1500"])

    with pytest.raises(ValueError, match="mapping CSV columns"):
        read_mapping(path)
