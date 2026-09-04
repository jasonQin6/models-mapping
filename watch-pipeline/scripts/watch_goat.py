#!/usr/bin/env python3
"""Scrape https://commandcode.ai/docs/plans/goat and emit data/goat-models.json.

Output: single provider envelope ``{"commandcode-goat": {id, name, api, npm, env, doc, models}}``
shaped like data/opencode-go-models.json. Each model is keyed by model_id (id).

Per-model contract
- GOAT-claimed: id, name, cost (GOAT deal pricing) and extra: {rp5h,
  usage_quota (==Monthly credits), tok_s}. cost/rp5h/usage_quota are the
  GOAT channel. Intelligence scoring drives model selection only and is
  not stored.
- Enriched by fallback merge: if a model_id exists in data/all_models.json,
  the record is filled with that upstream's static fields (attachment,
  description, family, limit, modalities, reasoning, etc.). If absent,
  only GOAT fields remain (such GOAT-exclusive variants are dropped).
- Never duplicate model_id as both ``id`` and ``model_id``; canonical key is
  ``id``. All discrimination is by model_id.

Stdlib only. Pipeline entrypoint; see .github/workflows/watch-pipeline.yml.
Usage: watch_goat.py [--url URL] [--html FILE] [--all-models PATH] [--output PATH]
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import tempfile
import urllib.request
from pathlib import Path
from typing import Any, Iterator, Mapping, Optional

from error_state import clear_error, error_dump_path, persist_error

sys.path.insert(0, str(Path(__file__).resolve().parent))

REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_ALL_MODELS = REPO_ROOT / "data" / "all_models.json"
DEFAULT_OUT = REPO_ROOT / "data" / "goat-models.json"
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
    t = strip_tags(raw) if "<" in raw else raw.strip()
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


def _norm_key(name: str) -> str:
    return norm_name(name)


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
) -> tuple[dict, dict]:
    """Return (upstream_patch, goat_extra) for one model.

    upstream_patch contains only GOAT-channel fields that should appear at
    models.<model_id> top level (id, name, cost). goat_extra is nested
    under extra: {rp5h, usage_quota, tok_s} — the subset with downstream
    consumers (remark fields in axonhub-catalog-sync; tok/s is GOAT-only).
    Monthly credits map to usage_quota; intelligence scoring is not stored.
    Tok/s is in the main table; limit/context is NOT here — it belongs to
    the upstream static schema and is filled via fallback, not scraped.
    """
    mid = to_model_id(slug)
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

    patch: dict = {"id": mid, "name": name}
    if cost:
        patch["cost"] = cost
    # tok_s: GOAT-only, not in models.dev
    tok_s: Optional[int] = None
    if tok_s_raw is not None:
        raw_t = strip_tags(tok_s_raw) if "<" in tok_s_raw else str(tok_s_raw).strip()
        if raw_t and raw_t not in ("—", "-", ""):
            try:
                tok_s = int(float(raw_t))
            except ValueError:
                tok_s = None
    extra: dict = {
        "rp5h": rp5h_val,
        "usage_quota": usage_quota_val,
        "tok_s": tok_s,
    }
    return patch, extra


def load_upstream_lookup(all_models: Path) -> dict[str, dict]:
    """Return bare model_id -> record from data/all_models.json only (single source).

    all_models keys are vendor/model_id (e.g. deepseek/deepseek-v4-flash);
    index by bare id (rec["id"]) for GOAT lookup. If a GOAT variant has no
    bare-id entry (e.g. deepseek-v4-flash-fast without deepseek-v4-flash),
    it is already filtered before calling this — no variant fallback needed.
    """
    path = all_models
    if not path.exists():
        return {}
    try:
        doc = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}
    lookup: dict[str, dict] = {}
    if not isinstance(doc, dict):
        return lookup
    for key, rec in doc.items():
        if not isinstance(rec, dict):
            continue
        vendor_mid = rec.get("id") or key
        if not isinstance(vendor_mid, str) or not vendor_mid.strip():
            continue
        vendor_mid = vendor_mid.strip()
        bare = vendor_mid.split("/")[-1]
        if bare and bare not in lookup:
            lookup[bare] = dict(rec)
        # also index by full vendor/mid for completeness
        if vendor_mid not in lookup:
            lookup[vendor_mid] = dict(rec)
    return lookup


def write_json_atomic(path: Path, payload: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(payload, fh, ensure_ascii=False, indent=2, sort_keys=False)
            fh.write("\n")
        os.replace(tmp, str(path))
    except Exception:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def main(argv: Optional[list[str]] = None) -> int:
    ap = argparse.ArgumentParser(description="Scrape GOAT plan and emit data/goat-models.json (api.json-shaped)")
    ap.add_argument("--url", default=URL)
    ap.add_argument("--html", help="read HTML from local file instead of fetching (for testing)")
    ap.add_argument(
        "--all-models",
        type=Path,
        default=DEFAULT_ALL_MODELS,
        help="Path to data/all_models.json upstream snapshot",
    )
    ap.add_argument(
        "--output",
        "-o",
        type=Path,
        default=DEFAULT_OUT,
        help="Output JSON path (default: data/goat-models.json)",
    )
    ap.add_argument(
        "--dump-html",
        type=Path,
        default=None,
        help="Where to dump HTML when the page structure changes (default: watch-pipeline/reference/goat/failed-page.html)",
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

        # Monthly credits table == retention
        retention_by_norm: dict[str, Optional[float]] = {}
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
                    retention_by_norm[_norm_key(name)] = parse_credits(row[crd_i][0])

        quota_by_norm: dict[str, Optional[int]] = {}
        for row in quota_rows[1:]:
            if len(row) <= max(qmi or 0, qi or 0):
                continue
            qname = strip_tags(row[qmi][0]) if qmi is not None else ""
            quota_by_norm[_norm_key(qname)] = parse_quota(row[qi][0]) if qi is not None else None

        universe: dict[str, tuple[str, str]] = {}
        main_by_norm: dict[str, dict] = {}
        for row in main_rows[1:]:
            if len(row) <= max(mi or 0, ii or 0):
                continue
            name = cell_model_name(row[mi][2])
            slug = row[mi][1] or slugify(name)
            score = row[ii][0]
            universe[name] = (slug, score)
            main_by_norm[_norm_key(name)] = {
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
        kept: list[tuple[str, str, str, str]] = []
        for name, (slug, score) in universe.items():
            if re.fullmatch(r"\d+(?:\.\d+)?", score.strip()):
                kept.append((name, slug, score.strip(), "官方评分"))
            else:
                inherited = None
                for base in base_candidates(name, idx):
                    inherited = (base, score_by_canonical[base])
                    break
                if inherited:
                    kept.append((name, slug, inherited[1] + "*", f"继承自 {inherited[0]}（not yet scored）"))
                else:
                    kept.append((name, slug, "50", "无前代分数，默认赋 50"))

        upstream = load_upstream_lookup(args.all_models)

        models: dict[str, dict] = {}
        for name, slug, _score_text, _remark in kept:
            nk = _norm_key(name)
            raw = main_by_norm.get(nk, {})
            patch, extra_fields = build_goat_fields(
                name, slug,
                price_input_raw=raw.get("input_raw"),
                price_output_raw=raw.get("output_raw"),
                cache_read_raw=raw.get("cache_read_raw"),
                cache_write_raw=raw.get("cache_write_raw"),
                rp5h_val=quota_by_norm.get(nk),
                usage_quota_val=retention_by_norm.get(nk),
                tok_s_raw=raw.get("tok_raw"),
            )
            mid = patch["id"]
            if mid not in upstream:
                # GOAT-exclusive without base upstream — drop per Q8
                continue
            base = dict(upstream[mid])
            merged = dict(base)
            merged.update(patch)
            merged["extra"] = extra_fields
            models[mid] = merged

        provider = {
            "id": "commandcode-goat",
            "name": "Command Code GOAT",
            "api": "https://api.commandcode.ai",
            "npm": "@ai-sdk/openai-compatible",
            "env": ["COMMANDCODE_API_KEY"],
            "doc": "https://commandcode.ai/docs/plans/goat",
            "models": dict(sorted(models.items())),
        }

        write_json_atomic(args.output, {"commandcode-goat": provider})
    except (OSError, ValueError) as exc:
        dump_ref = error_dump_path(CHANNEL) if error_dump_path(CHANNEL).exists() else None
        persist_error(CHANNEL, "watch_goat.py", str(exc), dump_ref)
        print(f"watch-goat: {exc}", file=sys.stderr)
        return 1

    clear_error(CHANNEL)
    print(f"watch-goat: {len(models)} models -> {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
