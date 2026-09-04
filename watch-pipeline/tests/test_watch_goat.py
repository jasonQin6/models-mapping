#!/usr/bin/env python3
"""Tests for the GOAT JSON watcher."""

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "scripts"))

from watch_goat import (
    build_goat_fields,
    col_index,
    load_upstream_lookup,
    main,
    parse_price,
    parse_tables,
    to_model_id,
)

FIXTURE = Path(__file__).parent / "fixtures" / "goat-sample.html"


def _all_models_doc() -> dict:
    return {
        "qwen/qwen3.30b": {"id": "qwen/qwen3.30b", "name": "Qwen3 30B", "family": "qwen"},
        "qwen/qwen3.32b": {"id": "qwen/qwen3.32b", "name": "Qwen3 32B", "family": "qwen"},
    }


def test_parse_tables_finds_three_tables() -> None:
    tables = parse_tables(FIXTURE.read_text(encoding="utf-8"))

    assert len(tables) == 3
    assert col_index(tables[0][0], {"model"}) == 0
    assert col_index(tables[0][0], {"intelligence"}) == 1
    assert col_index(tables[1][0], {"requests / 5 hours"}) == 1
    assert col_index(tables[2][0], {"monthly credits"}) == 2


def test_to_model_id_restores_minor_version() -> None:
    assert to_model_id("qwen3-30b") == "qwen3.30b"
    assert to_model_id("deepseek-v4-flash-fast") == "deepseek-v4-flash-fast"


def test_parse_price_covers_dash_free_and_suffix() -> None:
    assert parse_price("—") is None
    assert parse_price("free") == 0.0
    assert parse_price("$0.10+5") == 0.10


def test_build_goat_fields_splits_channel_and_extra() -> None:
    patch, extra = build_goat_fields(
        "Qwen3 30B",
        "qwen3-30b",
        price_input_raw="$0.20",
        price_output_raw="$1.20",
        cache_read_raw="$0.05",
        cache_write_raw=None,
        rp5h_val=1000,
        usage_quota_val=5.0,
        tok_s_raw="120",
    )

    assert patch == {
        "id": "qwen3.30b",
        "name": "Qwen3 30B",
        "cost": {"input": 0.2, "output": 1.2, "cache_read": 0.05},
    }
    assert extra == {
        "rp5h": 1000,
        "usage_quota": 5.0,
        "tok_s": 120,
    }


def test_main_html_to_tmp_drops_variant_without_base(tmp_path: Path) -> None:
    all_models = tmp_path / "all.json"
    all_models.write_text(json.dumps(_all_models_doc()), encoding="utf-8")
    out = tmp_path / "out.json"

    assert (
        main(
            [
                "--html",
                str(FIXTURE),
                "--output",
                str(out),
                "--all-models",
                str(all_models),
                "--dump-html",
                str(tmp_path / "failed.html"),
            ]
        )
        == 0
    )

    payload = json.loads(out.read_text(encoding="utf-8"))
    models = payload["commandcode-goat"]["models"]
    assert set(models) == {"qwen3.30b", "qwen3.32b"}
    assert models["qwen3.30b"]["extra"]["rp5h"] == 1000
    assert models["qwen3.30b"]["extra"]["usage_quota"] == 5.0
    assert not list(tmp_path.glob(".out.json.*.tmp"))


def test_main_missing_tables_returns_one(tmp_path: Path) -> None:
    bad = tmp_path / "bad.html"
    bad.write_text("<html><body><table><tr><th>Other</th></tr></table></body></html>", encoding="utf-8")
    all_models = tmp_path / "all.json"
    all_models.write_text("{}", encoding="utf-8")

    assert (
        main(
            [
                "--html",
                str(bad),
                "--output",
                str(tmp_path / "out.json"),
                "--all-models",
                str(all_models),
                "--dump-html",
                str(tmp_path / "failed.html"),
            ]
        )
        == 1
    )
    assert (tmp_path / "failed.html").exists()


def test_load_upstream_lookup_indexes_bare_id(tmp_path: Path) -> None:
    all_models = tmp_path / "all.json"
    all_models.write_text(json.dumps(_all_models_doc()), encoding="utf-8")

    lookup = load_upstream_lookup(all_models)

    assert lookup["qwen3.30b"]["family"] == "qwen"
