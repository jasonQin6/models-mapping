#!/usr/bin/env python3
"""Step 1 — channel-sync: maintain the AxonHub autoSync filter.

AxonHub channels run with ``autoSyncSupportedModels`` on, so the upstream
model list flows in hourly; the project governs it only through the blocking
regex ``autoSyncModelPattern``.  This script is the single owner of that
filter's logic:

1. Materialize the derived blocklist classes — rebuild the planner-owned
   ``speed:``/``lowscore:``/``superseded:`` entries from the current snapshots
   (human-owned reasons pass through, conflicts keep the human entry) and
   write ``data/blocklist.json`` atomically.
2. Generate the per-channel allow regex as a pure enumeration of the stored
   blocklist: a fixed ``claude-*`` block (Claude is served by AxonHub's own
   global models and is never collected) plus one terminated match per
   blocklist id.  Output is JSON on stdout for the confirm-then-write flow;
   the actual ``updateChannel`` mutation happens in the interactive session.

Pure offline: no credentials, no network, no AxonHub writes.

Usage:
    python3 .agents/skills/axonhub-admin/scripts/channel_sync.py [--channel <name>]
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable, Mapping, Optional, Sequence

from registry import canonical_id, is_free_model, variant_supersessions  # noqa: E402
from name_matching import find_best_match  # noqa: E402
from snapshot import (  # noqa: E402
    BLOCKLIST_PATH,
    EXTRA_SCHEMA_VERSION,
    PlanningError,
    load_arena,
    load_blocklist,
    load_extra_aliases,
    load_extra_sections,
    number,
)

# Speed-marketing variants of a base model are never adopted; the exclusion
# derives from the id so the store keeps no derived state.  Series names like
# ``-flash`` do not match.
SPEED_VARIANT_SUFFIXES = ("-fast", "-highspeed")
# Planner-owned blocklist reason prefixes: entries with these reasons are
# regenerated from scratch on every run; every other reason is human-owned
# and passes through untouched.
PLANNER_OWNED_REASON_PREFIXES = ("speed:", "lowscore:", "superseded:")
SPEED_VARIANT_EXCLUDE_REASON = "speed: fast/highspeed suffix"
# Derived exclusion materialized into the blocklist: a scored non-free model
# below this leaves the registry (aligned with the models.md "低分非免费不入册"
# write-side rule and this script's regex, which enumerates the blocklist).
LOWSCORE_EXCLUDE_THRESHOLD = 1500.0


def speed_variant_exclude(model_id: str) -> Optional[str]:
    """Return the derived exclude reason for speed-marketing ids, else None."""

    lowered = model_id.strip().lower()
    for suffix in SPEED_VARIANT_SUFFIXES:
        if lowered.endswith(suffix):
            return SPEED_VARIANT_EXCLUDE_REASON
    return None


def superseded_variant_excludes(
    sections: Mapping[str, Mapping[str, dict[str, Any]]],
    aliases: Mapping[str, str],
) -> dict[tuple[str, str], dict[str, str]]:
    """Derived entries for variant-superseded ids a channel still serves.

    Ruling mirrors the registry's variant groups exactly (``-free`` beats
    ``-contributor`` beats plain, via :func:`registry.variant_supersessions`):
    a losing variant leaves the registry, so a channel still exposing it
    serves an id no global model routes to — block it.  Grouping is global
    over every section, so a plain id loses to a ``-free`` variant served by
    another channel too.
    """

    canonicals = {
        canonical_id(native_id, aliases)
        for records in sections.values()
        for native_id in records
    }
    supersessions = variant_supersessions(canonicals)
    derived: dict[tuple[str, str], dict[str, str]] = {}
    for channel, records in sections.items():
        for native_id in sorted(records):
            winner = supersessions.get(canonical_id(native_id, aliases))
            if winner:
                derived[(channel.lower(), native_id.strip().lower())] = {
                    "id": native_id,
                    "reason": f"superseded: replaced by {winner}",
                }
    return derived


def materialize_blocklist(
    raw_blocklist: Mapping[str, Sequence[Any]],
    sections: Mapping[str, Mapping[str, dict[str, Any]]],
    aliases: Mapping[str, str],
    arena_models: Mapping[str, Mapping[str, Any]],
) -> dict[str, list[dict[str, str]]]:
    """Regenerate the planner-owned blocklist entries; human entries pass through.

    The blocklist is the single source of truth for every exclusion: this
    step owns the ``speed:``, ``lowscore:`` and ``superseded:`` reason
    classes and rebuilds them from the current snapshots (stale derived
    entries disappear, refreshed ones update in place); every other reason
    (``manual``/``tier``/``retired``/…) belongs to the human maintainers and
    is preserved verbatim.  A human entry for the same id wins over a
    derived one.  The sync regex is a pure enumeration of the result.
    """

    human: dict[tuple[str, str], dict[str, str]] = {}
    for channel, entries in raw_blocklist.items():
        for entry in entries:
            record = entry if isinstance(entry, Mapping) else {"id": str(entry), "reason": "manual"}
            reason = str(record.get("reason") or "manual")
            if reason.startswith(PLANNER_OWNED_REASON_PREFIXES):
                continue
            lowered = str(record.get("id") or "").strip().lower()
            if not lowered:
                continue
            human[(str(channel).lower(), lowered)] = {
                "id": str(record.get("id")),
                "reason": reason,
            }
            human.setdefault(
                (str(channel).lower(), lowered.split("/")[-1]),
                {"id": str(record.get("id")), "reason": reason},
            )

    derived: dict[tuple[str, str], dict[str, str]] = {}
    for channel, records in sections.items():
        for native_id, record in sorted(records.items()):
            lowered = native_id.strip().lower()
            if (
                (channel.lower(), lowered) in human
                or (channel.lower(), lowered.split("/")[-1]) in human
            ):
                continue
            if speed_variant_exclude(native_id):
                derived[(channel, lowered)] = {"id": native_id, "reason": SPEED_VARIANT_EXCLUDE_REASON}
                continue
            canonical = canonical_id(native_id, aliases)
            match, match_type = find_best_match(canonical, dict(arena_models))
            score = number((match or {}).get("rating"))
            if match_type in ("version_downgrade", "prefix_match"):
                score = None
            if (
                not is_free_model(record)
                and score is not None
                and score < LOWSCORE_EXCLUDE_THRESHOLD
            ):
                derived[(channel, lowered)] = {
                    "id": native_id,
                    "reason": f"lowscore: arena {score:g} < {LOWSCORE_EXCLUDE_THRESHOLD:g}",
                }

    human_keys = set(human)
    for key, entry in superseded_variant_excludes(sections, aliases).items():
        # Human rulings and the per-id speed/lowscore triage already covering
        # the id win; supersession is the residual derived class.
        bare_key = (key[0], key[1].split("/")[-1])
        if key in human_keys or bare_key in human_keys or key in derived:
            continue
        derived[key] = entry

    merged: dict[str, list[dict[str, str]]] = {}
    for (channel, _key), entry in sorted(human.items()):
        bucket = merged.setdefault(channel, [])
        if entry not in bucket:
            bucket.append(entry)
    for (channel, _key), entry in sorted(derived.items()):
        bucket = merged.setdefault(channel, [])
        if entry not in bucket:
            bucket.append(entry)
    for bucket in merged.values():
        bucket.sort(key=lambda item: item["id"])
    return merged


def run_materialize_blocklist(
    *,
    extra_path: Path,
    arena_path: Path,
    blocklist_path: Path = BLOCKLIST_PATH,
) -> tuple[dict[str, list[dict[str, str]]], dict[str, dict[str, dict[str, Any]]]]:
    """Materialize the derived blocklist classes and persist them.

    Rebuilds the planner-owned ``speed:``/``lowscore:``/``superseded:``
    entries from the current snapshots (human-owned reasons pass through,
    conflicts keep the human entry) and writes ``data/blocklist.json``
    atomically, so the sync
    regex is a pure enumeration of the stored result.  ``extra_path`` is
    read-only here — the collection store belongs to watch-pipeline.

    Returns the merged blocklist and the channel sections (the main flow
    reports human entries whose id has left the channel's section, so the
    caller needs both).
    """

    sections = load_extra_sections(extra_path)
    aliases = load_extra_aliases(extra_path)
    raw_blocklist = load_blocklist(blocklist_path)
    arena_models = load_arena(arena_path)
    merged = materialize_blocklist(raw_blocklist, sections, aliases, arena_models)
    _write_blocklist(blocklist_path, merged)
    return merged, sections


# Self-description persisted into the blocklist store so the file records its
# own ownership and class semantics (AGENTS.md deliberately does not).
REASON_CLASSES_DOC = {
    "lowscore:": "derived — arena score below 1500 and not free (rebuilt on every run)",
    "speed:": "derived — '-fast'/'-highspeed' speed-marketing variants (rebuilt on every run)",
    "superseded:": "derived — variant supersession, '-free' beats '-contributor' beats plain (rebuilt on every run)",
    "manual": "human — maintainer-ordered removal (e.g. upstream auth/id issues)",
    "tier": "human — subscription-tier gap (stable set)",
    "retired": "human — superseded by a successor or otherwise withdrawn; suggestions come from model-card-update's retire_suggested report",
}


def _write_blocklist(path: Path, blocklist: Mapping[str, list[dict[str, str]]]) -> None:
    """Persist the exclusion store atomically (whole-file replace)."""

    document = {
        "schema_version": EXTRA_SCHEMA_VERSION,
        "reason_classes": REASON_CLASSES_DOC,
        "updated_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "blocklist": {
            channel: [dict(entry) for entry in entries]
            for channel, entries in sorted(blocklist.items())
        },
    }
    temporary = path.with_name(f".{path.name}.tmp")
    temporary.write_text(
        json.dumps(document, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    temporary.replace(path)


def bare_id(entry_id: str) -> str:
    """Strip the vendor prefix from a blocklist id (``zai-org/GLM-5`` -> ``GLM-5``)."""

    return str(entry_id).strip().rsplit("/", 1)[-1]


def sync_pattern(entry_ids: Iterable[str]) -> str:
    """Render the AxonHub ``autoSyncModelPattern`` allow regex.

    ``(?i)`` is required — synced upstream ids are mixed-case with vendor
    prefixes (``zai-org/GLM-5``, ``MiniMaxAI/MiniMax-M2.5``) and a
    case-sensitive pattern silently under-blocks.  The fixed ``claude-*``
    block matches by prefix anywhere in the id; each blocklist id matches by
    its bare form terminated by end, ``:`` (variant suffixes), or ``/``.
    Target dialect is regexp2; this generated subset behaves identically in
    Python ``re``.
    """

    bare = sorted({bare for bare in (bare_id(item) for item in entry_ids) if bare})
    lookaheads = [r"(?!.*(^|/)claude-)"]
    if bare:
        alternation = "|".join(re.escape(item) for item in bare)
        lookaheads.append(r"(?!.*(^|/)(?:" + alternation + r")(?:$|[:/]))")
    return r"(?i)^" + "".join(lookaheads) + r".*$"


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        description="Materialize the derived blocklist classes and generate the autoSync allow regex per channel"
    )
    parser.add_argument("--extra", type=Path, default=Path("data/models_extra.json"))
    parser.add_argument("--arena", type=Path, default=Path("data/arena.json"))
    parser.add_argument("--blocklist", type=Path, default=BLOCKLIST_PATH)
    parser.add_argument(
        "--channel",
        action="append",
        default=None,
        help="restrict regex output to this channel (repeatable); materialization always covers all channels",
    )
    args = parser.parse_args(argv)

    try:
        merged, sections = run_materialize_blocklist(
            extra_path=args.extra, arena_path=args.arena, blocklist_path=args.blocklist
        )
    except PlanningError as exc:
        print(f"channel-sync: {exc}", file=sys.stderr)
        return 1

    total = sum(len(entries) for entries in merged.values())
    counts = ", ".join(f"{ch}={len(entries)}" for ch, entries in sorted(merged.items()))
    print(f"channel-sync: blocklist materialized ({total} entries: {counts})", file=sys.stderr)

    # 人工条目生命周期呈报：派生类随快照自愈，人工类会积累死条目。条目 id
    # 离开该渠道节键（公开事实源）即失去明确对象——上游是否也不再提供只有
    # 维护者能确认，确认后删条目；正则里的死亡条目无害但属噪音。
    for channel, entries in sorted(merged.items()):
        section_keys = {native_id.strip().lower() for native_id in sections.get(channel, {})}
        section_bare = {key.split("/")[-1] for key in section_keys}
        stale = [
            entry["id"]
            for entry in entries
            if not entry["reason"].startswith(PLANNER_OWNED_REASON_PREFIXES)
            and entry["id"].strip().lower() not in section_keys
            and entry["id"].strip().lower().split("/")[-1] not in section_bare
        ]
        if stale:
            print(
                f"channel-sync: {channel} 人工条目已不在节键，若上游也不再提供即可清理: "
                f"{', '.join(sorted(stale))}",
                file=sys.stderr,
            )

    selected = merged
    if args.channel:
        selected = {channel: merged[channel] for channel in args.channel if channel in merged}
        for channel in args.channel:
            if channel not in merged:
                print(f"channel-sync: no blocklist section for channel {channel!r}", file=sys.stderr)
    patterns = {
        channel: sync_pattern(entry["id"] for entry in entries)
        for channel, entries in sorted(selected.items())
    }
    print(json.dumps(patterns, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
