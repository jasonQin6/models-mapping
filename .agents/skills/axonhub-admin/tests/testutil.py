#!/usr/bin/env python3
"""Shared factories for the axonhub-admin step tests."""

from typing import Any, Mapping, Optional

from channel_sync import materialize_blocklist  # noqa: E402
from registry import build_registry  # noqa: E402


def rec(rp5h: Optional[float] = None, quota: Optional[float] = None, name: str = "X", **kw: Any) -> dict[str, Any]:
    record: dict[str, Any] = {"name": name, "rp5h": rp5h, "usage_quota": quota, "cost": {}}
    record.update(kw)
    return record


def arena_doc(scores: dict[str, float]) -> dict:
    models = {model_id: {"arena_score": score} for model_id, score in scores.items()}
    return {"schema_version": 1, "models": models}


def arena_models(arena: dict[str, float]) -> dict:
    return {model_id: {"rating": score} for model_id, score in arena.items()}


def build(
    sections: Mapping[str, Mapping[str, dict[str, Any]]],
    arena: dict[str, float] | None = None,
    aliases: Mapping[str, str] | None = None,
    blocklist: Mapping[str, list[dict[str, str]]] | None = None,
) -> dict:
    # Mirror the real flow: channel-sync materializes the derived classes,
    # every step consumes the stored blocklist as-is.
    lookup = arena_models(arena or {})
    blocklist_raw = materialize_blocklist(blocklist or {}, sections, aliases or {}, lookup)
    return build_registry(
        sections=sections,
        aliases=aliases or {},
        blocklist_raw=blocklist_raw,
        arena_models=lookup,
    )
