#!/usr/bin/env python3
"""Push a provider snapshot's model list to an AxonHub channel (ADR 0010).

Managed channels whose entitlement fact source is an external snapshot
(today: ``data/goat-models.json`` for the commandcode channel) take their
``supportedModels`` from that snapshot verbatim — AxonHub's own upstream
sync stays off and there is no intersection fallback. A server-side timer
(vol-server) runs this script periodically: pull the raw snapshot from this
repository's main branch, then apply it here.

Semantics:
- Idempotent: reads live state first; exits 0 without writing when the
  channel already matches (list equal AND autoSyncSupportedModels false).
- One mutation: ``supportedModels`` (sorted snapshot keys) plus
  ``autoSyncSupportedModels: false``. Never touches channel settings,
  manualModels, or any other field.
- Write-then-verify: re-reads the channel after the mutation; a mismatch
  exits non-zero so the timer surfaces it and retries next round.
- Gates: refuses a snapshot without a non-empty ``models`` object for the
  provider and, with ``--expected-count MIN:MAX``, a count outside the range
  (mirrors the production-side gate in watch_goat.py).

Usage:
    AXONHUB_EMAIL=ops@example.com AXONHUB_PASSWORD_FILE=/etc/axonhub-push/password \\
        python3 apply_channel_models.py --source /var/lib/axonhub-push/goat-models.json \\
            --channel commandcode-goat --expected-count 25:60
    AXONHUB_JWT=<jwt> python3 apply_channel_models.py --source ... --dry-run

Exit codes: 0 success (including no-op and dry-run), 1 write/verify failure,
2 refused (bad snapshot, gate hit, or channel lookup failure).
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any, Callable, Optional

from common import fetch_connection, fetch_graphql

DEFAULT_AXONHUB_URL = "http://127.0.0.1:8868"

CHANNEL_FIELDS = "id name supportedModels autoSyncSupportedModels"

UPDATE_MUTATION = """
mutation($id: ID!, $input: UpdateChannelInput!) {
  updateChannel(id: $id, input: $input) {
    id
    name
    supportedModels
    autoSyncSupportedModels
  }
}
"""


class ApplyError(RuntimeError):
    """A user-actionable refusal: the run stops before any write (exit 2)."""


def load_target(source: Path, provider: str) -> list[str]:
    """Return the sorted model ids of ``provider`` in the snapshot document."""
    try:
        doc = json.loads(source.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise ApplyError(f"snapshot not found: {source}") from exc
    except json.JSONDecodeError as exc:
        raise ApplyError(f"invalid JSON in snapshot {source}: {exc}") from exc
    node = doc.get(provider) if isinstance(doc, dict) else None
    models = node.get("models") if isinstance(node, dict) else None
    if not isinstance(models, dict) or not models:
        raise ApplyError(
            f"snapshot {source} has no non-empty models object for provider {provider!r}"
        )
    return sorted(str(key) for key in models)


def parse_expected_count(raw: Optional[str]) -> Optional[tuple[int, int]]:
    """Parse a ``MIN:MAX`` gate; ``None`` (the default) disables the gate."""
    if raw is None:
        return None
    try:
        lo, hi = (int(part) for part in raw.split(":", 1))
    except ValueError as exc:
        raise ApplyError(f"--expected-count must look like MIN:MAX, got {raw!r}") from exc
    if lo <= 0 or hi < lo:
        raise ApplyError(f"--expected-count must satisfy 0 < MIN <= MAX, got {raw!r}")
    return lo, hi


def check_expected_count(target: list[str], expected: Optional[tuple[int, int]]) -> None:
    if expected is None:
        return
    lo, hi = expected
    if not lo <= len(target) <= hi:
        raise ApplyError(f"model count {len(target)} outside expected range [{lo}, {hi}]")


def sign_in(
    axonhub_url: str,
    email: str,
    password: str,
    *,
    opener: Callable[..., Any] = urllib.request.urlopen,
) -> str:
    """Exchange service-account credentials for a fresh JWT via /admin/auth/signin."""
    request = urllib.request.Request(
        f"{axonhub_url.rstrip('/')}/admin/auth/signin",
        data=json.dumps({"email": email, "password": password}).encode(),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with opener(request, timeout=30) as response:
            body = json.loads(response.read().decode("utf-8"))
    except (OSError, urllib.error.URLError) as exc:
        raise RuntimeError("AxonHub signin request failed") from exc
    except json.JSONDecodeError as exc:
        raise RuntimeError("AxonHub signin returned invalid JSON") from exc
    token = body.get("token") if isinstance(body, dict) else None
    if not isinstance(token, str) or not token:
        raise RuntimeError("AxonHub signin response has no token")
    return token


def resolve_token(args: argparse.Namespace) -> str:
    """Return the JWT from --token/AXONHUB_JWT, or sign in with credentials."""
    token = args.token or os.environ.get("AXONHUB_JWT")
    if token:
        return token
    email = os.environ.get("AXONHUB_EMAIL", "")
    password = os.environ.get("AXONHUB_PASSWORD", "")
    if args.password_file is not None:
        try:
            password = args.password_file.read_text(encoding="utf-8").strip()
        except OSError as exc:
            raise ApplyError(f"cannot read password file {args.password_file}: {exc}") from exc
    if not email or not password:
        raise ApplyError(
            "provide --token / AXONHUB_JWT, or AXONHUB_EMAIL plus "
            "AXONHUB_PASSWORD / --password-file"
        )
    return sign_in(args.axonhub_url, email, password)


def find_channel(channels: list[dict], name: str) -> dict:
    """Match a channel by exact name; never guess on ambiguity."""
    matches = [node for node in channels if node.get("name") == name]
    if not matches:
        raise ApplyError(f"channel {name!r} not found on AxonHub")
    if len(matches) > 1:
        raise ApplyError(f"channel {name!r} matched {len(matches)} channels — refusing to guess")
    return matches[0]


def describe_diff(current: list[str], target: list[str]) -> tuple[list[str], list[str]]:
    """Return (to-add, to-remove) between the live list and the target."""
    return sorted(set(target) - set(current)), sorted(set(current) - set(target))


def push_channel_models(axonhub_url: str, token: str, channel: dict, target: list[str]) -> None:
    fetch_graphql(
        axonhub_url,
        token,
        UPDATE_MUTATION,
        {
            "id": channel["id"],
            "input": {"supportedModels": target, "autoSyncSupportedModels": False},
        },
    )


def _fetch_channels(axonhub_url: str, token: str) -> list[dict]:
    return fetch_connection(axonhub_url, token, "channels", CHANNEL_FIELDS)


def _print_diff(added: list[str], removed: list[str]) -> None:
    for label, models in (("add", added), ("remove", removed)):
        for mid in models[:10]:
            print(f"  {label}: {mid}")
        if len(models) > 10:
            print(f"  ... and {len(models) - 10} more to {label}")


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        description="Push a provider snapshot's models keys to an AxonHub channel"
    )
    parser.add_argument("--source", type=Path, required=True, help="api.json-shaped snapshot file")
    parser.add_argument("--channel", required=True, help="exact AxonHub channel name")
    parser.add_argument(
        "--provider", help="provider key in the snapshot (default: same as --channel)"
    )
    parser.add_argument(
        "--axonhub-url",
        default=os.environ.get("AXONHUB_URL") or DEFAULT_AXONHUB_URL,
        help=f"AxonHub base URL (default: {DEFAULT_AXONHUB_URL})",
    )
    parser.add_argument("--token", help="AxonHub JWT (default: AXONHUB_JWT env)")
    parser.add_argument(
        "--password-file",
        type=Path,
        default=None,
        help="password file for unattended signin (requires AXONHUB_EMAIL)",
    )
    parser.add_argument(
        "--expected-count",
        default=None,
        metavar="MIN:MAX",
        help="refuse model counts outside this range (mirrors watch_goat.py's gate)",
    )
    parser.add_argument("--dry-run", action="store_true", help="show the diff without writing")
    args = parser.parse_args(argv)

    try:
        target = load_target(args.source, args.provider or args.channel)
        check_expected_count(target, parse_expected_count(args.expected_count))
        token = resolve_token(args)

        channel = find_channel(_fetch_channels(args.axonhub_url, token), args.channel)
        where = f"channel {channel['name']} ({channel['id']})"
        current = sorted(channel.get("supportedModels") or [])
        added, removed = describe_diff(current, target)
        in_sync = not added and not removed and channel.get("autoSyncSupportedModels") is False

        if in_sync:
            print(f"apply-channel-models: {where} already in sync ({len(target)} models)")
            return 0

        print(f"apply-channel-models: {where} drift: +{len(added)} -{len(removed)}")
        _print_diff(added, removed)
        if args.dry_run:
            print("apply-channel-models: [DRY RUN] no changes written")
            return 0

        push_channel_models(args.axonhub_url, token, channel, target)
        after = find_channel(_fetch_channels(args.axonhub_url, token), args.channel)
        if (
            sorted(after.get("supportedModels") or []) != target
            or after.get("autoSyncSupportedModels") is not False
        ):
            print(
                "apply-channel-models: verification failed — channel state does not match target",
                file=sys.stderr,
            )
            return 1
        print(f"apply-channel-models: {where} updated and verified ({len(target)} models)")
        return 0
    except ApplyError as exc:
        print(str(exc), file=sys.stderr)
        return 2
    except RuntimeError as exc:
        print(str(exc), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
