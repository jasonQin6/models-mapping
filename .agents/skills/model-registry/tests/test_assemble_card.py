#!/usr/bin/env python3
"""Tests for the write-time card assembly (incremental plan -> AxonHub input)."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "scripts"))

from assemble_card import assemble, main  # noqa: E402


def _entry(model_id="glm-5.3", card_ref="zhipuai/glm-5.3", cost=None, **input_kw):
    payload = {
        "modelID": model_id,
        "channel": "commandcode-goat",
        "cardRef": card_ref,
        "input": {
            "name": "GLM-5.3",
            "developer": "zai",
            "type": "chat",
            "icon": "ChatGLM",
            "group": "glm",
            "cost": cost if cost is not None else {"input": 1.4, "output": 4.4, "cacheRead": 0.26, "cacheWrite": 0},
            "remark": '{"manual":"","rp5h":271,"usage_quota":20.0}',
        },
    }
    payload["input"].update(input_kw)
    return payload


_CARDS = {
    "zhipuai/glm-5.3": {
        "reasoning": True,
        "tool_call": True,
        "temperature": True,
        "modalities": {"input": ["text"], "output": ["text"]},
        "limit": {"context": 1000000, "output": 131072},
        "release_date": "2026-08-14",
        "last_updated": "2026-08-14",
        "cost": {"input": 99, "output": 99},
    }
}


def test_assemble_renders_card_from_reference_and_keeps_plan_cost() -> None:
    assembled = assemble(_entry(), _CARDS)

    assert assembled["modelID"] == "glm-5.3"
    assert assembled["name"] == "GLM-5.3"
    card = assembled["modelCard"]
    # Descriptive fields come from the referenced all_models card.
    assert card["reasoning"] == {"supported": True, "default": True}
    assert card["toolCall"] is True
    assert card["limit"] == {"context": 1000000, "output": 131072}
    assert card["releaseDate"] == "2026-08-14"
    # The card's list price never leaks: the plan's merged cost wins.
    assert card["cost"] == {"input": 1.4, "output": 4.4, "cacheRead": 0.26, "cacheWrite": 0}
    assert assembled["remark"] == '{"manual":"","rp5h":271,"usage_quota":20.0}'


def test_assemble_cardless_entry_uses_default_card() -> None:
    assembled = assemble(_entry(card_ref=None), {})

    card = assembled["modelCard"]
    assert card["reasoning"] == {"supported": False, "default": False}
    assert card["toolCall"] is False
    assert card["temperature"] is True
    assert card["modalities"] == {"input": ["text"], "output": ["text"]}
    assert card["vision"] is False
    assert card["limit"] == {"context": 0, "output": 0}
    assert card["cost"]["input"] == 1.4  # plan cost survives


def test_main_prints_assembled_input(tmp_path: Path) -> None:
    import json

    model_plan = tmp_path / "model-plan.json"
    model_plan.write_text(
        json.dumps({"schema_version": 1, "models": [_entry()], "warnings": [], "report": {"counts": {}}}),
        encoding="utf-8",
    )
    cards = tmp_path / "all_models.json"
    cards.write_text(json.dumps(_CARDS), encoding="utf-8")

    assert main(["--model-plan", str(model_plan), "--cards", str(cards), "--id", "glm-5.3"]) == 0


def test_main_unknown_id_fails(tmp_path: Path) -> None:
    import json

    model_plan = tmp_path / "model-plan.json"
    model_plan.write_text(
        json.dumps({"schema_version": 1, "models": [], "warnings": [], "report": {"counts": {}}}),
        encoding="utf-8",
    )

    assert main(["--model-plan", str(model_plan), "--cards", str(tmp_path / "c.json"), "--id", "x"]) == 1
