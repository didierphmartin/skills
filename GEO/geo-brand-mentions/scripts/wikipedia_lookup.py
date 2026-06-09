#!/usr/bin/env python3
"""
wikipedia_lookup.py — Wikipedia + Wikidata API check for the geo-brand-mentions
skill.

Web search alone misses Wikipedia presence regularly — the original Claude
Code geo-brand-mentions explicitly called this out. This script hits the
Wikipedia and Wikidata APIs directly (JSON endpoints, both reliable), then
reports the article URL, the intro extract, the Wikidata Q-number, and a
property count to anchor the LLM's Wikipedia/Wikidata score.

Pyodide-safe: the SynergyAI proxy at /gpt/backend/api/v1/fetch-url is
content-type agnostic (returns body in data.html regardless of Content-Type),
so the JSON API responses come through cleanly.

Usage:
    python wikipedia_lookup.py "Anthropic"
    python wikipedia_lookup.py "Anthropic" --founder "Dario Amodei"
    python wikipedia_lookup.py "Dario Amodei" --founder-only
"""

from __future__ import annotations

import os
import argparse
import asyncio
import json
import re
import sys
import time
from pathlib import Path
from urllib.parse import quote_plus


DEFAULT_OUTPUT_DIR = (
    Path(os.environ.get("SYNERGYAI_OUTPUT_DIR", "/outputs"))
    if sys.platform == "emscripten"
    else Path.home() / "Documents" / "synergyAI" / "outputs"
)
PROXY_ENDPOINT = "/gpt/backend/api/v1/fetch-url"

BROWSER_UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
)

WIKI_API = "https://en.wikipedia.org/w/api.php"
WIKIDATA_API = "https://www.wikidata.org/w/api.php"


# ---------------------------------------------------------------------------
# Fetch helpers (dual Pyodide/CPython, same proven pattern)
# ---------------------------------------------------------------------------

def _is_pyodide() -> bool:
    return sys.platform == "emscripten"


async def _fetch_pyodide(url: str) -> dict | None:
    try:
        import pyodide.http  # type: ignore[import-not-found]
    except ImportError:
        return None
    headers = {"Content-Type": "application/json"}
    try:
        from js import window  # type: ignore[import-not-found]
        tok = getattr(getattr(window, "authManager", None), "token", None)
        if tok:
            headers["Authorization"] = f"Bearer {tok}"
    except Exception:
        pass
    try:
        resp = await pyodide.http.pyfetch(
            PROXY_ENDPOINT, method="POST", headers=headers,
            body=json.dumps({"url": url}),
        )
        payload = json.loads(await resp.string())
    except Exception:
        return None
    if not payload.get("success"):
        data = payload.get("data", {}) or {}
        status = data.get("status") or data.get("upstream_status") or 0
        return {"body": "", "status": status, "final_url": data.get("final_url") or url}
    data = payload.get("data", {}) or {}
    return {
        "body": data.get("html", "") or "",
        "status": data.get("status", 0) or 0,
        "final_url": data.get("final_url", "") or url,
    }


def _fetch_urllib(url: str) -> dict | None:
    import gzip
    import ssl
    import urllib.error
    import urllib.request

    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    req = urllib.request.Request(url, headers={
        "User-Agent": BROWSER_UA,
        "Accept": "application/json,*/*;q=0.8",
        "Accept-Encoding": "gzip",
    })
    try:
        with urllib.request.urlopen(req, timeout=15, context=ctx) as r:
            body_bytes = r.read()
            status = r.status
            final_url = r.url
            encoding = (r.headers.get("Content-Encoding") or "").lower()
    except urllib.error.HTTPError as e:
        return {"body": "", "status": e.code, "final_url": url}
    except Exception:
        return None
    if encoding == "gzip":
        try:
            body_bytes = gzip.decompress(body_bytes)
        except OSError:
            pass
    return {
        "body": body_bytes.decode("utf-8", errors="replace"),
        "status": status,
        "final_url": final_url,
    }


async def fetch_json(url: str) -> dict | None:
    """Fetch a URL and parse the body as JSON. Returns None on any failure."""
    if _is_pyodide():
        resp = await _fetch_pyodide(url)
    else:
        loop = asyncio.get_running_loop()
        resp = await loop.run_in_executor(None, _fetch_urllib, url)
    if not resp or not resp.get("body"):
        return None
    try:
        return json.loads(resp["body"])
    except json.JSONDecodeError:
        return None


# ---------------------------------------------------------------------------
# Wikipedia API
# ---------------------------------------------------------------------------

def _match_confidence(query: str, title: str) -> str:
    """Heuristic: how confident are we that this Wikipedia title is about
    the searched brand?"""
    q = query.lower().strip()
    t = title.lower().strip()
    if t == q:
        return "exact"
    if t.startswith(q) or q.startswith(t):
        return "near-exact"
    if q in t or t in q:
        return "partial"
    # Token overlap
    q_tokens = set(re.findall(r"\w+", q))
    t_tokens = set(re.findall(r"\w+", t))
    if q_tokens and (q_tokens & t_tokens) == q_tokens:
        return "partial"
    return "weak"


async def lookup_wikipedia(query: str) -> dict:
    """Search Wikipedia for a query and return the best-matching article's
    metadata + intro extract."""
    out = {
        "query": query,
        "exists": False,
        "title": None,
        "url": None,
        "match_confidence": "none",
        "extract": None,
        "extract_words": 0,
        "snippet": None,
    }

    # 1. Search
    search_url = (f"{WIKI_API}?action=query&list=search&srsearch="
                   f"{quote_plus(query)}&srlimit=3&format=json&origin=*")
    search = await fetch_json(search_url)
    if not search:
        out["error"] = "Wikipedia search API call failed"
        return out
    results = (search.get("query") or {}).get("search") or []
    if not results:
        return out

    best = results[0]
    title = best.get("title", "")
    out["title"] = title
    out["url"] = f"https://en.wikipedia.org/wiki/{title.replace(' ', '_')}"
    out["match_confidence"] = _match_confidence(query, title)
    out["snippet"] = re.sub(r"<.*?>", "", best.get("snippet", ""))

    # Only treat as "exists" if match confidence is decent.
    if out["match_confidence"] in ("exact", "near-exact", "partial"):
        out["exists"] = True

    # 2. Intro extract for the best match (regardless of confidence — even a
    # weak match's extract helps the LLM judge)
    extract_url = (f"{WIKI_API}?action=query&prop=extracts&exintro=true"
                    f"&explaintext=true&titles={quote_plus(title)}"
                    f"&format=json&origin=*&redirects=1")
    ex = await fetch_json(extract_url)
    if ex:
        pages = (ex.get("query") or {}).get("pages") or {}
        for _, page in pages.items():
            extract = page.get("extract")
            if extract:
                out["extract"] = extract[:1500]
                out["extract_words"] = len(re.findall(r"\b\w+\b", extract))
                break

    return out


# ---------------------------------------------------------------------------
# Wikidata API
# ---------------------------------------------------------------------------

async def lookup_wikidata(query: str) -> dict:
    """Search Wikidata for a query and return the best-matching entity's
    QID, label, description, and property count."""
    out = {
        "query": query,
        "exists": False,
        "qid": None,
        "label": None,
        "description": None,
        "url": None,
        "property_count": 0,
        "match_confidence": "none",
    }

    search_url = (f"{WIKIDATA_API}?action=wbsearchentities&search="
                   f"{quote_plus(query)}&language=en&limit=3&format=json"
                   f"&origin=*")
    search = await fetch_json(search_url)
    if not search:
        out["error"] = "Wikidata search API call failed"
        return out
    results = search.get("search") or []
    if not results:
        return out

    best = results[0]
    qid = best.get("id")
    label = best.get("label", "")
    desc = best.get("description", "")
    out["qid"] = qid
    out["label"] = label
    out["description"] = desc
    out["url"] = f"https://www.wikidata.org/wiki/{qid}" if qid else None
    out["match_confidence"] = _match_confidence(query, label) if label else "none"
    if out["match_confidence"] in ("exact", "near-exact", "partial"):
        out["exists"] = True

    # Property count — fetch the full entity to count claims
    if qid:
        entity_url = (f"{WIKIDATA_API}?action=wbgetentities&ids={qid}"
                       f"&props=claims&format=json&origin=*")
        ent = await fetch_json(entity_url)
        if ent:
            entities = ent.get("entities") or {}
            data = entities.get(qid) or {}
            claims = data.get("claims") or {}
            out["property_count"] = len(claims)

    return out


# ---------------------------------------------------------------------------
# Rendering
# ---------------------------------------------------------------------------

def render(result: dict) -> str:
    L = [
        f"# Wikipedia / Wikidata lookup",
        "",
        f"**Fetched:** {result['fetched_at']}",
        "",
    ]

    def render_subject(label: str, data: dict) -> list[str]:
        wp = data["wikipedia"]
        wd = data["wikidata"]
        block = [f"## {label}: {data['query']!r}", ""]

        # Wikipedia
        block.append("### Wikipedia")
        block.append("")
        if wp["exists"]:
            block.append(f"- ✅ Article found: [{wp['title']}]({wp['url']})")
            block.append(f"- Match confidence: **{wp['match_confidence']}**")
            block.append(f"- Extract length (intro): {wp['extract_words']} words")
            if wp["extract"]:
                preview = wp["extract"][:500]
                if len(wp["extract"]) > 500:
                    preview += "…"
                block.append("")
                block.append("**Intro extract:**")
                block.append("")
                block.append(f"> {preview}")
        else:
            if wp.get("title") and wp.get("match_confidence") == "weak":
                block.append(f"- ⚠️ No high-confidence article found. Closest "
                              f"match was [{wp['title']}]({wp['url']}) but the "
                              "title does not clearly correspond to the brand.")
                if wp.get("snippet"):
                    block.append(f"- Snippet: _{wp['snippet'][:200]}_")
            elif "error" in wp:
                block.append(f"- ❌ API error: {wp['error']}")
            else:
                block.append("- ❌ No Wikipedia article found.")
        block.append("")

        # Wikidata
        block.append("### Wikidata")
        block.append("")
        if wd["exists"]:
            block.append(f"- ✅ Entity found: [{wd['qid']}]({wd['url']}) — "
                          f"**{wd['label']}**")
            block.append(f"- Description: {wd['description'] or '(none)'}")
            block.append(f"- Match confidence: **{wd['match_confidence']}**")
            block.append(f"- Property count: **{wd['property_count']}**")
        else:
            if wd.get("qid") and wd.get("match_confidence") == "weak":
                block.append(f"- ⚠️ No high-confidence entity. Closest match: "
                              f"[{wd['qid']}]({wd['url']}) — {wd['label']!r} "
                              f"({wd['description'] or 'no description'}) — "
                              "doesn't clearly correspond to the brand.")
            elif "error" in wd:
                block.append(f"- ❌ API error: {wd['error']}")
            else:
                block.append("- ❌ No Wikidata entity found.")
        block.append("")
        return block

    if result["brand"]:
        L.extend(render_subject("Brand", result["brand"]))
    if result["founder"]:
        L.extend(render_subject("Founder", result["founder"]))

    # Quick aggregate hint for the LLM's scoring
    L += [
        "## Scoring hint for the LLM",
        "",
        "Map this output to the Wikipedia/Wikidata sub-score using the rubric "
        "in SKILL.md:",
        "",
        "- High-confidence article + Wikidata + ≥10 properties + Founder "
        "article → 90-100",
        "- Article + Wikidata + 3-9 properties → 70-89",
        "- Stub article + basic Wikidata → 50-69",
        "- No article but Wikidata exists OR weak/partial match → 30-49",
        "- Nothing found → 0-9",
        "",
    ]

    return "\n".join(L).rstrip() + "\n"


# ---------------------------------------------------------------------------
# Output paths
# ---------------------------------------------------------------------------

def resolve_output_paths(output: Path | None) -> tuple[Path, Path]:
    if output is None:
        base = DEFAULT_OUTPUT_DIR
        return base / "GEO-BRAND-MENTIONS-WIKI.md", base / "GEO-BRAND-MENTIONS-WIKI.json"
    if output.suffix == "" or output.is_dir():
        return output / "GEO-BRAND-MENTIONS-WIKI.md", output / "GEO-BRAND-MENTIONS-WIKI.json"
    return output, output.with_suffix(".json")


# ---------------------------------------------------------------------------
# Main pipeline
# ---------------------------------------------------------------------------

async def amain(args) -> int:
    brand = args.brand
    if not brand:
        print("ERROR: a brand name is required.", file=sys.stderr)
        return 2

    print(f"Looking up Wikipedia + Wikidata for {brand!r}…", file=sys.stderr)
    if args.founder:
        print(f"  Also looking up founder {args.founder!r}…", file=sys.stderr)

    brand_block = None
    founder_block = None

    if args.founder_only:
        founder_block = {
            "query": brand,
            "wikipedia": await lookup_wikipedia(brand),
            "wikidata": await lookup_wikidata(brand),
        }
    else:
        wp_brand, wd_brand = await asyncio.gather(
            lookup_wikipedia(brand),
            lookup_wikidata(brand),
        )
        brand_block = {"query": brand, "wikipedia": wp_brand, "wikidata": wd_brand}
        if args.founder:
            wp_f, wd_f = await asyncio.gather(
                lookup_wikipedia(args.founder),
                lookup_wikidata(args.founder),
            )
            founder_block = {"query": args.founder, "wikipedia": wp_f, "wikidata": wd_f}

    result = {
        "fetched_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "brand": brand_block,
        "founder": founder_block,
    }

    report = render(result)
    md_path, json_path = resolve_output_paths(args.output)
    md_path.parent.mkdir(parents=True, exist_ok=True)
    md_path.write_text(report, encoding="utf-8")
    json_path.write_text(json.dumps(result, indent=2, ensure_ascii=False) + "\n",
                         encoding="utf-8")

    # Full report to stdout — what run_skill_script returns to the model.
    print(report)

    brand_summary = "—"
    if brand_block:
        b_wp = "✓" if brand_block["wikipedia"]["exists"] else "✗"
        b_wd = "✓" if brand_block["wikidata"]["exists"] else "✗"
        brand_summary = f"brand WP={b_wp} WD={b_wd}"
    founder_summary = ""
    if founder_block:
        f_wp = "✓" if founder_block["wikipedia"]["exists"] else "✗"
        f_wd = "✓" if founder_block["wikidata"]["exists"] else "✗"
        founder_summary = f" founder WP={f_wp} WD={f_wd}"
    print(f"[geo-brand-mentions] {brand_summary}{founder_summary} — wrote {md_path}",
          file=sys.stderr)
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Wikipedia + Wikidata API lookup for the geo-brand-mentions skill.")
    parser.add_argument("brand", nargs="?", default=None,
                        help="Brand name (positional). With --founder-only, "
                             "this is treated as the founder's name instead.")
    parser.add_argument("--founder", default=None,
                        help="Also look up the founder by name.")
    parser.add_argument("--founder-only", action="store_true",
                        help="Treat the positional arg as a person's name and "
                             "skip the brand lookup (useful when auditing an "
                             "individual creator/thought-leader).")
    parser.add_argument("--source", default=None,
                        help="Runner pre-fetch source label (ignored — the "
                             "script self-fetches via the API).")
    parser.add_argument("-o", "--output", type=Path, default=None,
                        help="Output path. Default: "
                             "~/Documents/synergyAI/outputs/ (/outputs/ in "
                             "Pyodide).")
    args = parser.parse_args(argv)

    return asyncio.run(amain(args))


if __name__ == "__main__":
    sys.exit(main())
