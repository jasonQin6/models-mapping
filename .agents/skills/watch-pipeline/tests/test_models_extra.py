#!/usr/bin/env python3
"""Tests for the shared models_extra store (field-level channel merges)."""

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "scripts"))

from models_extra import load_document, update_channel  # noqa: E402


def _write_store(tmp_path: Path, channels: dict) -> Path:
    path = tmp_path / "models_extra.json"
    path.write_text(
        json.dumps(
            {"schema_version": 1, "updated_at": None, "channels": channels},
            sort_keys=True,
        ),
        encoding="utf-8",
    )
    return path


def test_update_channel_refreshes_contract_fields_and_keeps_hand_fields(
    tmp_path: Path,
) -> None:
    path = _write_store(
        tmp_path,
        {
            "opencode-go": {
                "glm-5.2": {"name": "GLM-5.2", "rp5h": 1, "exclude": "manual reason"}
            }
        },
    )

    update_channel(path, "opencode-go", {"glm-5.2": {"name": "GLM-5.2", "rp5h": 2}})

    record = load_document(path)["channels"]["opencode-go"]["glm-5.2"]
    assert record == {"name": "GLM-5.2", "rp5h": 2, "exclude": "manual reason"}


def test_update_channel_inserts_new_and_drops_vanished_ids(tmp_path: Path) -> None:
    path = _write_store(
        tmp_path,
        {
            "opencode-go": {
                "kept": {"name": "Kept"},
                "gone": {"name": "Gone", "exclude": "hand"},
            }
        },
    )

    update_channel(path, "opencode-go", {"kept": {"name": "Kept"}, "new": {"name": "New"}})

    section = load_document(path)["channels"]["opencode-go"]
    assert set(section) == {"kept", "new"}
    assert section["new"] == {"name": "New"}


def test_update_channel_leaves_other_sections_alone(tmp_path: Path) -> None:
    path = _write_store(
        tmp_path,
        {
            "opencode-go": {"a": {"name": "A"}},
            "sensenova": {"b": {"name": "B", "exclude": "hand"}},
        },
    )

    update_channel(path, "opencode-go", {"a": {"name": "A2"}})

    document = load_document(path)
    assert document["channels"]["sensenova"] == {"b": {"name": "B", "exclude": "hand"}}
    assert document["channels"]["opencode-go"]["a"] == {"name": "A2"}


def test_update_channel_creates_section_and_document(tmp_path: Path) -> None:
    path = tmp_path / "models_extra.json"

    update_channel(path, "opencode-go", {"a": {"name": "A"}})

    document = load_document(path)
    assert document["schema_version"] == 1
    assert document["channels"]["opencode-go"] == {"a": {"name": "A"}}
    assert document["updated_at"]
    assert not list(tmp_path.glob(".models_extra.json.*"))


def test_update_channel_returns_newly_inserted_ids(tmp_path: Path) -> None:
    path = _write_store(tmp_path, {"opencode-go": {"kept": {"name": "Kept"}}})

    new_ids = update_channel(
        path, "opencode-go", {"kept": {"name": "Kept"}, "new": {"name": "New"}}
    )

    assert new_ids == ["new"]
