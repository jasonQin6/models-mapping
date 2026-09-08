#!/usr/bin/env python3
"""Scrape https://commandcode.ai/docs/plans/goat and update ``data/models_extra.json``.

``watch-goat`` is the source watcher for the commandcode-goat channel.  The
section's keys are the authoritative entitlement allowlist of the commandcode
channel (ADR 0010): a partial parse must fail the run instead of publishing a
shrunken list.  The section carries channel-declared facts only (quotas,
GOAT deal prices, tok/s); public card data is filled at planning time from
``data/all_models.json``, never here.

Hard gates (any hit -> no write, last-error.json persisted):
- a main-table row is skipped for missing columns (page layout drifted);
- two rows normalize to the same model_id (to_model_id collision);
- zero models resolve;
- the model count falls outside ``expected_count`` in the reference contract.

Channel-provided ``claude-*`` models are not collected (ADR 0012): Claude
requests are served by self-built AxonHub models mapped by arena score.
Intelligence scoring drives model selection only and is not stored.

Stdlib only. Pipeline entrypoint; see .github/workflows/watch-pipeline.yml.
Usage: watch_goat.py [--url URL] [--html FILE] [--extra PATH] [--reference PATH]
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import urllib.request
from pathlib import Path
from typing import Any, Iterator, Mapping, Optional

from error_state import clear_error, error_dump_path, persist_error
from models_extra import (
    DEFAULT_EXTRA_PATH,
    is_excluded_model,
    speed_variant_exclude,
    update_channel,
)

sys.path.insert(0, str(Path(__file__).resolve().parent))

REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_REFERENCE = REPO_ROOT / "watch-pipeline" / "reference" / "goat" / "extra.json"
URL = "https://commandcode.ai/docs/plans/goat"
CHANNEL = "goat"

VERSION = re.compile(r"\d+(?:\.\d+)+")

Cell = tuple[str, Optional[str], str]


def strip_tags(html: str) -> str:
    html = re.sub(r"<[^>]+>", "", html)
    html = html.replace("&amp;", "&").replace("&#x27;", "'").replace("&lt;", "<").replace("&gt;", ">")
    return re.sub(r"\s+", " ", html).strip()


def parse_tables(html: str) -> list[list[list[Cell]]]:
    tables = []
    for table_html in re.findall(r"<table.*?</table>", html, re.DOTALL):
        rows = []
        for tr in re.findall(r"<tr.*?</tr>", table_html, re.DOTALL):
            cells = []
            for cell in re.findall(r"<t[hd][^>]*>.*?</t[hd]>", tr, re.DOTALL):
                m = re.search(r'href="(/models/[^"]+)"', cell)
                slug = m.group(1).rsplit("/", 1)[-1] if m else None
                cells.append((strip_tags(cell), slug, cell))
            if cells:
                rows.append(cells)
        tables.append(rows)
    return tables


def col_index(header: list[Cell], names: set[str]) -> Optional[int]:
    for i, (text, _slug, _raw) in enumerate(header):
        if text.split("\u2195")[0].strip().lower() in names:
            return i
    return None


def slugify(name: str) -> str:
    return re.sub(r"\s+", "-", name.lower())


def to_model_id(slug: str) -> str:
    return re.sub(r"(?<![.\d])(\d+)-(\d+)(?![.\d])", r"\1.\2", slug)


def norm_name(name: str) -> str:
    return re.sub(r"\s+", " ", name).strip().lower()


def cell_model_name(cell_html: str) -> str:
    m = re.search(r'<a href="/models/[^"]+"[^>]*>(.*?)</a>', cell_html, re.DOTALL)
    if m:
        return strip_tags(m.group(1))
    return strip_tags(re.sub(r"<button.*?</button>", "", cell_html, flags=re.DOTALL))


def build_score_index(universe: dict[str, tuple[str, str]]) -> dict[str, tuple[str, str]]:
    idx: dict[str, tuple[str, str]] = {}
    for name, (slug, score) in universe.items():
        m = re.fullmatch(r"(\d+(?:\.\d+)?)", score.strip())
        if m:
            entry = (name, m.group(1))
            idx.setdefault(name.lower(), entry)
            idx.setdefault(slug, entry)
    return idx


def lookup_score(
    idx: Mapping[str, tuple[str, str]], name: str
) -> Optional[tuple[str, str]]:
    return idx.get(name.lower()) or idx.get(slugify(name))


def base_candidates(
    name: str, idx: Mapping[str, tuple[str, str]]
) -> Iterator[str]:
    tokens = name.split()
    chain = [" ".join(tokens[:-d]) for d in range(1, len(tokens))] + [name]
    own = lookup_score(idx, name)
    for cand in chain:
        hit = lookup_score(idx, cand)
        if hit and hit != own:
            yield hit[0]
    for cand in chain:
        m = None
        for m in VERSION.finditer(cand):
            pass
        if m is None:
            continue
        cur = tuple(int(p) for p in m.group().split("."))
        preds: list[tuple[tuple[int, ...], str]] = []
        seen: set[str] = set()
        for canonical, _score in idx.values():
            if canonical in seen:
                continue
            seen.add(canonical)
            om = None
            for om in VERSION.finditer(canonical):
                pass
            if om is None:
                continue
            ov = tuple(int(p) for p in om.group().split("."))
            rebuilt = cand[: m.start()] + om.group() + cand[m.end() :]
            hit = lookup_score(idx, rebuilt)
            if ov < cur and hit and hit[0] == canonical:
                preds.append((ov, canonical))
        for _ov, canonical in sorted(preds, reverse=True):
            yield canonical


def parse_price(raw: str) -> Optional[float]:
    t = raw
    if "<" in t:
        # Discounted cells quote the struck "was" price in <s> followed by the
        # bare deal price; footnote triggers live in <button>. Drop both so the
        # effective GOAT price is the only number left.
        t = re.sub(r"<s[^>]*>.*?</s>", " ", t, flags=re.DOTALL)
        t = re.sub(r"<button.*?</button>", " ", t, flags=re.DOTALL)
        t = strip_tags(t)
    t = t.strip()
    if not t or t in ("—", "-", "–"):
        return None
    if re.fullmatch(r"free", t, re.IGNORECASE):
        return 0.0
    t = re.sub(r"\+\d+\s*$", "", t).strip()
    t = t.replace("$", "").replace(",", "").strip()
    m = re.search(r"(\d+(?:\.\d+)?)", t)
    if not m:
        return None
    try:
        return float(m.group(1))
    except ValueError:
        return None


def parse_quota(raw: str) -> Optional[int]:
    t = strip_tags(raw) if "<" in raw else str(raw)
    t = t.replace(",", "").strip()
    if not t or t in ("—", "-", "?", "?"):
        return None
    try:
        return int(float(t))
    except ValueError:
        return None


def parse_credits(raw: str) -> Optional[float]:
    t = strip_tags(raw) if "<" in raw else str(raw)
    t = t.replace("$", "").replace(",", "").strip()
    if not t or t in ("—", "-"):
        return None
    try:
        return float(t)
    except ValueError:
        return None


def fetch_html(url: str, html_path: Optional[str]) -> str:
    if html_path:
        return Path(html_path).read_text(encoding="utf-8")
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=30) as resp:
        return resp.read().decode("utf-8")


def build_goat_fields(
    name: str,
    slug: str,
    *,
    price_input_raw: Optional[str],
    price_output_raw: Optional[str],
    cache_read_raw: Optional[str],
    cache_write_raw: Optional[str],
    rp5h_val: Optional[int],
    usage_quota_val: Optional[float],
    tok_s_raw: Optional[str],
) -> dict:
    """Return the models_extra record for one GOAT model.

    Only channel-declared facts are kept: display name, GOAT deal pricing
    (cost), rp5h, usage_quota (== Monthly credits) and tok_s (GOAT-only
    throughput).  Monthly credits map to usage_quota; intelligence scoring is
    not stored.
    """
    price_in = parse_price(price_input_raw or "") if price_input_raw is not None else None
    price_out = parse_price(price_output_raw or "") if price_output_raw is not None else None
    cache_r = parse_price(cache_read_raw or "") if cache_read_raw is not None else None
    cache_w = parse_price(cache_write_raw or "") if cache_write_raw is not None else None
    cost: dict = {}
    if price_in is not None:
        cost["input"] = price_in
    if price_out is not None:
        cost["output"] = price_out
    if cache_r is not None:
        cost["cache_read"] = cache_r
    if cache_w is not None:
        cost["cache_write"] = cache_w

    tok_s: Optional[int] = None
    if tok_s_raw is not None:
        raw_t = strip_tags(tok_s_raw) if "<" in tok_s_raw else str(tok_s_raw).strip()
        if raw_t and raw_t not in ("—", "-", ""):
            try:
                tok_s = int(float(raw_t))
            except ValueError:
                tok_s = None
    record = {
        "name": name,
        "rp5h": rp5h_val,
        "usage_quota": usage_quota_val,
        "tok_s": tok_s,
        "cost": cost,
    }
    exclude_reason = speed_variant_exclude(to_model_id(slug))
    if exclude_reason:
        record["exclude"] = exclude_reason
    return record


def load_expected_count(reference: Path) -> Optional[tuple[int, int]]:
    """Return the (min, max) model-count gate from the channel reference contract.

    The section is the authoritative allowlist, so a partial parse must fail
    the run instead of publishing a shrunken list. A missing file or a
    malformed ``expected_count`` disables the gate.
    """
    try:
        doc = json.loads(reference.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    gate = doc.get("expected_count") if isinstance(doc, dict) else None
    if not isinstance(gate, dict):
        return None
    try:
        lo, hi = int(gate["min"]), int(gate["max"])
    except (KeyError, TypeError, ValueError):
        return None
    if lo <= 0 or hi < lo:
        return None
    return lo, hi


def main(argv: Optional[list[str]] = None) -> int:
    ap = argparse.ArgumentParser(
        description="Scrape the GOAT plan page and update the commandcode-goat section of data/models_extra.json"
    )
    ap.add_argument("--url", default=URL)
    ap.add_argument("--html", help="read HTML from local file instead of fetching (for testing)")
    ap.add_argument(
        "--extra",
        type=Path,
        default=DEFAULT_EXTRA_PATH,
        help="Path to data/models_extra.json (default: data/models_extra.json)",
    )
    ap.add_argument(
        "--dump-html",
        type=Path,
        default=None,
        help="Where to dump HTML when the page structure changes (default: watch-pipeline/reference/goat/failed-page.html)",
    )
    ap.add_argument(
        "--reference",
        type=Path,
        default=DEFAULT_REFERENCE,
        help="Channel contract JSON carrying the expected_count gate (default: watch-pipeline/reference/goat/extra.json)",
    )
    args = ap.parse_args(argv)

    try:
        html = fetch_html(args.url, args.html)
        tables = parse_tables(html)

        main_rows = None
        quota_rows = None
        for rows in tables:
            hdr = rows[0] if rows else []
            if col_index(hdr, {"intelligence"}) is not None and col_index(hdr, {"model"}) is not None:
                main_rows = rows
            if col_index(hdr, {"requests / 5 hours"}) is not None:
                quota_rows = rows
        if main_rows is None or quota_rows is None:
            dump: Path = args.dump_html or error_dump_path(CHANNEL)
            dump.parent.mkdir(parents=True, exist_ok=True)
            dump.write_text(html, encoding="utf-8")
            raise ValueError(
                f"page structure changed; Intelligence or quota table not found. HTML saved to {dump} for adaptation."
            )

        mi = col_index(main_rows[0], {"model"})
        ii = col_index(main_rows[0], {"intelligence"})
        tok_i = col_index(main_rows[0], {"tok/s"})
        inp_i = col_index(main_rows[0], {"input"})
        out_i = col_index(main_rows[0], {"output"})
        cr_i = col_index(main_rows[0], {"cache read"})
        cw_i = col_index(main_rows[0], {"cache write"})

        qmi = col_index(quota_rows[0], {"model"})
        qi = col_index(quota_rows[0], {"requests / 5 hours"})

        # Monthly credits table == usage_quota
        credits_by_norm: dict[str, Optional[float]] = {}
        for rows in tables:
            hdr = rows[0] if rows else []
            if col_index(hdr, {"monthly credits"}) is not None and col_index(hdr, {"input"}) is not None:
                cmi = col_index(hdr, {"model"})
                crd_i = col_index(hdr, {"monthly credits"})
                if cmi is None or crd_i is None:
                    continue
                for row in rows[1:]:
                    if len(row) <= max(cmi, crd_i):
                        continue
                    name = strip_tags(re.sub(r"<button.*?</button>", "", row[cmi][2], flags=re.DOTALL)) if len(row[cmi][2]) < 2000 else strip_tags(row[cmi][0])
                    m = re.search(r'<a href="/models/[^"]+"[^>]*>(.*?)</a>', row[cmi][2], re.DOTALL)
                    if m:
                        name = strip_tags(m.group(1))
                    elif not name:
                        # Some cells wrap the whole name in a tooltip button;
                        # stripping buttons then loses the name entirely.
                        name = strip_tags(row[cmi][2])
                    credits_by_norm[norm_name(name)] = parse_credits(row[crd_i][0])

        quota_by_norm: dict[str, Optional[int]] = {}
        for row in quota_rows[1:]:
            if len(row) <= max(qmi or 0, qi or 0):
                continue
            qname = strip_tags(row[qmi][0]) if qmi is not None else ""
            quota_by_norm[norm_name(qname)] = parse_quota(row[qi][0]) if qi is not None else None

        universe: dict[str, tuple[str, str]] = {}
        main_by_norm: dict[str, dict] = {}
        skipped_rows = 0
        for row in main_rows[1:]:
            if len(row) <= max(mi or 0, ii or 0):
                skipped_rows += 1
                continue
            name = cell_model_name(row[mi][2])
            slug = row[mi][1] or slugify(name)
            score = row[ii][0]
            universe[name] = (slug, score)
            main_by_norm[norm_name(name)] = {
                "name": name,
                "slug": slug,
                "score": score,
                "tok_raw": row[tok_i][2] if tok_i is not None and len(row) > tok_i else None,
                "input_raw": row[inp_i][2] if inp_i is not None and len(row) > inp_i else None,
                "output_raw": row[out_i][2] if out_i is not None and len(row) > out_i else None,
                "cache_read_raw": row[cr_i][2] if cr_i is not None and len(row) > cr_i else None,
                "cache_write_raw": row[cw_i][2] if cw_i is not None and len(row) > cw_i else None,
            }

        idx = build_score_index(universe)
        score_by_canonical = {c: s for c, s in idx.values()}
        kept: list[tuple[str, str, str]] = []
        for name, (slug, score) in universe.items():
            if re.fullmatch(r"\d+(?:\.\d+)?", score.strip()):
                kept.append((name, slug, score.strip()))
            else:
                inherited = None
                for base in base_candidates(name, idx):
                    inherited = (base, score_by_canonical[base])
                    break
                if inherited:
                    kept.append((name, slug, inherited[1] + "*"))
                else:
                    kept.append((name, slug, "50"))

        models: dict[str, dict] = {}
        for name, slug, _score_text in kept:
            mid = to_model_id(slug)
            if is_excluded_model(mid):
                continue
            raw = main_by_norm.get(norm_name(name), {})
            record = build_goat_fields(
                name, slug,
                price_input_raw=raw.get("input_raw"),
                price_output_raw=raw.get("output_raw"),
                cache_read_raw=raw.get("cache_read_raw"),
                cache_write_raw=raw.get("cache_write_raw"),
                rp5h_val=quota_by_norm.get(norm_name(name)),
                usage_quota_val=credits_by_norm.get(norm_name(name)),
                tok_s_raw=raw.get("tok_raw"),
            )
            if mid in models:
                raise ValueError(
                    f"model id collision after normalization: {mid!r} "
                    f"({models[mid]['name']!r} vs {name!r})"
                )
            models[mid] = record

        if skipped_rows:
            raise ValueError(
                f"{skipped_rows} main-table row(s) skipped for missing columns — page structure drifted"
            )
        if not models:
            raise ValueError("no models resolved from the GOAT plan page")
        expected = load_expected_count(args.reference)
        if expected is not None:
            lo, hi = expected
            if not lo <= len(models) <= hi:
                raise ValueError(
                    f"model count {len(models)} outside expected range [{lo}, {hi}]"
                )

        update_channel(args.extra, "commandcode-goat", models)
    except (OSError, ValueError) as exc:
        dump_ref = error_dump_path(CHANNEL) if error_dump_path(CHANNEL).exists() else None
        persist_error(CHANNEL, "watch_goat.py", str(exc), dump_ref)
        print(f"watch-goat: {exc}", file=sys.stderr)
        return 1

    clear_error(CHANNEL)
    print(f"watch-goat: {len(models)} models -> {args.extra}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
