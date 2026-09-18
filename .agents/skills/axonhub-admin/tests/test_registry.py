#!/usr/bin/env python3
"""Tests for the model registry construction (axonhub-admin)."""

from registry import dedupe_registry, is_free_model  # noqa: E402
from testutil import arena_models, build, rec  # noqa: E402


def test_dedupe_keeps_highest_rp5h_channel_and_reports() -> None:
    warnings: list[dict] = []
    registry = dedupe_registry(
        {
            "opencode-go": {"deepseek-v4-flash": rec(rp5h=7600)},
            "commandcode-goat": {"deepseek-v4-flash": rec(rp5h=18200)},
        },
        {},
        warnings,
        {},
    )

    entry = registry["deepseek-v4-flash"]
    assert entry["channel"] == "commandcode-goat"
    assert entry["record"]["rp5h"] == 18200
    assert entry["channel_aliases"] == {}
    assert warnings == [
        {"type": "duplicate_model_across_sources", "model": "deepseek-v4-flash",
         "kept": "commandcode-goat", "dropped": "opencode-go"}
    ]


def test_dedupe_null_loses_to_value_and_ties_keep_first_channel() -> None:
    warnings: list[dict] = []
    registry = dedupe_registry(
        {
            "opencode-go": {"minimax-m2.5": rec(rp5h=None), "solo": rec(rp5h=None)},
            "commandcode-goat": {"minimax-m2.5": rec(rp5h=100), "solo": rec(rp5h=None)},
        },
        {},
        warnings,
        {},
    )

    assert registry["minimax-m2.5"]["channel"] == "commandcode-goat"
    # Both null: the alphabetically first channel keeps the model.
    assert registry["solo"]["channel"] == "commandcode-goat"


def test_registry_moves_model_between_channels() -> None:
    sections = {
        "opencode-go": {"deepseek-v4-flash": rec(rp5h=7600)},
        "commandcode-goat": {"deepseek-v4-flash": rec(rp5h=18200), "kimi-k2.5": rec(rp5h=900)},
    }

    result = build(sections)

    # Dedupe moved the model to the highest-rp5h channel.
    assert result["registry"]["deepseek-v4-flash"]["channel"] == "commandcode-goat"
    assert set(result["registry"]) == {"deepseek-v4-flash", "kimi-k2.5"}


def test_free_fill_recomputes_from_owning_channel() -> None:
    # The free model belongs to the goat channel, so its quotas must derive
    # from goat's non-free max (500), not from any other channel's values.
    sections = {
        "commandcode-goat": {"freebie": rec(rp5h=None, quota=None, cost={"input": 0, "output": 0}), "paid": rec(rp5h=500)},
    }

    result = build(sections, arena={"paid": 1500.0})

    record = result["registry"]["freebie"]["record"]
    assert record["rp5h"] == 500
    assert record["usage_quota"] == 60
    assert any(w["type"] == "free_default_filled" and w["provider"] == "commandcode-goat" for w in result["warnings"])


def test_free_declaration_and_rp5h_fallback() -> None:
    # Freeness comes from the record flag, not the id; a channel with no
    # non-free rp5h basis fills the free pool with the 1000 default.
    sections = {
        "ant": {
            "ling-3.0-flash": rec(rp5h=None, quota=None, cost={"input": 0, "output": 0}),
            "qwen3.8-flash": rec(rp5h=None),
        },
    }

    result = build(sections, arena={"ling-3.0-flash": 1458.0})

    record = result["registry"]["ling-3.0-flash"]["record"]
    assert record["rp5h"] == 1000
    assert record["usage_quota"] == 60
    assert any(w["type"] == "free_default_filled" and w["provider"] == "ant" for w in result["warnings"])


def test_variant_suffix_candidate_inherits_base_score() -> None:
    # -vl on the candidate side reaches the base model's hand-assigned
    # arena record through the chain's variant-suffix layer; same-model
    # inheritance is accepted silently (ADR 0015).
    sections = {"ant": {"ling-3.0-flash-vl": rec(name="Ling 3.0 Flash VL", rp5h=500)}}
    arena = {"ling-3.0-flash": 1520.0}

    result = build(sections, arena=arena)

    candidates = {c["model_id"]: c for c in result["candidates"]}
    assert candidates["ling-3.0-flash-vl"]["arena_score"] == 1520.0


def test_unrecognized_variant_suffix_surfaces_for_triage() -> None:
    # A never-seen alphabetic suffix on an unmatched model is raised for
    # human review instead of silently taking the 1500 default.
    sections = {"ant": {"ling-3.0-flash-vq": rec(name="Ling 3.0 Flash VQ", rp5h=500)}}
    arena = {"ling-3.0-flash": 1458.0}

    result = build(sections, arena=arena)

    assert any(
        w["type"] == "unrecognized_variant_suffix"
        and w["model"] == "ling-3.0-flash-vq"
        and w["suffix"] == "vq"
        for w in result["warnings"]
    )


def test_blocklist_entry_skips_model() -> None:
    # Hand-maintained model decisions live in blocklist.<channel>, matched by
    # full or vendor-prefix-stripped id — not on the collected record.
    sections = {
        "commandcode-goat": {
            "minimax-m2.5": rec(rp5h=None),
            "kimi-k2.5": rec(rp5h=900),
        },
    }
    blocklist = {"commandcode-goat": [{"id": "minimax-m2.5", "reason": "Missing mapping-critical RP5H"}]}

    result = build(sections, blocklist=blocklist)

    assert "minimax-m2.5" not in result["registry"]
    assert any(
        w["type"] == "manual_excluded" and w["model"] == "minimax-m2.5"
        and w["reason"] == "Missing mapping-critical RP5H"
        for w in result["warnings"]
    )


def test_blocklist_bare_form_matches_collected_id() -> None:
    # The blocklist may spell ids as the upstream exposes them (vendor
    # prefix); the collected bare lowercase id still matches.
    sections = {"opencode-go": {"gemini-3.5-flash-lite": rec(rp5h=800)}}
    blocklist = {"opencode-go": [{"id": "google/gemini-3.5-flash-lite", "reason": "tier"}]}

    result = build(sections, blocklist=blocklist)

    assert result["registry"] == {}
    assert any(w["type"] == "manual_excluded" and w["model"] == "gemini-3.5-flash-lite" for w in result["warnings"])


def test_lowscore_non_free_excluded_free_exempt() -> None:
    # Derived exclusion: a scored non-free model below 1500 is materialized
    # into the blocklist and leaves the registry; free models are exempt
    # regardless of score.
    sections = {
        "commandcode-goat": {
            "minimax-m3": rec(rp5h=3200),            # arena 1487.3 -> excluded
            "laguna-s-2.1-free": rec(rp5h=None, cost={"input": 0, "output": 0}),  # free, exempt
        },
    }
    arena = {"minimax-m3": 1487.3, "laguna-s-2.1-free": 1500.0}

    result = build(sections, arena=arena)

    assert "minimax-m3" not in result["registry"]
    assert "laguna-s-2.1-free" in result["registry"]
    assert any(
        w["type"] == "manual_excluded" and w["model"] == "minimax-m3"
        and w["reason"].startswith("lowscore:")
        for w in result["warnings"]
    )
    assert "minimax-m3" not in {c["model_id"] for c in result["candidates"]}


def test_speed_variant_ids_are_materialized_as_excluded() -> None:
    # Speed-marketing ids are materialized into the blocklist (reason class
    # "speed:") on every run, so the exclusion is visible and the channel
    # regex can enumerate it.
    sections = {
        "commandcode-goat": {
            "glm-5.2-fast": rec(rp5h=138),
            "kimi-k2.5": rec(rp5h=900),
        },
    }

    result = build(sections)

    assert "glm-5.2-fast" not in result["registry"]
    assert any(
        w["type"] == "manual_excluded" and w["model"] == "glm-5.2-fast"
        and w["reason"].startswith("speed:")
        for w in result["warnings"]
    )


def test_rp5h_missing_low_arena_excluded_high_arena_review() -> None:
    sections = {
        "commandcode-goat": {
            "glm-5": rec(rp5h=None),          # arena 1435 < 1500 -> lowscore excluded
            "kimi-k2.5": rec(rp5h=None),      # no arena standing -> excluded too
        },
    }
    arena = {"glm-5": 1435.72}

    result = build(sections, arena=arena)

    assert result["registry"] == {}
    # The scored-but-low model is materialized into the blocklist and
    # excluded ahead of the rp5h triage.
    assert any(
        w["type"] == "manual_excluded" and w["model"] == "glm-5"
        and w["reason"].startswith("lowscore:")
        for w in result["warnings"]
    )
    # No invented 1500: an unlisted model has no standing, so the missing-rp5h
    # triage excludes it instead of reviewing it (ADR 0015).
    assert any(w["type"] == "arena_missing" and w["model"] == "kimi-k2.5" for w in result["warnings"])
    assert any(w["type"] == "rp5h_missing_excluded" and w["model"] == "kimi-k2.5" for w in result["warnings"])


def test_missing_arena_leaves_candidate_unscored() -> None:
    sections = {"opencode-go": {"omen-alpha": rec(rp5h=11600)}}
    arena = {"muse-spark-1.3-contributor": 1622.48}
    sections["opencode-go"]["muse-spark-1.3-contributor"] = rec(rp5h=45300)

    result = build(sections, arena=arena)

    assert any(w["type"] == "arena_missing" and w["model"] == "omen-alpha" for w in result["warnings"])
    candidates = {c["model_id"]: c for c in result["candidates"]}
    # Listed for review, but with no invented score.
    assert candidates["omen-alpha"]["arena_score"] is None


def test_borrowed_arena_scores_are_rejected() -> None:
    # version_downgrade (qwen3.7-plus -> qwen3.6-plus) and prefix_match
    # (qwen3.8-flash -> the qwen3.8-* family) borrow another model's
    # standing; both are rejected with an empty score (ADR 0015).
    sections = {
        "commandcode-goat": {
            "qwen3.7-plus": rec(rp5h=4300),
            "qwen3.8-flash": rec(rp5h=5400),
        },
    }
    arena = {"qwen3.6-plus": 1460.01, "qwen3.8-flash-27b": 1600.0}

    result = build(sections, arena=arena)

    rejected = {w["model"]: w["match_type"] for w in result["warnings"] if w["type"] == "arena_borrowed_rejected"}
    assert rejected == {"qwen3.7-plus": "version_downgrade", "qwen3.8-flash": "prefix_match"}
    candidates = {c["model_id"]: c for c in result["candidates"]}
    assert candidates["qwen3.7-plus"]["arena_score"] is None
    assert candidates["qwen3.8-flash"]["arena_score"] is None


def test_alias_merges_cross_channel_naming() -> None:
    aliases = {"tencent-hy3": "hy3"}
    sections = {
        "opencode-go": {"hy3": rec(rp5h=4300)},
        "commandcode-goat": {"tencent-hy3": rec(rp5h=7080)},
    }

    result = build(sections, aliases=aliases)

    # One canonical model, owned by the goat channel (7080 > 4300); the
    # native per-channel spellings live in channelAliases.
    assert set(result["registry"]) == {"hy3"}
    entry = result["registry"]["hy3"]
    assert entry["channel"] == "commandcode-goat"
    assert entry["channel_aliases"] == {"commandcode-goat": "tencent-hy3"}


def test_free_variant_supersedes_plain_original() -> None:
    sections = {
        "opencode-go": {"longcat-2.0": rec(rp5h=11400)},
        "commandcode-goat": {"longcat-2.0-free": rec(rp5h=None, cost={"input": 0, "output": 0})},
    }

    result = build(sections)

    assert set(result["registry"]) == {"longcat-2.0-free"}
    assert any(
        w["type"] == "variant_superseded" and w["model"] == "longcat-2.0" and w["replaced_by"] == "longcat-2.0-free"
        for w in result["warnings"]
    )


def test_contributor_variant_supersedes_plain_original() -> None:
    sections = {
        "commandcode-goat": {
            "muse-spark-1.2": rec(rp5h=428),
            "muse-spark-1.2-contributor": rec(rp5h=18200),
        },
    }

    result = build(sections)

    assert set(result["registry"]) == {"muse-spark-1.2-contributor"}
    assert any(w["type"] == "variant_superseded" and w["model"] == "muse-spark-1.2" for w in result["warnings"])




def test_declared_zero_prices_are_free() -> None:
    # Collection transcribes every channel's freeness wording to zero
    # cost; the planner reads prices, nothing else.
    assert is_free_model(rec(cost={"input": 0, "output": 0})) is True


def test_positive_prices_are_not_free() -> None:
    assert is_free_model(rec(cost={"input": 0.15, "output": 0.6})) is False


def test_undeclared_prices_are_not_free() -> None:
    # No declared prices: the rp5h-missing triage owns the record,
    # freeness does not step in.
    assert is_free_model(rec()) is False


def test_flags_are_no_longer_read() -> None:
    # Legacy markers from the flag era must not resurrect freeness: only
    # the declared prices decide.
    assert is_free_model(rec(free=True, cost={"input": 0.15, "output": 0.6})) is False
    assert is_free_model(rec(free=True)) is False


def test_zero_declared_cost_model_gets_free_fill() -> None:
    sections = {
        "opencode-go": {
            "union-alpha": rec(rp5h=None, quota=None, cost={"input": 0, "output": 0}),
            "paid": rec(rp5h=500),
        },
    }

    result = build(sections)

    record = result["registry"]["union-alpha"]["record"]
    assert record["rp5h"] == 500
    assert any(
        w["type"] == "free_default_filled" and w["provider"] == "opencode-go"
        for w in result["warnings"]
    )
