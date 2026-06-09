#!/usr/bin/env python3
"""
audit.py — Search Experience Optimization (SXO).

Pipeline:
  1. Classify the target page's type
  2. Fetch SerpAPI top-10 for the keyword
  3. Classify each SERP result with the same taxonomy
  4. Compute SERP consensus + detect mismatch
  5. Score the target page against 4 personas × 5 axes
  6. Derive 4-6 user stories from SERP intent + persona signals

Output: SXO-REPORT.md

Usage:
    python audit.py URL                          # auto-detect keyword from page
    python audit.py URL --keyword "search term"  # override keyword
    python audit.py URL --skip-serp              # persona-only (no SerpAPI)
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
# Page-type taxonomy + classifier
# ---------------------------------------------------------------------------

PAGE_TYPES = (
    "blog-post", "listicle", "comparison", "how-to",
    "product", "service", "landing-page", "tool",
    "glossary", "homepage", "category-hub", "other",
)


@dataclass
class PageSignals:
    """Cheap signals we use to classify a page."""
    url: str = ""
    title: str = ""
    h1: str = ""
    word_count: int = 0
    h2_count: int = 0
    has_pricing_signal: bool = False  # "$", price near top
    has_cta_button: bool = False
    has_buy_intent: bool = False      # "add to cart", "buy now", etc.
    has_step_pattern: bool = False    # numbered H2s, "Step 1" pattern
    has_listicle_pattern: bool = False  # "Top 10", "5 Best", numbered ranking
    has_comparison_pattern: bool = False  # "vs", comparison table
    has_byline: bool = False
    has_publication_date: bool = False
    has_definition_pattern: bool = False  # short page, "X is" lead
    has_interactive: bool = False     # multiple <input>, calculators
    schema_types: list[str] = field(default_factory=list)


def classify_page_type(signals: PageSignals) -> tuple[str, int, list[str]]:
    """Score every page type. Return (winner, score, reasons)."""
    scores: dict[str, int] = {t: 0 for t in PAGE_TYPES}
    reasons: dict[str, list[str]] = {t: [] for t in PAGE_TYPES}

    path = urlparse(signals.url).path.lower()
    title_lower = (signals.title + " " + signals.h1).lower()

    # Homepage
    if path in ("", "/"):
        scores["homepage"] += 30
        reasons["homepage"].append("URL is site root")

    # URL-based path signals
    url_signals = [
        ("blog-post", ["/blog/", "/post/", "/article/"], 15),
        ("listicle", ["/best-", "/top-"], 10),
        ("comparison", ["-vs-", "-versus-", "/compare/"], 20),
        ("how-to", ["/how-to-", "/how-", "/guide/"], 10),
        ("product", ["/product/", "/products/", "/shop/", "/store/"], 15),
        ("service", ["/service/", "/services/", "/our-work/"], 10),
        ("glossary", ["/glossary/", "/dictionary/", "/define/", "/term/"], 20),
        ("category-hub", ["/category/", "/topics/", "/categories/"], 15),
    ]
    for ptype, patterns, weight in url_signals:
        for pat in patterns:
            if pat in path:
                scores[ptype] += weight
                reasons[ptype].append(f"URL contains `{pat}`")
                break

    # Title-based signals
    title_signals = [
        ("listicle", [r"\bbest\b", r"\btop\b", r"\b\d+\s+(best|top|reasons|tips|tools|ways|examples)"], 10),
        ("comparison", [r"\bvs\b", r"\bversus\b"], 25),
        ("how-to", [r"^how to\b", r"\bhow to\b"], 15),
        ("glossary", [r"^what is\b", r"\bdefinition\b"], 8),
    ]
    for ptype, patterns, weight in title_signals:
        for pat in patterns:
            if re.search(pat, title_lower):
                scores[ptype] += weight
                reasons[ptype].append(f"title matches /{pat}/")
                break

    # Content-pattern signals
    if signals.has_listicle_pattern:
        scores["listicle"] += 15
        reasons["listicle"].append("numbered ranking pattern in headings")
    if signals.has_comparison_pattern:
        scores["comparison"] += 15
        reasons["comparison"].append("comparison-table pattern detected")
    if signals.has_step_pattern:
        scores["how-to"] += 15
        reasons["how-to"].append("step-by-step pattern detected")
    if signals.has_buy_intent:
        scores["product"] += 20
        reasons["product"].append("buy-intent content (add-to-cart / buy-now)")
    if signals.has_pricing_signal and signals.has_cta_button:
        scores["product"] += 10
        scores["landing-page"] += 10
        reasons["product"].append("pricing + CTA")
        reasons["landing-page"].append("pricing + CTA")
    if signals.has_byline and signals.has_publication_date:
        scores["blog-post"] += 15
        reasons["blog-post"].append("byline + date detected")
    if signals.has_interactive:
        scores["tool"] += 25
        reasons["tool"].append("multiple input elements (interactive widget)")
    if signals.has_definition_pattern and signals.word_count < 800:
        scores["glossary"] += 15
        reasons["glossary"].append("definition-pattern + short page")

    # Schema-based signals
    schema_map = {
        "Article": ("blog-post", 15),
        "BlogPosting": ("blog-post", 20),
        "NewsArticle": ("blog-post", 15),
        "Product": ("product", 25),
        "Service": ("service", 25),
        "LocalBusiness": ("service", 15),
        "HowTo": ("how-to", 20),
        "ItemList": ("category-hub", 10),
        "WebSite": ("homepage", 5),
    }
    for t in signals.schema_types:
        if t in schema_map:
            ptype, weight = schema_map[t]
            scores[ptype] += weight
            reasons[ptype].append(f"JSON-LD declares {t}")

    # Word count tiebreakers
    if signals.word_count >= 1500 and not signals.has_buy_intent:
        scores["blog-post"] += 3
    if signals.word_count < 300:
        scores["landing-page"] += 3

    # Pick winner
    winner = max(scores.items(), key=lambda kv: kv[1])
    return (winner[0], winner[1], reasons[winner[0]])


def extract_signals(html: str, url: str) -> PageSignals:
    s = PageSignals(url=url)
    try:
        soup = BeautifulSoup(html, "html.parser")
    except Exception:
        return s

    if soup.title and soup.title.string:
        s.title = soup.title.string.strip()
    h1 = soup.find("h1")
    if h1:
        s.h1 = h1.get_text(" ", strip=True)

    # Extract JSON-LD schema BEFORE decomposing <script> tags below
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
                    s.schema_types.extend(t)
                else:
                    s.schema_types.append(t)
            graph = it.get("@graph")
            if isinstance(graph, list):
                stack.extend(g for g in graph if isinstance(g, dict))
    s.schema_types = list(dict.fromkeys(s.schema_types))

    # Word count from main content (strip chrome)
    for el in soup(["script", "style", "nav", "header", "footer", "aside", "noscript"]):
        el.decompose()
    text = soup.get_text(" ", strip=True)
    s.word_count = len(re.findall(r"\b\w+\b", text))

    h2s = [h.get_text(" ", strip=True) for h in soup.find_all("h2")]
    s.h2_count = len(h2s)
    h2_text = " | ".join(h2s).lower()

    s.has_step_pattern = bool(
        re.search(r"step\s+\d", h2_text) or
        sum(1 for h in h2s if re.match(r"^\s*\d+[\.\):]", h)) >= 3
    )
    s.has_listicle_pattern = bool(
        sum(1 for h in h2s if re.match(r"^\s*\d+[\.\):]?\s", h)) >= 3 or
        re.search(r"\b(top|best)\s+\d+", h2_text)
    )
    s.has_comparison_pattern = bool(
        re.search(r"\bvs\b|\bversus\b|\bcomparison\b", h2_text) or
        bool(soup.find("table"))
    )

    # Buy-intent: CTA + add-to-cart
    body_text_lower = text.lower()[:30000]
    s.has_buy_intent = any(p in body_text_lower for p in [
        "add to cart", "buy now", "in stock", "out of stock", "free shipping",
    ])
    s.has_pricing_signal = bool(re.search(r"\$\d+(?:[.,]\d{2})?|\d+\s*(usd|eur|gbp|cad)", body_text_lower))

    # CTA button heuristic
    buttons = soup.find_all(["button", "a"])
    cta_words = {"sign up", "get started", "try free", "start free", "request demo",
                  "book demo", "buy", "add to cart", "subscribe", "download"}
    for el in buttons:
        text_lower = el.get_text(strip=True).lower()
        if any(cw in text_lower for cw in cta_words):
            s.has_cta_button = True
            break

    # Byline + date
    s.has_byline = bool(
        soup.find("meta", attrs={"name": "author"}) or
        soup.find(attrs={"rel": "author"}) or
        soup.find(class_=re.compile(r"(byline|author|by-line)", re.I))
    )
    s.has_publication_date = bool(
        soup.find("meta", attrs={"property": "article:published_time"}) or
        soup.find("time") or
        soup.find(class_=re.compile(r"(published|publish-date|post-date)", re.I))
    )

    # Definition pattern
    first_para = soup.find("p")
    if first_para:
        para_text = first_para.get_text(" ", strip=True)
        s.has_definition_pattern = bool(
            re.match(r"^\s*\S+(?:\s+\S+){0,3}\s+(is|are|refers? to|means|stands for)\b",
                     para_text, re.I)
        )

    # Interactive tool heuristic
    inputs = soup.find_all("input")
    s.has_interactive = len([i for i in inputs if i.get("type") not in ("hidden", "submit")]) >= 3

    return s


def derive_keyword(signals: PageSignals) -> str:
    """If no keyword provided, derive one from title + H1 overlap."""
    # Strip site-name suffix from title
    title = re.sub(r"\s*[|\-–—]\s*[^|]+$", "", signals.title).strip()
    if signals.h1:
        # Pick the shorter of title vs H1 as the candidate keyword
        if len(signals.h1) < len(title) and len(signals.h1) > 5:
            return signals.h1
    return title


# ---------------------------------------------------------------------------
# Persona scoring (4 personas × 5 axes)
# ---------------------------------------------------------------------------

PERSONAS = ("researcher", "decision-maker", "buyer", "new-visitor")
AXES = ("clarity", "depth", "trust", "action", "scannability")


def score_persona(signals: PageSignals, persona: str) -> dict[str, int]:
    """Score 0-10 per axis. Heuristic rubric."""
    s = signals
    scores = {a: 0 for a in AXES}

    # Clarity — can persona understand in 5s?
    clarity = 0
    if s.title:
        clarity += 3
    if s.h1 and len(s.h1) < 100:
        clarity += 3
    if s.has_definition_pattern:
        clarity += 2
    if s.word_count > 200:  # has SOME content
        clarity += 2
    scores["clarity"] = min(10, clarity)

    # Depth — does the page answer follow-ups?
    depth = 0
    if s.word_count >= 300:
        depth += 2
    if s.word_count >= 800:
        depth += 2
    if s.word_count >= 1500:
        depth += 2
    if s.h2_count >= 4:
        depth += 2
    if s.h2_count >= 8:
        depth += 2
    scores["depth"] = min(10, depth)

    # Trust — bylines, dates, schema, social proof
    trust = 0
    if s.has_byline:
        trust += 3
    if s.has_publication_date:
        trust += 2
    if any(t in s.schema_types for t in ("Person", "Organization", "Article", "Product")):
        trust += 2
    if "Review" in s.schema_types or "AggregateRating" in s.schema_types:
        trust += 3
    scores["trust"] = min(10, trust)

    # Action — is the next step obvious for this persona?
    action = 0
    if s.has_cta_button:
        action += 4
    if s.has_buy_intent:
        action += 3
    if s.has_pricing_signal:
        action += 3
    scores["action"] = min(10, action)

    # Scannability — headings, lists, tables, bold
    scan = 0
    if s.h2_count >= 3:
        scan += 3
    if s.h2_count >= 6:
        scan += 2
    if s.has_listicle_pattern:
        scan += 2
    if s.has_comparison_pattern:
        scan += 2
    if 600 <= s.word_count <= 2500:  # appropriate length, not too short or bloated
        scan += 1
    scores["scannability"] = min(10, scan)

    # Persona-specific weighting — return same axes but emphasize relevant ones
    return scores


def persona_summary(persona: str, scores: dict[str, int]) -> tuple[float, list[str], list[str]]:
    """Compute weighted persona score + strengths + gaps."""
    # Weights per persona — sum to 10
    weights = {
        "researcher":     {"clarity": 1.5, "depth": 4, "trust": 3, "action": 0.5, "scannability": 1},
        "decision-maker": {"clarity": 3, "depth": 1.5, "trust": 1.5, "action": 2, "scannability": 2},
        "buyer":          {"clarity": 1.5, "depth": 1, "trust": 3, "action": 3, "scannability": 1.5},
        "new-visitor":    {"clarity": 3.5, "depth": 1, "trust": 1.5, "action": 2.5, "scannability": 1.5},
    }
    w = weights[persona]
    weighted = sum(scores[a] * w[a] for a in AXES) / 10.0  # max 10

    # Strengths = axes ≥ 7
    strengths = [a for a, v in scores.items() if v >= 7]
    # Gaps = axes ≤ 4 weighted heavily for this persona
    gaps = [a for a, v in scores.items() if v <= 4 and w[a] >= 2]
    return (round(weighted, 1), strengths, gaps)


# ---------------------------------------------------------------------------
# SerpAPI helpers
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
        "User-Agent": BROWSER_UA, "Accept": "*/*", "Accept-Encoding": "gzip",
    })
    try:
        with urllib.request.urlopen(req, timeout=20, context=ctx) as r:
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


async def serpapi_search(keyword: str, api_key: str, gl: str, hl: str) -> dict:
    params = {
        "engine": "google", "q": keyword, "num": 10,
        "gl": gl, "hl": hl, "google_domain": "google.com", "api_key": api_key,
    }
    url = "https://serpapi.com/search?" + urlencode(params)
    body = await fetch(url)
    if not body:
        return {"error": "fetch_failed"}
    try:
        return json.loads(body)
    except json.JSONDecodeError:
        return {"error": "non_json"}


# ---------------------------------------------------------------------------
# Render report
# ---------------------------------------------------------------------------

def derive_user_stories(target_type: str, serp_consensus: Optional[str],
                          persona_scores: dict, gaps_by_persona: dict[str, list[str]],
                          keyword: str) -> list[str]:
    """Generate user stories from intent + persona analysis."""
    stories: list[str] = []

    # 1-2 stories from SERP consensus / target-type mismatch
    if serp_consensus and target_type != serp_consensus and serp_consensus != "other":
        # The most common case
        action_map = {
            "comparison": f"see a feature-comparison matrix for {keyword}",
            "listicle": f"see a ranked shortlist for {keyword}",
            "how-to": f"follow step-by-step instructions for {keyword}",
            "product": f"see pricing and specs for {keyword}",
            "tool": f"compute the answer using an interactive tool",
            "blog-post": f"read an in-depth article on {keyword}",
            "glossary": f"see a quick definition of {keyword}",
        }
        action = action_map.get(serp_consensus, f"get the {serp_consensus} treatment of {keyword}")
        stories.append(
            f"As a searcher for `{keyword}`, I want to {action} "
            f"so that I can match the expectation Google's top results set."
        )

    # Persona-gap stories — surface 1-2 per persona with biggest gaps
    gap_templates = {
        "clarity":      ("understand what this page offers in 5 seconds",
                          "I can decide if it's relevant without reading the full page"),
        "depth":        ("find detailed coverage of {topic}",
                          "I have enough information to make my decision"),
        "trust":        ("see author credentials + publication dates",
                          "I can verify the claims are credible"),
        "action":       ("see a clear next step at the top of the page",
                          "I don't have to hunt for the CTA"),
        "scannability": ("scan the page in 30 seconds via headings + lists",
                          "I can locate the section I need quickly"),
    }
    for persona, gaps in gaps_by_persona.items():
        if not gaps:
            continue
        # One story per persona
        gap = gaps[0]
        if gap in gap_templates:
            action, outcome = gap_templates[gap]
            action = action.format(topic=keyword)
            stories.append(f"As a {persona}, I want to {action} so that {outcome}.")
        if len(stories) >= 6:
            break

    # Always include a generic story if we have fewer than 3
    if len(stories) < 3:
        stories.append(
            f"As a new visitor searching for `{keyword}`, I want to confirm this page "
            f"answers my search intent within the first paragraph "
            f"so that I stay rather than bounce."
        )
    return stories[:6]


def render_report(target_url: str, keyword: str, target_signals: PageSignals,
                    target_type: str, target_type_score: int, target_type_reasons: list[str],
                    serp_data: Optional[dict],
                    serp_classifications: list[tuple[int, str, str, int]],
                    serp_consensus: Optional[str], serp_consensus_pct: float,
                    persona_scores: dict, user_stories: list[str]) -> str:
    lines = [
        f"# SXO Report — {target_url}",
        "",
        f"**Keyword:** `{keyword}`",
        f"**Generated:** {time.strftime('%Y-%m-%d %H:%M:%S')}",
        "",
        "## Target page classification",
        "",
        f"**Detected page type:** `{target_type}` (score {target_type_score})",
        "",
        "Signals:",
    ]
    for r in target_type_reasons:
        lines.append(f"- {r}")
    if not target_type_reasons:
        lines.append("- (low confidence — no strong signals)")
    lines.append("")
    lines.append("**Detected attributes:**")
    lines.append("")
    lines.append(f"- Title: {target_signals.title!r}")
    lines.append(f"- H1: {target_signals.h1!r}")
    lines.append(f"- Word count: {target_signals.word_count}")
    lines.append(f"- H2 count: {target_signals.h2_count}")
    lines.append(f"- Has byline: {target_signals.has_byline}")
    lines.append(f"- Has publication date: {target_signals.has_publication_date}")
    lines.append(f"- Has CTA button: {target_signals.has_cta_button}")
    lines.append(f"- Has pricing signal: {target_signals.has_pricing_signal}")
    lines.append(f"- Has buy-intent: {target_signals.has_buy_intent}")
    lines.append(f"- Schema types: {', '.join(target_signals.schema_types) or 'none'}")
    lines.append("")

    # SERP section
    if serp_classifications:
        lines.append("## SERP analysis (top 10)")
        lines.append("")
        lines.append("| # | Domain | Page type | Confidence |")
        lines.append("|---|---|---|---|")
        for rank, domain, ptype, score in serp_classifications:
            lines.append(f"| {rank} | {domain} | `{ptype}` | {score} |")
        lines.append("")

        if serp_consensus:
            lines.append(f"**SERP consensus:** `{serp_consensus}` ({serp_consensus_pct:.0%} of results)")
            lines.append("")
            if target_type == serp_consensus:
                lines.append("✅ **ALIGNED** — your page type matches the SERP consensus. Focus on content depth + UX.")
            elif serp_consensus_pct >= 0.6:
                severity = "🛑 **CRITICAL MISMATCH**"
                recommendation = (
                    f"Your `{target_type}` page is competing in a SERP that overwhelmingly rewards `{serp_consensus}` "
                    f"pages ({serp_consensus_pct:.0%}). Restructure as a `{serp_consensus}` page OR target a different keyword."
                )
                lines.append(f"{severity} — {recommendation}")
            elif serp_consensus_pct >= 0.4:
                lines.append(f"⚠️ **HIGH MISMATCH** — SERP leans toward `{serp_consensus}` ({serp_consensus_pct:.0%}); your `{target_type}` page has a steeper hill.")
            else:
                lines.append("⚠️ Fragmented SERP — no dominant type. Differentiation opportunity but no obvious template to copy.")
            lines.append("")

    # Persona scoring
    lines.append("## Persona scoring")
    lines.append("")
    lines.append("| Persona | Weighted score | Clarity | Depth | Trust | Action | Scan |")
    lines.append("|---|---|---|---|---|---|---|")
    for persona in PERSONAS:
        info = persona_scores[persona]
        ax = info["axes"]
        lines.append(
            f"| {persona} | {info['weighted']:.1f}/10 | "
            f"{ax['clarity']} | {ax['depth']} | {ax['trust']} | "
            f"{ax['action']} | {ax['scannability']} |"
        )
    lines.append("")

    for persona in PERSONAS:
        info = persona_scores[persona]
        lines.append(f"### {persona.title()} — {info['weighted']:.1f}/10")
        lines.append("")
        if info["strengths"]:
            lines.append(f"**Strengths:** {', '.join(info['strengths'])}")
        if info["gaps"]:
            lines.append(f"**Gaps (weighted heavily for {persona}):** {', '.join(info['gaps'])}")
        if not info["gaps"] and not info["strengths"]:
            lines.append("Adequate but no standout strengths or gaps")
        lines.append("")

    # User stories
    if user_stories:
        lines.append("## User stories")
        lines.append("")
        for s in user_stories:
            lines.append(f"- {s}")
        lines.append("")

    return "\n".join(lines).rstrip() + "\n"


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

async def amain(args) -> int:
    DEFAULT_OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    # Fetch target page
    print(f"Fetching target page: {args.input}…", file=sys.stderr)
    html = await fetch(args.input)
    if not html:
        print(f"ERROR: could not fetch {args.input}", file=sys.stderr)
        return 1

    target_signals = extract_signals(html, args.input)
    target_type, target_score, target_reasons = classify_page_type(target_signals)
    print(f"Target page type: {target_type} (score {target_score})", file=sys.stderr)

    # Keyword
    keyword = args.keyword or derive_keyword(target_signals)
    if not keyword:
        print("ERROR: could not derive a keyword from the page. Pass --keyword.", file=sys.stderr)
        return 1
    print(f"Using keyword: {keyword!r}", file=sys.stderr)

    # SERP analysis
    serp_data: Optional[dict] = None
    serp_classifications: list[tuple[int, str, str, int]] = []
    consensus: Optional[str] = None
    consensus_pct = 0.0

    if not args.skip_serp:
        api_key = load_env_key()
        if not api_key:
            print("WARNING: no SERPAPI_API_KEY. Skipping SERP analysis (use --skip-serp to silence).",
                  file=sys.stderr)
        else:
            print(f"Fetching SerpAPI top-10 for {keyword!r}…", file=sys.stderr)
            serp_data = await serpapi_search(keyword, api_key, args.gl, args.hl)
            if serp_data.get("error"):
                print(f"WARNING: SerpAPI failed — {serp_data.get('error')}", file=sys.stderr)
            else:
                organic = serp_data.get("organic_results") or []
                urls_to_fetch = [(i + 1, r.get("link")) for i, r in enumerate(organic[:10]) if r.get("link")]
                print(f"Classifying {len(urls_to_fetch)} SERP result(s)…", file=sys.stderr)

                # Concurrent fetches
                sem = asyncio.Semaphore(5)
                async def classify_one(rank: int, url: str) -> tuple[int, str, str, int]:
                    async with sem:
                        body = await fetch(url)
                    if not body:
                        return (rank, urlparse(url).netloc, "other", 0)
                    sigs = extract_signals(body, url)
                    ptype, score, _ = classify_page_type(sigs)
                    return (rank, urlparse(url).netloc, ptype, score)

                serp_classifications = await asyncio.gather(
                    *(classify_one(r, u) for r, u in urls_to_fetch)
                )

                # Consensus
                type_counts = Counter(c[2] for c in serp_classifications)
                if type_counts:
                    most_common_type, most_common_count = type_counts.most_common(1)[0]
                    consensus = most_common_type
                    consensus_pct = most_common_count / sum(type_counts.values())
                    print(f"SERP consensus: {consensus} ({consensus_pct:.0%})", file=sys.stderr)

    # Persona scoring
    persona_scores: dict[str, dict] = {}
    gaps_by_persona: dict[str, list[str]] = {}
    for persona in PERSONAS:
        axes = score_persona(target_signals, persona)
        weighted, strengths, gaps = persona_summary(persona, axes)
        persona_scores[persona] = {
            "weighted": weighted, "axes": axes,
            "strengths": strengths, "gaps": gaps,
        }
        gaps_by_persona[persona] = gaps

    # User stories
    user_stories = derive_user_stories(target_type, consensus, persona_scores, gaps_by_persona, keyword)

    # Render + write
    report = render_report(args.input, keyword, target_signals, target_type, target_score, target_reasons,
                            serp_data, list(serp_classifications), consensus, consensus_pct,
                            persona_scores, user_stories)

    out = args.output if args.output else (DEFAULT_OUTPUT_DIR / "SXO-REPORT.md")
    if out.is_dir():
        out = out / "SXO-REPORT.md"
    out.parent.mkdir(parents=True, exist_ok=True)
    if args.stdout:
        sys.stdout.write(report)
    else:
        out.write_text(report, encoding="utf-8")
        print(f"\nWrote {out}", file=sys.stderr)
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Search Experience Optimization (SXO) audit.")
    parser.add_argument("input", nargs="?", help="Target page URL.")
    parser.add_argument("--url", dest="url_alias", default=None, help="Alias for positional URL.")
    parser.add_argument("--keyword", default=None,
        help="Search keyword to analyze. Auto-derived from page title + H1 if omitted.")
    parser.add_argument("--skip-serp", action="store_true",
        help="Skip SerpAPI + per-result fetches (persona-only mode — faster, no SerpAPI cost).")
    parser.add_argument("--gl", default="us")
    parser.add_argument("--hl", default="en")
    parser.add_argument("-o", "--output", type=Path, default=None)
    parser.add_argument("--stdout", action="store_true")
    parser.add_argument("--source", default=None, help="Source label for bridge pre-fetch.")
    args = parser.parse_args(argv)

    if not args.input and args.url_alias:
        args.input = args.url_alias
    if args.source and re.match(r"^https?://", args.source, re.I):
        args.input = args.source
    if not args.input:
        parser.print_help(sys.stderr)
        return 2

    return asyncio.run(amain(args))


if __name__ == "__main__":
    sys.exit(main())
