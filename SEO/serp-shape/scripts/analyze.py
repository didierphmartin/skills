#!/usr/bin/env python3
"""
serp-shape/analyze.py — compare a webpage to the top-10 Google results for
a keyword.

Runs in two environments:
  - CPython (Claude Desktop, CLI, anything with stdlib + beautifulsoup4):
    calls SerpAPI and fetches pages directly via urllib.
  - Pyodide (in-browser AI Assistant): CORS blocks direct SerpAPI calls,
    so the script routes HTTP through the host's same-origin proxy at
    /gpt/backend/api/v1/fetch-url. Detection via sys.platform == 'emscripten'.

The SerpAPI key is read from `<skill_dir>/.env` (SERPAPI_API_KEY=...).
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import re
import statistics
import sys
from collections import Counter
from pathlib import Path
from urllib.parse import urlparse, urlencode

from bs4 import BeautifulSoup

BROWSER_UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
)
DEFAULT_OUTPUT_DIR = Path.home() / "Documents" / "synergyAI" / "outputs"
PROXY_ENDPOINT = "/gpt/backend/api/v1/fetch-url"

# Auto-detect for the article container (same list as ai-search-audit).
AUTO_ROOT_SELECTORS = [
    "main", "article", "[role='main']",
    "#content", "#main",
    ".post-content", ".entry-content", ".article-content",
]

STOPWORDS = {
    "the", "and", "for", "with", "from", "your", "this", "that", "what",
    "how", "why", "are", "you", "our", "their", "his", "her", "its",
    "into", "have", "has", "had", "will", "can", "all", "any", "out",
    "but", "not", "use", "using", "best", "top", "vs", "—", "-",
    "le", "la", "les", "des", "une", "un", "et", "ou", "pour", "avec",
    "dans", "sur", "par", "ce", "cette", "ces", "qui", "que", "quoi",
    "comment", "pourquoi",
}


# ---------------------------------------------------------------------------
# Env / SerpAPI key
# ---------------------------------------------------------------------------

def load_env(skill_dir: Path) -> dict:
    """Read KEY=VALUE pairs from <skill_dir>/.env. Returns empty dict if absent."""
    env_path = skill_dir / ".env"
    if not env_path.exists():
        return {}
    out = {}
    for line in env_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if "=" not in line:
            continue
        k, v = line.split("=", 1)
        out[k.strip()] = v.strip().strip('"').strip("'")
    return out


def _is_pyodide() -> bool:
    return sys.platform == "emscripten"


# ---------------------------------------------------------------------------
# Unified HTTP fetcher — branches on environment
# ---------------------------------------------------------------------------

async def fetch_url(url: str) -> str:
    """Fetch a URL and return its decoded body as a string.

    - CPython: direct urllib request, gzip-aware.
    - Pyodide: POST to the host's fetch-url proxy (same-origin XHR avoids CORS).
    """
    if _is_pyodide():
        import pyodide.http
        # The proxy endpoint requires JWT auth (same as the rest of the app).
        # Pull the token from window.authManager — same pattern the bridge
        # uses internally for its own /api/v1/fetch-url calls.
        headers = {"Content-Type": "application/json"}
        try:
            from js import window  # type: ignore[import-not-found]
            tok = getattr(getattr(window, "authManager", None), "token", None)
            if tok:
                headers["Authorization"] = f"Bearer {tok}"
        except Exception:
            pass  # CPython-shaped fallback path; will likely fail without auth.
        resp = await pyodide.http.pyfetch(
            PROXY_ENDPOINT,
            method="POST",
            headers=headers,
            body=json.dumps({"url": url}),
        )
        text = await resp.string()
        try:
            payload = json.loads(text)
        except json.JSONDecodeError:
            raise RuntimeError(f"proxy returned non-JSON: {text[:200]}")
        if not payload.get("success"):
            raise RuntimeError(f"proxy fetch failed for {url}: {payload.get('error') or payload}")
        return payload.get("data", {}).get("html", "")

    # CPython path
    import gzip
    import ssl
    import urllib.request

    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    req = urllib.request.Request(url, headers={
        "User-Agent": BROWSER_UA,
        "Accept": "text/html,application/xhtml+xml,application/json;q=0.9,*/*;q=0.8",
        "Accept-Encoding": "gzip",
    })
    with urllib.request.urlopen(req, timeout=30, context=ctx) as r:
        body = r.read()
        encoding = (r.headers.get("Content-Encoding") or "").lower()
        ctype = r.headers.get("Content-Type") or ""
    if encoding == "gzip":
        body = gzip.decompress(body)
    # crude charset detection
    charset = None
    m = re.search(r"charset=([\w\-]+)", ctype, re.I)
    if m:
        charset = m.group(1).lower()
    if not charset:
        m = re.search(rb'<meta[^>]+charset=["\']?([\w\-]+)', body[:4096], re.I)
        if m:
            charset = m.group(1).decode("ascii", errors="replace").lower()
    return body.decode(charset or "utf-8", errors="replace")


# ---------------------------------------------------------------------------
# SerpAPI client
# ---------------------------------------------------------------------------

async def fetch_serp(keyword: str, api_key: str, depth: int,
                     location: str | None, gl: str, hl: str,
                     google_domain: str) -> dict:
    """Call SerpAPI for a keyword. Returns the raw JSON response.

    - `location` (optional): geo-targets local results. Omit for the broadest,
      least-geo-biased SERP — matches what the Google web UI returns when
      no location is set in your account.
    - `gl`: country code (e.g. 'us', 'fr', 'ca'). Affects organic ranking.
    - `hl`: language code (e.g. 'en', 'fr'). Affects language preference.
    - `google_domain`: which Google domain (default 'google.com' = international).
    """
    params = {
        "engine": "google",
        "q": keyword,
        "num": min(20, max(1, depth)),
        "gl": gl,
        "hl": hl,
        "google_domain": google_domain,
        "api_key": api_key,
    }
    if location:
        params["location"] = location
    url = "https://serpapi.com/search?" + urlencode(params)
    body = await fetch_url(url)
    try:
        return json.loads(body)
    except json.JSONDecodeError as e:
        raise RuntimeError(f"SerpAPI returned non-JSON (first 200 chars): {body[:200]}") from e


# ---------------------------------------------------------------------------
# HTML feature extraction (copied verbatim from ai-search-audit/audit.py
# patterns; will move to a shared _parsers.py in a future refactor)
# ---------------------------------------------------------------------------

def pick_root(soup: BeautifulSoup, main_selector: str | None = None):
    if main_selector:
        picked = soup.select_one(main_selector)
        if picked is not None:
            return picked
    for sel in AUTO_ROOT_SELECTORS:
        picked = soup.select_one(sel)
        if picked is not None and picked.get_text(strip=True):
            return picked
    return soup.body if soup.body is not None else soup


def extract_jsonld_types(soup: BeautifulSoup) -> list[str]:
    """Return all @type values found in JSON-LD blocks (flattened)."""
    types: list[str] = []
    for block in soup.find_all("script", type="application/ld+json"):
        if not block.string:
            continue
        try:
            data = json.loads(block.string)
        except json.JSONDecodeError:
            continue
        items = data if isinstance(data, list) else [data]
        for item in items:
            if not isinstance(item, dict):
                continue
            t = item.get("@type")
            if t:
                types.extend(t if isinstance(t, list) else [t])
            graph = item.get("@graph")
            if isinstance(graph, list):
                for sub in graph:
                    if isinstance(sub, dict) and sub.get("@type"):
                        t = sub["@type"]
                        types.extend(t if isinstance(t, list) else [t])
    return types


def has_faq_schema(types: list[str]) -> bool:
    return any(t == "FAQPage" or (isinstance(t, str) and "FAQ" in t) for t in types)


def extract_page_features(html: str, source_url: str) -> dict:
    """Per-page feature record used by both SERP-shape aggregation and the
    user-page comparison."""
    soup = BeautifulSoup(html, "html.parser")
    root = pick_root(soup)
    title = soup.title.get_text(strip=True) if soup.title else ""
    md = soup.find("meta", attrs={"name": "description"})
    meta_desc = (md.get("content") or "").strip() if md else ""
    h1 = soup.h1.get_text(" ", strip=True) if soup.h1 else ""
    h2s = [h.get_text(" ", strip=True) for h in root.find_all("h2")]
    h3s = [h.get_text(" ", strip=True) for h in root.find_all("h3")]
    word_count = len(root.get_text(" ", strip=True).split())
    schema_types = extract_jsonld_types(soup)
    has_faq = has_faq_schema(schema_types) or bool(soup.find("script", string=re.compile(r'"@type"\s*:\s*"FAQPage"')))
    list_count = len(root.find_all(["ul", "ol"]))
    table_count = len(root.find_all("table"))
    image_count = len(root.find_all("img"))
    return {
        "url": source_url,
        "title": title,
        "title_len": len(title),
        "meta_desc_len": len(meta_desc),
        "h1": h1,
        "h2_list": h2s,
        "h3_list": h3s,
        "h2_count": len(h2s),
        "word_count": word_count,
        "schema_types": schema_types,
        "has_faq_schema": has_faq,
        "list_count": list_count,
        "table_count": table_count,
        "image_count": image_count,
    }


# ---------------------------------------------------------------------------
# SerpAPI shape aggregation
# ---------------------------------------------------------------------------

def detect_snippet_format(answer_box: dict | None) -> str | None:
    if not isinstance(answer_box, dict):
        return None
    if "list" in answer_box and answer_box.get("list"):
        return "list"
    if "table" in answer_box and answer_box.get("table"):
        return "table"
    if "snippet" in answer_box or "answer" in answer_box:
        return "paragraph"
    return None


def top_phrases(strings: list[str], top_n: int = 10) -> list[tuple[str, int]]:
    """Most common 2-3 word phrases across strings (stopword-filtered)."""
    counter: Counter = Counter()
    for s in strings:
        tokens = [t.lower() for t in re.findall(r"[a-zA-ZÀ-ÿ0-9]+", s) if len(t) > 2]
        tokens = [t for t in tokens if t not in STOPWORDS]
        for i in range(len(tokens) - 1):
            counter[tokens[i] + " " + tokens[i + 1]] += 1
        for i in range(len(tokens) - 2):
            counter[tokens[i] + " " + tokens[i + 1] + " " + tokens[i + 2]] += 1
    return counter.most_common(top_n)


def compute_shape(records: list[dict], serp: dict) -> dict:
    if not records:
        return {}
    wc = [r["word_count"] for r in records if r["word_count"] > 0]
    h2 = [r["h2_count"] for r in records]
    titles = [r["title"] for r in records if r["title"]]
    h2_phrases = []
    for r in records:
        h2_phrases.extend(r["h2_list"])

    all_schema: Counter = Counter()
    for r in records:
        all_schema.update(r["schema_types"])

    return {
        "n_pages": len(records),
        "median_word_count": int(statistics.median(wc)) if wc else 0,
        "median_h2_count": int(statistics.median(h2)) if h2 else 0,
        "median_title_len": int(statistics.median([r["title_len"] for r in records])),
        "median_list_count": int(statistics.median([r["list_count"] for r in records])),
        "pct_with_faq_schema": round(sum(1 for r in records if r["has_faq_schema"]) / len(records), 2),
        "pct_with_table": round(sum(1 for r in records if r["table_count"] > 0) / len(records), 2),
        "pct_with_list": round(sum(1 for r in records if r["list_count"] > 0) / len(records), 2),
        "dominant_schema": all_schema.most_common(5),
        "snippet_format": detect_snippet_format(serp.get("answer_box")),
        "paa_questions": [q.get("question", "") for q in serp.get("related_questions", []) if q.get("question")],
        "related_searches": [s.get("query", "") for s in serp.get("related_searches", []) if s.get("query")],
        "knowledge_graph_present": bool(serp.get("knowledge_graph")),
        "videos_present": bool(serp.get("inline_videos")),
        "news_present": bool(serp.get("top_stories")),
        "common_title_phrases": top_phrases(titles, top_n=8),
        "common_h2_phrases": top_phrases(h2_phrases, top_n=10),
    }


# ---------------------------------------------------------------------------
# Gap analysis (user's page vs aggregate)
# ---------------------------------------------------------------------------

def compute_gaps(your: dict, shape: dict) -> list[tuple[str, str, str]]:
    """Return (priority, category, message) tuples — sorted by priority."""
    gaps: list[tuple[str, str, str]] = []

    # --- High priority: schema and format mismatches
    if shape.get("pct_with_faq_schema", 0) >= 0.4 and not your["has_faq_schema"]:
        gaps.append(("high", "schema",
            f"Add FAQPage JSON-LD — {int(shape['pct_with_faq_schema']*100)}% of ranking pages have FAQ rich snippets."))

    snippet_fmt = shape.get("snippet_format")
    if snippet_fmt == "list" and your["list_count"] == 0:
        gaps.append(("high", "format",
            "Google's featured snippet for this query is a list; your page has none. Add a numbered or bulleted list near the top."))
    if snippet_fmt == "table" and your["table_count"] == 0:
        gaps.append(("high", "format",
            "Google's featured snippet for this query is a table; your page has none. Consider adding a comparison table."))

    # Dominant schema mismatch
    if shape["dominant_schema"]:
        top_schema, top_count = shape["dominant_schema"][0]
        if top_count >= max(2, shape["n_pages"] // 3) and top_schema not in your["schema_types"]:
            gaps.append(("high", "schema",
                f"Top ranking pages use `{top_schema}` schema ({top_count}/{shape['n_pages']}); your page does not. Consider switching."))

    # --- Medium priority: content depth
    if shape["median_word_count"] > 0 and your["word_count"] < shape["median_word_count"] * 0.6:
        gaps.append(("medium", "depth",
            f"Word count {your['word_count']} vs SERP median {shape['median_word_count']}. Expand to at least the median."))

    if shape["median_h2_count"] >= 4 and your["h2_count"] < shape["median_h2_count"] - 1:
        gaps.append(("medium", "structure",
            f"Section count {your['h2_count']} H2s vs SERP median {shape['median_h2_count']}. Add more sections."))

    # PAA questions not covered as H2s
    your_h2_text = " ".join(your["h2_list"]).lower()
    missing_paa = []
    for q in shape.get("paa_questions", []):
        toks = [t for t in re.findall(r"[a-zA-ZÀ-ÿ]+", q.lower()) if len(t) > 4 and t not in STOPWORDS]
        if toks and not any(t in your_h2_text for t in toks):
            missing_paa.append(q)
    if missing_paa:
        gaps.append(("medium", "topics",
            "Add H2s answering these 'People Also Ask' questions: " + " | ".join(missing_paa[:5])))

    # --- Low priority: minor structure
    if shape.get("pct_with_list", 0) >= 0.5 and your["list_count"] == 0:
        gaps.append(("low", "format",
            f"{int(shape['pct_with_list']*100)}% of ranking pages have at least one list; yours has none."))

    if your["title_len"] > shape["median_title_len"] + 20:
        gaps.append(("low", "title",
            f"Title length {your['title_len']} chars vs SERP median {shape['median_title_len']}. Trim if SEO-truncated."))

    pri_order = {"high": 0, "medium": 1, "low": 2}
    gaps.sort(key=lambda g: pri_order.get(g[0], 9))
    return gaps


# ---------------------------------------------------------------------------
# Report rendering
# ---------------------------------------------------------------------------

def render_report(keyword: str, your: dict, shape: dict, records: list[dict],
                  gaps: list[tuple[str, str, str]], skipped: list[tuple[str, str]] | None = None) -> str:
    from datetime import datetime, timezone
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    skipped = skipped or []
    lines: list[str] = []
    lines.append(f"# SERP Shape — \"{keyword}\"")
    lines.append("")
    lines.append(f"**Your page:** {your['url']}")
    n_total = shape.get('n_pages', 0) + len(skipped)
    lines.append(f"**Analyzed:** {shape.get('n_pages', 0)} of {n_total} top-ranking pages, {today}")
    if skipped:
        lines.append("")
        lines.append("> ⚠ Some pages were skipped:")
        for url, reason in skipped:
            lines.append(f"> - `{url}` — {reason}")
    lines.append("")

    # Headline gap summary
    if gaps:
        high = [g for g in gaps if g[0] == "high"]
        medium = [g for g in gaps if g[0] == "medium"]
        low = [g for g in gaps if g[0] == "low"]
        lines.append(f"## Position summary")
        lines.append(f"- {len(high)} high-priority gap(s)")
        lines.append(f"- {len(medium)} medium-priority gap(s)")
        lines.append(f"- {len(low)} low-priority gap(s)")
        lines.append("")

    # SERP intent map
    lines.append("## SERP intent map")
    lines.append(f"- Featured snippet format: **{shape.get('snippet_format') or 'none detected'}**")
    lines.append(f"- FAQ rich snippets present in **{int(shape.get('pct_with_faq_schema', 0)*100)}%** of results")
    lines.append(f"- Lists present in **{int(shape.get('pct_with_list', 0)*100)}%** of results")
    lines.append(f"- Tables present in **{int(shape.get('pct_with_table', 0)*100)}%** of results")
    mix = []
    if shape.get("videos_present"): mix.append("videos")
    if shape.get("news_present"): mix.append("news")
    if shape.get("knowledge_graph_present"): mix.append("knowledge panel")
    if mix:
        lines.append(f"- Content mix: " + ", ".join(mix))
    lines.append("")

    # People Also Ask
    paa = shape.get("paa_questions", [])
    if paa:
        lines.append("## People Also Ask (Google's suggested H2s)")
        for q in paa:
            lines.append(f"- {q}")
        lines.append("")

    # Related searches
    rs = shape.get("related_searches", [])
    if rs:
        lines.append("## Related searches (sub-intents)")
        for q in rs[:10]:
            lines.append(f"- {q}")
        lines.append("")

    # Per-page table
    if records:
        lines.append("## Top-10 — per-page detail")
        lines.append("")
        lines.append("| # | Title | Domain | Words | H2s | Schema | FAQ |")
        lines.append("|---|---|---|---|---|---|---|")
        for i, r in enumerate(records, start=1):
            domain = urlparse(r["url"]).netloc
            title = (r["title"] or "")[:60].replace("|", "/")
            schema = ", ".join(r["schema_types"][:2]) or "—"
            faq = "✓" if r["has_faq_schema"] else "·"
            lines.append(f"| {i} | {title} | {domain} | {r['word_count']} | {r['h2_count']} | {schema} | {faq} |")
        lines.append("")

    # Your page vs aggregate
    lines.append("## Your page vs SERP shape")
    lines.append("")
    lines.append("| Dimension | Your page | SERP aggregate |")
    lines.append("|---|---|---|")
    lines.append(f"| Word count | {your['word_count']} | median {shape.get('median_word_count', 0)} |")
    lines.append(f"| H2 count | {your['h2_count']} | median {shape.get('median_h2_count', 0)} |")
    lines.append(f"| Title length | {your['title_len']} | median {shape.get('median_title_len', 0)} |")
    lines.append(f"| FAQ schema | {'yes' if your['has_faq_schema'] else 'no'} | {int(shape.get('pct_with_faq_schema', 0)*100)}% of rankers |")
    lines.append(f"| Lists | {your['list_count']} | {int(shape.get('pct_with_list', 0)*100)}% of rankers have ≥1 |")
    lines.append(f"| Schema types | {', '.join(your['schema_types']) or 'none'} | top: {', '.join(t for t, _ in shape.get('dominant_schema', [])[:3])} |")
    lines.append("")

    # Common H2 phrases
    if shape.get("common_h2_phrases"):
        lines.append("## Common H2 phrases across rankers")
        for phrase, count in shape["common_h2_phrases"]:
            lines.append(f"- **{phrase}** — {count}×")
        lines.append("")

    # Prioritized recommendations
    if gaps:
        lines.append("## Prioritized recommendations")
        for i, (pri, cat, msg) in enumerate(gaps, start=1):
            badge = pri.upper()
            lines.append(f"{i}. **[{badge}] [{cat}]** {msg}")
        lines.append("")
    else:
        lines.append("## Prioritized recommendations")
        lines.append("")
        lines.append("_No significant gaps detected — your page broadly matches the SERP shape._")
        lines.append("")

    return "\n".join(lines).rstrip() + "\n"


# ---------------------------------------------------------------------------
# Slug helper
# ---------------------------------------------------------------------------

def slugify(s: str) -> str:
    s = re.sub(r"[^a-zA-Z0-9]+", "-", s.lower()).strip("-")
    return s[:60] or "serp"


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

async def amain(args: argparse.Namespace) -> int:
    # Find skill dir (parent of scripts/)
    script_path = Path(__file__).resolve()
    skill_dir = script_path.parent.parent

    env = load_env(skill_dir)
    api_key = env.get("SERPAPI_API_KEY") or os.environ.get("SERPAPI_API_KEY", "")
    if not api_key or api_key == "your_serpapi_key_here":
        print(
            f"ERROR: SERPAPI_API_KEY not set.\n"
            f"  Create {skill_dir}/.env from .env.example and add your key.\n"
            f"  Get a free key at https://serpapi.com",
            file=sys.stderr,
        )
        return 2

    geo_label = args.location or f"global (gl={args.gl}, hl={args.hl})"
    print(f"Fetching SerpAPI for keyword: {args.keyword!r} (depth={args.depth}, {geo_label})…", file=sys.stderr)
    serp = await fetch_serp(args.keyword, api_key, args.depth,
                            args.location, args.gl, args.hl, args.google_domain)
    if "error" in serp:
        print(f"ERROR: SerpAPI returned: {serp['error']}", file=sys.stderr)
        return 1
    organic = serp.get("organic_results", [])[: args.depth]
    if not organic:
        print(f"ERROR: SerpAPI returned 0 organic results for {args.keyword!r}.", file=sys.stderr)
        return 1
    print(f"  Got {len(organic)} organic results.", file=sys.stderr)

    # Fetch each top-N page
    records: list[dict] = []
    skipped: list[tuple[str, str]] = []
    for i, item in enumerate(organic, start=1):
        url = item.get("link", "")
        if not url:
            continue
        print(f"  [{i}/{len(organic)}] fetching {url}", file=sys.stderr)
        try:
            html = await fetch_url(url)
            records.append(extract_page_features(html, url))
        except Exception as e:
            reason = str(e)[:120]
            print(f"    skip ({reason})", file=sys.stderr)
            skipped.append((url, reason))

    if not records:
        print("ERROR: could not fetch any of the SERP pages.", file=sys.stderr)
        return 1

    # Fetch and analyze user's page
    print(f"Fetching your page: {args.your_page}", file=sys.stderr)
    try:
        your_html = await fetch_url(args.your_page)
    except Exception as e:
        print(f"ERROR fetching your page: {e}", file=sys.stderr)
        return 1
    your = extract_page_features(your_html, args.your_page)

    shape = compute_shape(records, serp)
    gaps = compute_gaps(your, shape)
    report = render_report(args.keyword, your, shape, records, gaps, skipped)

    # Resolve output path
    if args.output:
        out_path = Path(args.output)
    else:
        DEFAULT_OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
        out_path = DEFAULT_OUTPUT_DIR / f"{slugify(args.keyword)}-serp-shape.md"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(report, encoding="utf-8")

    if args.json_sidecar:
        sidecar = out_path.with_suffix(".json")
        sidecar.write_text(json.dumps({
            "keyword": args.keyword,
            "your_page": args.your_page,
            "your_record": your,
            "shape": shape,
            "records": records,
            "gaps": [{"priority": p, "category": c, "message": m} for p, c, m in gaps],
        }, ensure_ascii=False, indent=2, default=str), encoding="utf-8")

    high_count = sum(1 for g in gaps if g[0] == "high")
    print(f"Wrote {out_path} ({len(gaps)} gaps, {high_count} high-priority)", file=sys.stderr)
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Compare a webpage to the top-10 Google results for a keyword."
    )
    parser.add_argument("--keyword", required=True, help="Search keyword.")
    parser.add_argument("--your-page", required=True, help="URL of the page being optimized.")
    parser.add_argument("--depth", type=int, default=10, help="Number of SERP results (1-20).")
    parser.add_argument("--location", default="United States",
        help="SerpAPI location string. Default 'United States'. "
             "Pass empty string '' to skip geo-targeting (broadest SERP).")
    parser.add_argument("--gl", default="us",
        help="Country code (gl) — affects organic ranking. Default 'us'. "
             "Use 'ca' for Canada, 'fr' for France, etc.")
    parser.add_argument("--hl", default="en",
        help="Language code (hl). Default 'en'. Use 'fr' for French, etc.")
    parser.add_argument("--google-domain", default="google.com",
        help="Google domain. Default 'google.com' (international). "
             "Use 'google.ca', 'google.fr', etc. for country-specific.")
    parser.add_argument("-o", "--output", help="Output Markdown path.")
    parser.add_argument("--json-sidecar", action="store_true",
        help="Also write a JSON sidecar with the raw shape data.")
    args = parser.parse_args(argv)
    return asyncio.run(amain(args))


if __name__ == "__main__":
    sys.exit(main())
