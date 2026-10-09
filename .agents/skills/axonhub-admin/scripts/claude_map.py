#!/usr/bin/env python3
"""Step 4 — Claude mapping: pair request models with serving candidates.

The fastest-moving part of the pipeline: each Claude request model from the
hand-maintained ``REQUESTS`` dict below is scored against every eligible
non-free candidate by the Arena/RP5H/proximity formula, then the free pool
fills the lowest-scored requests (ascending) with the lowest-scored free
models (ascending).  GPT models pass through by name in AxonHub and are
deliberately not maintained here.  The output is the review table for the
confirm-then-write flow; association writes happen in the interactive
session.

Pure offline: no credentials, no network, no AxonHub writes.

Usage:
    python3 .agents/skills/axonhub-admin/scripts/claude_map.py
"""

from __future__ import annotations

import argparse
import json
import math
import sys
from pathlib import Path
from typing import Any, Mapping, Optional, Sequence

from name_matching import find_best_match  # noqa: E402
from registry import build_registry  # noqa: E402
from snapshot import (  # noqa: E402
    BLOCKLIST_PATH,
    PROVIDER_CONF_PATH,
    PlanningError,
    load_arena,
    load_extra_aliases,
    load_blocklist,
    load_extra_sections,
    load_provider_conf,
    number,
)

# Hand-maintained Claude request models (id -> human note): add or remove
# entries to grow or shrink the mapped set.  GPT models pass through by name
# in AxonHub and are deliberately absent — this step maintains Claude
# associations only.  Ids use the dash spelling; arena 的点号拼写（
# claude-haiku-5.5）与 effort 后缀（-high/-max）由匹配链归一，-high 只是
# reasoning effort，不构成不同模型。
REQUESTS: dict[str, str] = {
    # 2026-09-30 首批中的 4-6 及以上（4-5 及以下代际 2026-10-09 淘汰：
    # haiku-4-5/sonnet-4-5/opus-4-1/opus-4-5 实体已删）
    "claude-opus-5": "",
    "claude-sonnet-4-6": "",
    "claude-sonnet-5": "",
    # 2026-10-09 对齐 Arena 现行 Anthropic 阵容（opus-4-6/4-7/4-8、
    # 5.5 双系、fable 家族）。渠道直供的 claude-*（claude-haiku-5-5）不进
    # REQUESTS：它们自服（渠道链即路由），在④只作公式候选。
    "claude-opus-4-6": "",
    "claude-opus-4-7": "",
    "claude-opus-4-8": "",
    "claude-opus-5-5": "",
    "claude-sonnet-5-5": "",
    "claude-fable-5": "",
    "claude-fable-5-1": "",
}

# 2026-10-09 调权：接近度为最高权重（0.50），绝对分与 rp5h 降为辅助
# （0.30/0.20）——映射优先挑分数最邻近的候选，而非绝对分最高者。
DEFAULT_WEIGHTS = {
    "score": 0.30,
    "rp5h": 0.20,
    "proximity": 0.50,
    "penalty_k": 0.2,
    "upgrade_bonus": 0.1,
}


def score_match(
    request_score: float,
    candidate: Mapping[str, Any],
    max_values: Mapping[str, float],
    weights: Optional[Mapping[str, float]] = None,
) -> float:
    """Score one candidate using the agreed Arena/RP5H/proximity formula."""

    selected = dict(DEFAULT_WEIGHTS)
    if weights:
        selected.update(weights)
    candidate_score = number(candidate.get("arena_score"), 0.0) or 0.0
    max_score = number(max_values.get("max_score"), 1.0) or 1.0
    max_rp5h = number(max_values.get("max_rp5h"), 1.0) or 1.0
    max_score_diff = number(max_values.get("max_score_diff"), 0.0) or 0.0
    rp5h = number(candidate.get("rp5h"), 0.0) or 0.0

    score_component = candidate_score / max_score if max_score else 0.0
    if max_rp5h > 0:
        rp5h_component = math.log1p(max(rp5h, 0.0)) / math.log1p(max_rp5h)
    else:
        rp5h_component = 0.0
    if max_score_diff == 0:
        proximity = 1.0
    else:
        proximity = 1.0 - abs(candidate_score - request_score) / max_score_diff

    penalty = 0.0
    if candidate_score < request_score and max_score_diff:
        penalty = selected["penalty_k"] * (request_score - candidate_score) / max_score_diff
    upgrade = selected["upgrade_bonus"] if candidate_score > request_score else 0.0

    return (
        selected["score"] * score_component
        + selected["rp5h"] * rp5h_component
        + selected["proximity"] * proximity
        - penalty
        + upgrade
    )


def compute_mapping_for_request_model(
    request_score: float,
    candidates: Sequence[Mapping[str, Any]],
    weights: Optional[Mapping[str, float]] = None,
) -> tuple[Optional[str], Optional[dict[str, Any]]]:
    """Choose the highest-scoring eligible candidate for one request model."""

    eligible = []
    for candidate in candidates:
        model_id = str(candidate.get("model_id") or "").strip()
        score = number(candidate.get("arena_score"))
        rp5h = number(candidate.get("rp5h"))
        if model_id and score is not None and rp5h is not None:
            eligible.append(candidate)
    if not eligible:
        return None, None

    scores = [number(item.get("arena_score"), 0.0) or 0.0 for item in eligible]
    max_values = {
        "max_score": max(scores) or 1.0,
        "max_rp5h": max(number(item.get("rp5h"), 0.0) or 0.0 for item in eligible) or 1.0,
        "max_score_diff": (max(scores) - min(scores)) if len(scores) > 1 else 0.0,
    }
    best_score, best_id, best_item = min(
        (
            (score_match(request_score, item, max_values, weights), str(item["model_id"]), item)
            for item in eligible
        ),
        key=lambda item: (-item[0], item[1]),
    )
    return best_id, {"target": best_id, "score": best_score, "candidate": best_item, "max_values": max_values}


def _confidence(match_type: str) -> str:
    if match_type == "direct_match":
        return "high"
    if match_type in ("variant_suffix", "version_downgrade", "punctuation_normalized"):
        return "medium"
    if match_type == "prefix_match":
        return "low"
    return "none"


def build_mappings(
    result: Mapping[str, Any],
    requests: Sequence[Mapping[str, Any]],
    arena_models: Mapping[str, Mapping[str, Any]],
) -> dict[str, Any]:
    """Map request models to candidates: formula first, then free fill.

    Mapping order: (1) every request is scored by the formula over non-free
    candidates with a legitimate Arena standing; (2) free fill — the free
    pool (ascending) is paired with the requests (ascending arena score),
    replacing the lowest-scored requests' formula targets.  A request model
    missing from the Arena board is a blocking error until a manual
    assignment lands.
    """

    warnings: list[dict[str, Any]] = list(result["warnings"])
    errors: list[dict[str, Any]] = []
    candidates = result["candidates"]

    scored_requests: list[dict[str, Any]] = []
    for request in requests:
        model_id = str(request.get("model_id") or "").strip()
        match, match_type = find_best_match(model_id, dict(arena_models))
        score = number((match or {}).get("rating"))
        if match is None or score is None:
            errors.append(
                {
                    "code": "request_arena_missing",
                    "message": f"request model {model_id} has no arena score",
                    "model": model_id,
                }
            )
            continue
        if match_type != "direct_match":
            warnings.append(
                {"type": "request_arena_fallback", "model": model_id, "match_type": match_type}
            )
        scored_requests.append({**request, "arena_score": score})
    if not scored_requests:
        warnings.append({"type": "empty_claude_series", "message": "no enabled claude-* request models"})

    scored_requests.sort(key=lambda item: (float(item["arena_score"]), str(item["model_id"])))
    free_candidates = sorted(
        (c for c in candidates if c["free"]),
        key=lambda c: ((c["arena_score"] if c["arena_score"] is not None else 0.0), c["model_id"]),
    )
    # Candidates without an arena standing (arena_missing or a rejected
    # borrowed score) cannot enter the formula: proximity and the arena term
    # are undefined for them.
    non_free_candidates = [
        c for c in candidates if not c["free"] and c["arena_score"] is not None
    ]

    resolved: dict[str, tuple[Optional[str], str]] = {}
    for request in scored_requests:
        score = float(request["arena_score"])
        target, _details = compute_mapping_for_request_model(score, non_free_candidates)
        match_type = next(
            (c["match_type"] for c in non_free_candidates if c["model_id"] == target), ""
        )
        resolved[str(request["model_id"])] = (target, _confidence(match_type))

    free_supply = iter(free_candidates)
    for request in scored_requests:
        free_entry = next(free_supply, None)
        if free_entry is None:
            break
        resolved[str(request["model_id"])] = (free_entry["model_id"], "free_fill")

    mappings: list[dict[str, Any]] = []
    for request in scored_requests:
        model_id = str(request["model_id"])
        score = float(request["arena_score"])
        target, confidence = resolved[model_id]
        if target is None:
            errors.append(
                {
                    "code": "request_without_target",
                    "message": f"request model {model_id} has no eligible target",
                    "model": model_id,
                }
            )
        mappings.append(
            {
                "request_model": model_id,
                "suggested_target": target,
                "arena_score": score,
                "match_confidence": confidence,
            }
        )

    return {
        "mappings": mappings,
        "errors": errors,
        "warnings": warnings,
        "counts": {
            "requests": len(mappings),
            "candidates": len(candidates),
            "warnings": len(warnings),
            "errors": len(errors),
        },
    }


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        description="Pair Claude request models with candidates by the Arena/RP5H formula plus free fill"
    )
    parser.add_argument("--extra", type=Path, default=Path("data/models_extra.json"))
    parser.add_argument("--blocklist", type=Path, default=BLOCKLIST_PATH)
    parser.add_argument("--provider-conf", type=Path, default=PROVIDER_CONF_PATH)
    parser.add_argument("--arena", type=Path, default=Path("data/arena.json"))
    args = parser.parse_args(argv)

    try:
        sections = load_extra_sections(args.extra)
        aliases = load_extra_aliases(args.extra)
        blocklist_raw = load_blocklist(args.blocklist)
        arena_models = load_arena(args.arena)
        try:
            _cards, _refs, provider_canonicals = load_provider_conf(args.provider_conf)
        except PlanningError as exc:
            print(
                f"claude-map: provider-conf unavailable ({exc}); variant convergence off",
                file=sys.stderr,
            )
            provider_canonicals = {}
        result = build_registry(
            sections=sections,
            aliases=aliases,
            blocklist_raw=blocklist_raw,
            arena_models=arena_models,
            provider_canonicals=provider_canonicals,
        )
    except PlanningError as exc:
        print(f"claude-map: {exc}", file=sys.stderr)
        return 1

    requests = [{"model_id": model_id} for model_id in REQUESTS]
    report = build_mappings(result, requests, arena_models)

    counts = json.dumps(report["counts"], ensure_ascii=False, sort_keys=True)
    print(f"claude-map: {counts}", file=sys.stderr)
    for error in report["errors"]:
        print(f"ERROR [{error['code']}] {error['message']}", file=sys.stderr)
    for warning in report["warnings"]:
        fields = " ".join(f"{key}={warning[key]!r}" for key in warning if key != "type")
        print(f"WARNING [{warning['type']}] {fields}", file=sys.stderr)

    print(
        json.dumps(
            {"mappings": report["mappings"], "counts": report["counts"]},
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        )
    )
    if report["errors"]:
        print(f"claude-map: {len(report['errors'])} blocking error(s); mappings are for review only", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
