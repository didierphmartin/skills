#!/usr/bin/env python3
"""
audit.py — SEO content brief generator.

Pipeline:
  1. Fetch Google top-N for the target keyword via SerpAPI
  2. Filter out non-competitors (Wikipedia, Reddit, YouTube, social, etc.)
  3. Fetch each remaining competitor; extract H2/H3, word count, schema types
  4. Classify search intent (rule-based)
  5. (Improve mode only) Fetch the existing URL; diff against competitors
  6. Emit BRIEF.md

Pyodide-safe. SerpAPI key from <skill>/.env.

Usage:
    python audit.py "target keyword"
    python audit.py "target keyword" --existing-url URL
    python audit.py "target keyword" --top-n 3 --gl fr --hl fr
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import re
import statistics
import sys
import time
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional
from urllib.parse import urlparse, urlencode

from bs4 import BeautifulSoup


DEFAULT_OUTPUT_DIR = (
    Path(os.environ.get("SYNERGYAI_OUTPUT_DIR", "/outputs"))
    if sys.platform == "emscripten"
    else Path.home() / "Documents" / "synergyAI" / "outputs"
)
PROXY_ENDPOINT = "/gpt/backend/api/v1/fetch-url"
SCRIPT_DIR = Path(__file__).resolve().parent
SKILL_DIR = SCRIPT_DIR.parent

BROWSER_UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
)


# ---------------------------------------------------------------------------
# Excluded domains — non-competitors that shouldn't drive brief structure
# Sourced from claude-seo's references/excluded-domains.md.
# ---------------------------------------------------------------------------

EXCLUDED_DOMAINS = {
    # Encyclopedic / community
    "wikipedia.org", "wiktionary.org", "fandom.com",
    "reddit.com", "quora.com", "stackoverflow.com", "stackexchange.com",
    # Social / media
    "pinterest.com", "twitter.com", "x.com", "facebook.com", "instagram.com",
    "tiktok.com", "linkedin.com", "youtube.com", "vimeo.com",
    # E-commerce marketplaces (rarely the right brief target)
    "amazon.com", "amazon.co.uk", "amazon.ca", "ebay.com", "etsy.com",
    "walmart.com", "alibaba.com", "aliexpress.com",
    # SEO tools (their content is meta, not topic-specific)
    "ahrefs.com", "semrush.com", "moz.com", "neilpatel.com",
    "searchenginejournal.com", "searchengineland.com",
    # Job boards / directories
    "indeed.com", "glassdoor.com", "monster.com", "yelp.com",
    "tripadvisor.com", "yellowpages.com",
    # Aggregators / news
    "news.google.com", "msn.com", "yahoo.com",
}


def is_excluded(url: str) -> bool:
    host = urlparse(url).netloc.lower()
    # strip leading www.
    if host.startswith("www."):
        host = host[4:]
    return any(host == d or host.endswith("." + d) for d in EXCLUDED_DOMAINS)


# ---------------------------------------------------------------------------
# Intent classifier (same shape as topic-cluster's)
# ---------------------------------------------------------------------------

_INFO_TOKENS  = {"how", "what", "why", "when", "where", "who", "guide", "tutorial",
                  "learn", "examples", "explain", "explained", "definition", "meaning"}
_COMM_TOKENS  = {"best", "top", "review", "reviews", "comparison", "compare",
                  "vs", "versus", "alternative", "alternatives", "rated", "ranking"}
_TRANS_TOKENS = {"buy", "price", "pricing", "cost", "discount", "coupon", "order",
                  "subscribe", "sign", "demo", "free", "trial", "download"}


def classify_intent(keyword: str) -> str:
    tokens = set(re.findall(r"\w+", keyword.lower()))
    trans_score = len(tokens & _TRANS_TOKENS)
    comm_score = len(tokens & _COMM_TOKENS)
    info_score = len(tokens & _INFO_TOKENS)
    if trans_score > 0 and trans_score >= comm_score and trans_score >= info_score:
        return "transactional"
    if comm_score > 0 and comm_score >= info_score:
        return "commercial"
    if info_score > 0:
        return "informational"
    return "informational"


def recommend_format(intent: str, keyword: str) -> str:
    """Pick a SERP format recommendation by intent + keyword shape."""
    kw_lower = keyword.lower()
    if "vs" in kw_lower.split() or "versus" in kw_lower:
        return "comparison"
    if kw_lower.startswith("how to "):
        return "step-by-step how-to"
    if kw_lower.startswith("best ") or kw_lower.startswith("top "):
        return "listicle (numbered ranking)"
    if intent == "informational":
        return "long-form guide"
    if intent == "commercial":
        return "listicle or comparison"
    if intent == "transactional":
        return "landing page with offer + CTA"
    return "long-form guide"


# ---------------------------------------------------------------------------
# SerpAPI + fetch helpers
# ---------------------------------------------------------------------------

def _is_pyodide() -> bool:
    return sys.platform == "emscripten"


def load_env_key() -> str:
    env = SKILL_DIR / ".env"
    if env.exists():
        for line in env.read_text(encoding="utf-8").splitlines():
            line = line.split("#", 1)[0].strip()
            if line.startswith("SERPAPI_API_KEY="):
                return line.split("=", 1)[1].strip().strip('"').strip("'")
    return os.environ.get("SERPAPI_API_KEY", "")


async def _fetch_pyodide(url: str) -> Optional[str]:
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
        text = await resp.string()
        payload = json.loads(text)
    except Exception:
        return None
    if not payload.get("success"):
        return None
    return (payload.get("data", {}) or {}).get("html", "") or ""


def _fetch_urllib(url: str) -> Optional[str]:
    import ssl
    import urllib.request
    import gzip
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    req = urllib.request.Request(url, headers={
        "User-Agent": BROWSER_UA, "Accept": "text/html,*/*", "Accept-Encoding": "gzip",
    })
    try:
        with urllib.request.urlopen(req, timeout=30, context=ctx) as r:
            body = r.read()
            enc = (r.headers.get("Content-Encoding") or "").lower()
    except Exception:
        return None
    if enc == "gzip":
        try: body = gzip.decompress(body)
        except OSError: pass
    try:
        return body.decode("utf-8", errors="replace")
    except Exception:
        return None


async def fetch(url: str) -> Optional[str]:
    if _is_pyodide():
        return await _fetch_pyodide(url)
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(None, _fetch_urllib, url)


async def serpapi_search(keyword: str, api_key: str, depth: int,
                          gl: str, hl: str, location: Optional[str]) -> dict:
    params = {
        "engine": "google", "q": keyword, "num": min(20, max(3, depth)),
        "gl": gl, "hl": hl, "google_domain": "google.com", "api_key": api_key,
    }
    if location:
        params["location"] = location
    url = "https://serpapi.com/search?" + urlencode(params)
    body = await fetch(url)
    if not body:
        return {"error": "fetch_failed"}
    try:
        return json.loads(body)
    except json.JSONDecodeError:
        return {"error": "non_json", "preview": body[:200]}


# ---------------------------------------------------------------------------
# Competitor analysis
# ---------------------------------------------------------------------------

@dataclass
class CompetitorPage:
    rank: int
    url: str
    title: str = ""
    h1: str = ""
    h2s: list[str] = field(default_factory=list)
    h3s: list[str] = field(default_factory=list)
    word_count: int = 0
    schema_types: list[str] = field(default_factory=list)
    fetch_error: Optional[str] = None


async def extract_competitor(rank: int, url: str) -> CompetitorPage:
    cp = CompetitorPage(rank=rank, url=url)
    body = await fetch(url)
    if not body:
        cp.fetch_error = "fetch failed (proxy error or 4xx/5xx)"
        return cp
    try:
        soup = BeautifulSoup(body, "html.parser")
    except Exception as e:
        cp.fetch_error = f"parse error: {e}"
        return cp

    if soup.title and soup.title.string:
        cp.title = soup.title.string.strip()
    h1 = soup.find("h1")
    if h1:
        cp.h1 = h1.get_text(" ", strip=True)
    cp.h2s = [h.get_text(" ", strip=True) for h in soup.find_all("h2") if h.get_text(strip=True)][:25]
    cp.h3s = [h.get_text(" ", strip=True) for h in soup.find_all("h3") if h.get_text(strip=True)][:50]

    # Word count from cleaned body
    for el in soup(["script", "style", "nav", "header", "footer", "aside", "noscript"]):
        el.decompose()
    text = soup.get_text(" ", strip=True)
    cp.word_count = len(re.findall(r"\b\w+\b", text))

    # Schema types
    for script in soup.find_all("script", type="application/ld+json"):
        if not script.string:
            continue
        try:
            data = json.loads(script.string)
        except json.JSONDecodeError:
            continue
        items = data if isinstance(data, list) else [data]
        stack = list(items)
        while stack:
            it = stack.pop()
            if not isinstance(it, dict):
                continue
            t = it.get("@type")
            if t:
                if isinstance(t, list):
                    cp.schema_types.extend(t)
                else:
                    cp.schema_types.append(t)
            graph = it.get("@graph")
            if isinstance(graph, list):
                stack.extend(g for g in graph if isinstance(g, dict))
    cp.schema_types = list(dict.fromkeys(cp.schema_types))

    return cp


# ---------------------------------------------------------------------------
# Outline synthesis
# ---------------------------------------------------------------------------

def collect_common_topics(competitors: list[CompetitorPage]) -> list[str]:
    """Find H2 topics that appear (loosely) in ≥3 competitors.

    Loose matching: lowercase, strip punctuation, take the first 4 words.
    """
    def normalize(h: str) -> str:
        words = re.findall(r"\w+", h.lower())
        return " ".join(words[:4])

    counter: Counter[str] = Counter()
    raw_examples: dict[str, list[str]] = {}
    for cp in competitors:
        seen_normalized = set()
        for h in cp.h2s:
            n = normalize(h)
            if not n or n in seen_normalized:
                continue
            seen_normalized.add(n)
            counter[n] += 1
            raw_examples.setdefault(n, []).append(h)

    # Topics that appear in ≥3 competitors are "common"
    common = [(n, count) for n, count in counter.items() if count >= 3]
    # Sort by frequency
    common.sort(key=lambda kv: -kv[1])

    # Return the human-readable variant (first raw example for each normalized topic)
    return [raw_examples[n][0] for n, _ in common]


def collect_unique_topics(competitors: list[CompetitorPage]) -> list[tuple[str, str]]:
    """Topics covered by only 1 competitor — potential differentiation opportunities."""
    def normalize(h: str) -> str:
        words = re.findall(r"\w+", h.lower())
        return " ".join(words[:4])

    by_topic: dict[str, list[tuple[str, str]]] = {}
    for cp in competitors:
        for h in cp.h2s:
            n = normalize(h)
            if not n:
                continue
            by_topic.setdefault(n, []).append((h, urlparse(cp.url).netloc))

    unique = []
    for n, owners in by_topic.items():
        if len(owners) == 1:
            heading, domain = owners[0]
            unique.append((heading, domain))
    return unique[:15]


# ---------------------------------------------------------------------------
# Render the brief
# ---------------------------------------------------------------------------

def render_brief(keyword: str, intent: str, format_rec: str,
                  competitors: list[CompetitorPage],
                  excluded_results: list[tuple[int, str]],
                  paa: list[str], related: list[str],
                  existing: Optional[CompetitorPage]) -> str:
    valid_competitors = [c for c in competitors if not c.fetch_error]
    word_counts = [c.word_count for c in valid_competitors if c.word_count > 0]
    median_wc = int(statistics.median(word_counts)) if word_counts else 1500
    target_wc = int(median_wc * 1.1)  # 10% above median

    common_topics = collect_common_topics(valid_competitors)
    unique_topics = collect_unique_topics(valid_competitors)

    lines = [
        f"# Content Brief: {keyword}",
        "",
        f"**Generated:** {time.strftime('%Y-%m-%d %H:%M:%S')}",
        f"**Intent:** {intent}",
        f"**Recommended SERP format:** {format_rec}",
        f"**Word-count target:** ~{target_wc} (median competitor: {median_wc})",
        "",
        "## Title + Meta + URL",
        "",
        "- **Title tag (60-110 chars):** `[Write title here — must contain primary keyword near the front]`",
        "- **Meta description (150-160 chars):** `[Summary including primary keyword + value proposition]`",
        f"- **URL slug:** `/{re.sub(r'[^a-z0-9]+', '-', keyword.lower()).strip('-')}/`",
        "",
        "## Competitor analysis",
        "",
        "| Rank | Domain | Word count | H2 count | H3 count | Schema types |",
        "|---|---|---|---|---|---|",
    ]
    for c in competitors:
        if c.fetch_error:
            lines.append(f"| {c.rank} | {urlparse(c.url).netloc} | — | — | — | _fetch failed_ |")
            continue
        types_short = ", ".join(c.schema_types[:3]) or "—"
        if len(c.schema_types) > 3:
            types_short += f" (+{len(c.schema_types) - 3})"
        lines.append(
            f"| {c.rank} | {urlparse(c.url).netloc} | {c.word_count} | "
            f"{len(c.h2s)} | {len(c.h3s)} | {types_short} |"
        )
    lines.append("")

    if excluded_results:
        lines.append("**Excluded from analysis** (non-competitor domains):")
        lines.append("")
        for r, u in excluded_results:
            lines.append(f"- rank {r}: {urlparse(u).netloc} ({u})")
        lines.append("")

    lines.append("## H2/H3 outline")
    lines.append("")
    if common_topics:
        lines.append("**Must-cover topics** (appear in ≥3 competitors — table-stakes for ranking):")
        lines.append("")
        section_wc = max(200, target_wc // (max(1, len(common_topics)) + 2))
        for topic in common_topics:
            lines.append(f"### {topic}")
            lines.append(f"- Target: ~{section_wc} words")
            lines.append("- Subtopics (H3): _[to fill — see competitor H3 detail below]_")
            lines.append("")

    if unique_topics:
        lines.append("**Differentiation opportunities** (topics only ONE competitor covers — potential gap):")
        lines.append("")
        for heading, domain in unique_topics[:8]:
            lines.append(f"- `{heading}` (currently only covered by `{domain}`)")
        lines.append("")
        lines.append("_Evaluate each: does it match your business / audience? If yes, including it differentiates your page._")
        lines.append("")

    if existing:
        lines.append("## Improve-mode diff")
        lines.append("")
        existing_normalized = {re.sub(r'\W', '', h.lower()) for h in existing.h2s}
        common_normalized = {re.sub(r'\W', '', t.lower()) for t in common_topics}
        keep = [t for t in common_topics if re.sub(r'\W', '', t.lower()) in existing_normalized]
        add = [t for t in common_topics if re.sub(r'\W', '', t.lower()) not in existing_normalized]
        lines.append(f"Existing page ({existing.url}) has {len(existing.h2s)} H2s, {existing.word_count} words.")
        lines.append("")
        if keep:
            lines.append("**Keep/strengthen** (already in your page + appears in competitors):")
            for t in keep:
                lines.append(f"- {t}")
            lines.append("")
        if add:
            lines.append("**Add new** (in competitors, missing from your page):")
            for t in add:
                lines.append(f"- {t}")
            lines.append("")

    if paa:
        lines.append("## People Also Ask (address these questions in FAQ section or inline)")
        lines.append("")
        for q in paa:
            lines.append(f"- {q}")
        lines.append("")

    if related:
        lines.append("## Related searches (include semantically — don't keyword-stuff)")
        lines.append("")
        for r in related:
            lines.append(f"- {r}")
        lines.append("")

    lines.append("## Keyword placement rules")
    lines.append("")
    lines.append("Primary keyword MUST appear in:")
    lines.append("1. Title tag (near the front)")
    lines.append("2. H1 (near the front)")
    lines.append("3. URL slug")
    lines.append("4. Meta description")
    lines.append("5. First 100 words")
    lines.append("6. At least one image alt text")
    lines.append("")
    lines.append("Primary keyword density target: 0.5%-2.0% of total word count. Above 3% risks keyword-stuffing penalties.")
    lines.append("")
    lines.append("See `references/keyword-density.md` for full guidance.")
    lines.append("")

    # Competitor H2/H3 detail (handy reference at the end)
    lines.append("## Competitor heading detail (reference)")
    lines.append("")
    for c in valid_competitors:
        lines.append(f"### Rank {c.rank}: {urlparse(c.url).netloc}")
        lines.append("")
        lines.append(f"- Title: {c.title!r}")
        lines.append(f"- H1: {c.h1!r}")
        if c.h2s:
            lines.append(f"- H2s ({len(c.h2s)}):")
            for h in c.h2s[:15]:
                lines.append(f"    - {h}")
            if len(c.h2s) > 15:
                lines.append(f"    - _… {len(c.h2s) - 15} more_")
        lines.append("")

    return "\n".join(lines).rstrip() + "\n"


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

async def amain(args) -> int:
    DEFAULT_OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    api_key = load_env_key()
    if not api_key:
        print("ERROR: SERPAPI_API_KEY not found in <skill>/.env or env var.", file=sys.stderr)
        return 1

    print(f"Fetching SerpAPI top-{args.top_n} for {args.keyword!r}…", file=sys.stderr)
    serp = await serpapi_search(args.keyword, api_key, args.top_n,
                                  gl=args.gl, hl=args.hl, location=args.location)
    if serp.get("error"):
        print(f"ERROR: SerpAPI failed — {serp.get('error')}", file=sys.stderr)
        return 1

    organic = serp.get("organic_results") or []
    if not organic:
        print(f"ERROR: SerpAPI returned no organic results for {args.keyword!r}", file=sys.stderr)
        return 1

    # Split into competitors vs excluded
    competitors_to_fetch = []
    excluded_results = []
    for i, r in enumerate(organic[:args.top_n + 3], start=1):
        link = r.get("link")
        if not link:
            continue
        if is_excluded(link):
            excluded_results.append((i, link))
            continue
        competitors_to_fetch.append((i, link))
        if len(competitors_to_fetch) >= args.top_n:
            break

    if not competitors_to_fetch:
        print("ERROR: all top results filtered as non-competitors. Try a more specific keyword.",
              file=sys.stderr)
        return 1

    print(f"Fetching {len(competitors_to_fetch)} competitor page(s)…", file=sys.stderr)
    fetched = await asyncio.gather(*(extract_competitor(rank, url) for rank, url in competitors_to_fetch))

    existing = None
    if args.existing_url:
        print(f"Fetching existing page {args.existing_url}…", file=sys.stderr)
        existing = await extract_competitor(0, args.existing_url)

    intent = classify_intent(args.keyword)
    format_rec = recommend_format(intent, args.keyword)

    paa = [q.get("question") for q in (serp.get("related_questions") or []) if q.get("question")][:8]
    related = [r.get("query") for r in (serp.get("related_searches") or []) if r.get("query")][:8]

    brief = render_brief(args.keyword, intent, format_rec, fetched, excluded_results,
                          paa, related, existing)

    out_dir = args.output if args.output else DEFAULT_OUTPUT_DIR
    out_dir.mkdir(parents=True, exist_ok=True)
    # Slug-safe filename
    slug = re.sub(r"[^a-z0-9]+", "-", args.keyword.lower()).strip("-")[:60] or "brief"
    out = out_dir / f"BRIEF-{slug}.md"
    if args.stdout:
        sys.stdout.write(brief)
    else:
        out.write_text(brief, encoding="utf-8")
        print(f"\nWrote {out}", file=sys.stderr)
    print(f"Intent: {intent}; format: {format_rec}; competitors: {len(fetched)}, excluded: {len(excluded_results)}",
          file=sys.stderr)
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="SEO content brief generator.")
    parser.add_argument("keyword", nargs="?", help="Target keyword. Required unless --keyword.")
    parser.add_argument("--keyword", dest="keyword_alias", default=None,
        help="Alias for the positional keyword argument.")
    parser.add_argument("--existing-url", default=None,
        help="Existing page URL to brief in IMPROVE mode (keep/strengthen vs add-new diff).")
    parser.add_argument("--top-n", type=int, default=5,
        help="Number of competitor pages to analyze (default: 5).")
    parser.add_argument("--gl", default="us")
    parser.add_argument("--hl", default="en")
    parser.add_argument("--location", default=None,
        help="SerpAPI location (e.g. 'New York, NY'). Omit for broadest SERP.")
    parser.add_argument("-o", "--output", type=Path, default=None)
    parser.add_argument("--stdout", action="store_true")
    parser.add_argument("--source", default=None, help="Source label for bridge pre-fetch case.")
    args = parser.parse_args(argv)

    if not args.keyword and args.keyword_alias:
        args.keyword = args.keyword_alias
    if not args.keyword:
        parser.print_help(sys.stderr)
        return 2

    return asyncio.run(amain(args))


if __name__ == "__main__":
    sys.exit(main())
