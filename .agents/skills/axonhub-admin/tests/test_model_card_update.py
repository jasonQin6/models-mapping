#!/usr/bin/env python3
"""Tests for the model-card-update step: target state + write-time payload."""

import json
from pathlib import Path

from model_card_update import assemble, build_target_state, main  # noqa: E402
from testutil import build, rec  # noqa: E402


def _target(sections, cards=None, arena=None, aliases=None, blocklist=None):
    result = build(sections, arena=arena, aliases=aliases, blocklist=blocklist)
    warnings: list[dict] = []
    models = build_target_state(result, cards or {}, {key: key for key in (cards or {})}, warnings)
    return models, result["warnings"] + warnings


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


def test_channel_cost_wins_over_card_cost() -> None:
    cards = {"deepseek-v4-flash": {"name": "DeepSeek V4 Flash", "cost": {"input": 0.1, "output": 0.5}, "reasoning": True, "limit": {"context": 128000, "output": 8192}}}
    sections = {"commandcode-goat": {"deepseek-v4-flash": rec(rp5h=18200, quota=60, cost={"input": 0.22, "output": 0.66})}}

    models, _warnings = _target(sections, cards=cards)

    entry = models[0]
    assert entry["input"]["cost"] == {"input": 0.22, "output": 0.66, "cacheRead": 0, "cacheWrite": 0}
    assert entry["cardRef"] == "deepseek-v4-flash"


def test_missing_card_warns_and_falls_back_to_channel_name() -> None:
    sections = {"commandcode-goat": {"goat-only-model": rec(rp5h=100, name="Goat Only Model")}}

    models, warnings = _target(sections)

    assert any(w["type"] == "card_missing" and w["model"] == "goat-only-model" for w in warnings)
    assert models[0]["input"]["name"] == "Goat Only Model"
    assert models[0]["cardRef"] is None


def test_free_record_zeroes_silent_cost_fields() -> None:
    # `free: true` declares every price zero: card list prices may not leak
    # into cost fields the channel leaves silent; a non-free control keeps
    # the card values.
    sections = {
        "ant": {
            "free-a": rec(rp5h=None, quota=None, cost={"input": 0, "output": 0}),
            "paid": rec(rp5h=500, cost={}),
        },
    }
    card = {"name": "Same Card", "cost": {"input": 2, "output": 8, "cache_read": 0.2, "cache_write": 0.8}}

    models, _warnings = _target(sections, cards={"free-a": card, "paid": dict(card)}, arena={})

    by_id = {m["modelID"]: m for m in models}
    assert by_id["free-a"]["input"]["cost"] == {
        "input": 0,
        "output": 0,
        "cacheRead": 0,
        "cacheWrite": 0,
    }
    assert by_id["paid"]["input"]["cost"] == {
        "input": 2,
        "output": 8,
        "cacheRead": 0.2,
        "cacheWrite": 0.8,
    }


def test_zero_declared_cost_renders_zero_card_and_filled_remark() -> None:
    # Collection transcribed the channel's freeness wording to zero prices;
    # pricing zeroes and the remark carries the derived defaults.
    sections = {
        "commandcode-goat": {
            "longcat-2.0-free": rec(rp5h=None, quota=None, cost={"input": 0, "output": 0}),
        },
    }

    models, warnings = _target(sections)

    entry = models[0]
    assert entry["input"]["cost"] == {"input": 0, "output": 0, "cacheRead": 0, "cacheWrite": 0}
    assert entry["input"]["remark"] == '{"manual":"","rp5h":1000,"usage_quota":60}'
def test_assemble_renders_card_from_reference_and_keeps_target_cost() -> None:
    assembled = assemble(_entry(), _CARDS)

    assert assembled["modelID"] == "glm-5.3"
    assert assembled["name"] == "GLM-5.3"
    card = assembled["modelCard"]
    # Descriptive fields come from the referenced all_models card.
    assert card["reasoning"] == {"supported": True, "default": True}
    assert card["toolCall"] is True
    assert card["limit"] == {"context": 1000000, "output": 131072}
    assert card["releaseDate"] == "2026-08-14"
    # The card's list price never leaks: the target-state merged cost wins.
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
    assert card["cost"]["input"] == 1.4  # target cost survives


def _write_inputs(tmp_path: Path) -> tuple[Path, Path]:
    extra = tmp_path / "models_extra.json"
    extra.write_text(json.dumps({
        "schema_version": 1,
        "channels": {"commandcode-goat": {
            "glm-5.3": rec(rp5h=271, quota=20, cost={"input": 1.4, "output": 4.4}),
        }},
    }), encoding="utf-8")
    arena = tmp_path / "arena.json"
    arena.write_text(json.dumps({"schema_version": 1, "models": {"glm-5.3": {"arena_score": 1612.5}}}), encoding="utf-8")
    return extra, arena


def test_main_prints_target_state(tmp_path: Path, capsys) -> None:
    extra, arena = _write_inputs(tmp_path)
    cards = tmp_path / "all_models.json"
    cards.write_text(json.dumps(_CARDS), encoding="utf-8")

    rc = main(["--extra", str(extra), "--cards", str(cards), "--arena", str(arena)])

    assert rc == 0
    models = json.loads(capsys.readouterr().out)
    assert len(models) == 1
    assert models[0]["modelID"] == "glm-5.3"
    assert models[0]["cardRef"] == "zhipuai/glm-5.3"
    assert "modelCard" not in models[0]["input"]


def test_main_id_renders_full_payload(tmp_path: Path, capsys) -> None:
    extra, arena = _write_inputs(tmp_path)
    cards = tmp_path / "all_models.json"
    cards.write_text(json.dumps(_CARDS), encoding="utf-8")

    rc = main(["--extra", str(extra), "--cards", str(cards), "--arena", str(arena), "--id", "glm-5.3"])

    assert rc == 0
    assembled = json.loads(capsys.readouterr().out)
    assert assembled["modelID"] == "glm-5.3"
    assert assembled["modelCard"]["reasoning"] == {"supported": True, "default": True}
    assert assembled["modelCard"]["cost"]["input"] == 1.4


def test_main_unknown_id_fails(tmp_path: Path, capsys) -> None:
    extra, arena = _write_inputs(tmp_path)
    cards = tmp_path / "all_models.json"
    cards.write_text(json.dumps(_CARDS), encoding="utf-8")

    rc = main(["--extra", str(extra), "--cards", str(cards), "--arena", str(arena), "--id", "missing"])

    assert rc == 1
