#!/usr/bin/env python3
"""
audit.py — Audit an HTML page for AI-search readiness.

Scores how well a page is set up to be cited by AI search engines and LLM
overviews (Gemini AI Overviews, ChatGPT Search, Perplexity, Bing Copilot,
etc.). Three audit passes:

  1. Cognitive structure — heading hierarchy, section length, lead-sentence
     thesis, list/table density, citation density, paragraph length.
  2. Machine-readable metadata — JSON-LD schema, Open Graph, meta description,
     canonical, robots, language attribute.
  3. Asset hygiene — alt-text coverage, broken-looking links, title tag.

Each finding has a severity (critical / recommended / nice-to-have), a
reason ("why it hurts AI citation"), and a concrete fix. The report is a
Markdown file with a top-line score (0–100) and a per-category breakdown.

Usage:
    python audit.py INPUT [-o OUTPUT.md] [--main-selector "article"]

INPUT can be either:
  - a local .html file path
  - a URL (auto-detected by http:// or https:// prefix)

Default output: ~/Documents/synergyAI/outputs/<stem>-audit.md
"""

from __future__ import annotations

import argparse
import asyncio
import json
import re
import sys
import time
from collections import Counter, deque
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional
from urllib.parse import urlparse, urljoin, urldefrag  # pure-python, safe in Pyodide

# NOTE: `ssl` and `urllib.request` are intentionally NOT imported at module
# level. They're socket-based and unavailable (or stubbed) in Pyodide / browser
# Python. We lazy-import them inside `_fetch_url_urllib` so the rest of the
# module — including the `audit_html()` Python API — loads and works fine in
# Pyodide environments where the frontend fetches HTML server-side and passes
# it in.

from bs4 import BeautifulSoup, Tag


DEFAULT_OUTPUT_DIR = Path.home() / "Documents" / "synergyAI" / "outputs"

BROWSER_UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
)

# Auto-detect for the article container if --main-selector is not given.
AUTO_ROOT_SELECTORS = [
    "main", "article", "[role='main']",
    "#content", "#main",
    ".post-content", ".entry-content", ".article-content",
]


# ---------------------------------------------------------------------------
# Reference tables — schema, AI crawlers, security headers
# ---------------------------------------------------------------------------

# Schema types whose Google rich-result support has been retired or deprecated.
# Recognising them in JSON-LD is itself a finding — owners frequently leave
# stale markup in place after support is dropped.
DEPRECATED_SCHEMA_TYPES = {
    "HowTo":              "rich results removed September 2023",
    "SpecialAnnouncement": "deprecated July 2025",
    "CourseInfo":          "retired June 2025",
    "EstimatedSalary":     "retired June 2025",
    "LearningVideo":       "retired June 2025",
    "ClaimReview":         "retired from rich results June 2025",
    "VehicleListing":      "retired from rich results June 2025",
    "PracticeProblem":     "retired from rich results late 2025",
    "Dataset":             "retired from rich results late 2025",
}

# Types restricted by Google to specific verticals. FAQPage still benefits
# AI/LLM citation outside Google rich-result eligibility.
RESTRICTED_SCHEMA_TYPES = {
    "FAQPage": "Google rich results restricted to government and healthcare sites since August 2023; still valuable for AI/LLM citation on commercial sites",
    "QAPage":  "community-Q&A only (Stack-Overflow-style sites); generic commercial pages should not use this",
}

# Minimum properties Google enforces for the most common @types. Missing any
# of these means the markup is invalid and yields no rich-result eligibility.
SCHEMA_REQUIRED_PROPS = {
    "Article":        ("headline", "author", "datePublished"),
    "BlogPosting":    ("headline", "author", "datePublished"),
    "NewsArticle":    ("headline", "author", "datePublished"),
    "Organization":   ("name", "url"),
    "LocalBusiness":  ("name", "address"),
    "Product":        ("name",),
    "Person":         ("name",),
    "WebSite":        ("name", "url"),
    "WebPage":        ("name",),
    "BreadcrumbList": ("itemListElement",),
    "Event":          ("name", "startDate", "location"),
    "Recipe":         ("name", "recipeIngredient", "recipeInstructions"),
    "VideoObject":    ("name", "description", "thumbnailUrl", "uploadDate"),
    "JobPosting":     ("title", "description", "datePosted", "hiringOrganization"),
    "Review":         ("itemReviewed", "reviewRating", "author"),
    "Course":         ("name", "description", "provider"),
}

# AI-crawler user-agent tokens in robots.txt. Each row: (owner, purpose).
# Blocking these reduces AI-search visibility differently per vendor:
# - GPTBot / Bytespider / Google-Extended: training corpus only
# - OAI-SearchBot / ChatGPT-User / PerplexityBot / Perplexity-User: live search index
# - CCBot: open-dataset crawler (Common Crawl) used by many model trainers
AI_CRAWLERS = {
    "GPTBot":            ("OpenAI", "ChatGPT training corpus"),
    "OAI-SearchBot":     ("OpenAI", "ChatGPT Search live index"),
    "ChatGPT-User":      ("OpenAI", "ChatGPT browsing in real time"),
    "ClaudeBot":         ("Anthropic", "Claude training & web features"),
    "anthropic-ai":      ("Anthropic", "Claude legacy training token"),
    "Claude-Web":        ("Anthropic", "Claude browsing in real time"),
    "PerplexityBot":     ("Perplexity", "Perplexity AI search index"),
    "Perplexity-User":   ("Perplexity", "Perplexity browsing in real time"),
    "Google-Extended":   ("Google", "Gemini training (does NOT control Googlebot or AI Overviews)"),
    "Bytespider":        ("ByteDance", "TikTok / Douyin AI training"),
    "CCBot":             ("Common Crawl", "training-corpus dataset crawler"),
    "cohere-ai":         ("Cohere", "Cohere model training"),
}

# Security headers AI engines and trust scanners look for. Severity is the
# weight when the header is missing.
SECURITY_HEADERS = {
    "Content-Security-Policy":  ("recommended", "Mitigates XSS by constraining script/style/connect sources. Trust scanners and crawl-classifiers weigh CSP presence."),
    "Strict-Transport-Security": ("recommended", "Forces HTTPS for subsequent requests, blocks protocol-downgrade attacks. Absent HSTS is a visible trust gap on HTTPS sites."),
    "X-Frame-Options":           ("nice-to-have", "Mitigates clickjacking. Largely superseded by CSP frame-ancestors but still surfaced in audits."),
    "X-Content-Type-Options":    ("nice-to-have", "'nosniff' prevents MIME-type sniffing on user-uploaded content."),
    "Referrer-Policy":           ("nice-to-have", "Controls Referer header leakage on outbound links."),
}


# ---------------------------------------------------------------------------
# Findings
# ---------------------------------------------------------------------------

SEVERITIES = ("critical", "recommended", "nice-to-have")
SEVERITY_WEIGHT = {"critical": 10, "recommended": 3, "nice-to-have": 1}


@dataclass
class Finding:
    severity: str           # one of SEVERITIES
    category: str           # cognitive | metadata | assets
    title: str              # short label
    detail: str             # what was wrong (factual, with numbers)
    why: str                # why it hurts AI citation
    fix: str                # concrete remediation


@dataclass
class AuditResult:
    findings: list[Finding] = field(default_factory=list)
    strengths: list[str] = field(default_factory=list)
    stats: dict = field(default_factory=dict)

    def add(self, f: Finding) -> None:
        self.findings.append(f)

    def by(self, category: str) -> list[Finding]:
        return [f for f in self.findings if f.category == category]

    def by_severity(self, sev: str) -> list[Finding]:
        return [f for f in self.findings if f.severity == sev]

    def score(self) -> int:
        deduction = sum(SEVERITY_WEIGHT[f.severity] for f in self.findings)
        return max(0, 100 - deduction)

    def grade(self) -> str:
        s = self.score()
        if s >= 90: return "A"
        if s >= 80: return "B"
        if s >= 70: return "C"
        if s >= 60: return "D"
        return "F"


# ---------------------------------------------------------------------------
# Input acquisition
# ---------------------------------------------------------------------------

def load_input(target: str) -> tuple[str, str]:
    """Return (html, source_label).

    Accepts a URL, a bare domain (auto-prepends https://), or a file path."""
    target = target.strip()
    # Already a full URL
    if re.match(r"^https?://", target, re.I):
        return _fetch_url(target), target
    # Looks like a file we can read
    p = Path(target).expanduser()
    if p.exists() and p.is_file():
        return p.read_text(encoding="utf-8", errors="replace"), str(p)
    # Looks like a bare domain or domain+path (e.g. "finmind.org",
    # "example.com/article", "www.foo.org") — auto-prepend https://
    if _looks_like_bare_domain(target):
        url = "https://" + target
        return _fetch_url(url), url
    # Neither a URL, nor a file, nor a domain-like string — probably a typo.
    raise FileNotFoundError(
        f"{target!r} is not a URL, a readable file, or a recognizable domain. "
        "Pass a full URL like 'https://example.com', a domain like 'example.com', "
        "or a path to a local .html file."
    )


def _looks_like_bare_domain(s: str) -> bool:
    # Strip optional path/query for the check
    host = s.split("/", 1)[0]
    # Reject things that obviously aren't domains
    if "@" in s or s.startswith(("./", "../", "/")) or s.endswith(".html"):
        return False
    # Must have at least one dot, and the TLD piece must be 2+ letters
    parts = host.split(".")
    if len(parts) < 2 or len(parts[-1]) < 2:
        return False
    if not re.match(r"^[a-zA-Z0-9.\-]+$", host):
        return False
    return True


def _is_pyodide() -> bool:
    """True when running under Pyodide (Python-in-the-browser)."""
    return sys.platform == "emscripten"


def _fetch_url(url: str) -> str:
    """Fetch a URL and return its decoded body. Branches on Pyodide vs CPython
    because the browser environment can't use urllib's socket-based requests."""
    if _is_pyodide():
        return _fetch_url_pyodide(url)
    return _fetch_url_urllib(url)


def _fetch_url_pyodide(url: str) -> str:
    """Pyodide path: cross-origin fetching from inside browser-Python is
    blocked by browser CORS for virtually all production sites. Rather than
    trigger a noisy console error by attempting open_url and being blocked,
    refuse immediately with an actionable message telling the frontend
    integration what to do.

    The right pattern in a Pyodide environment is:
      1. Frontend's server-side code (PHP, Node, etc.) fetches the URL.
      2. Frontend hands the HTML string to Pyodide as a Python global.
      3. Pyodide calls `audit.audit_html_to_markdown(html, source=url)`.

    The script never touches the network in step 3.
    """
    raise RuntimeError(
        f"Cannot fetch {url} from inside Pyodide. Browser security (CORS) "
        f"blocks cross-origin XHR for sites that don't send "
        f"Access-Control-Allow-Origin headers — which is virtually every "
        f"production website.\n\n"
        f"FIX: Fetch the HTML *outside* Python (your frontend's PHP / Node "
        f"server-side code can do this without CORS restrictions), then "
        f"pass the HTML to the audit. Two ways:\n\n"
        f"  Python API (preferred — no subprocess needed):\n"
        f"    import audit\n"
        f"    report_md = audit.audit_html_to_markdown(html, source={url!r})\n\n"
        f"  Or via stdin from a shell:\n"
        f"    echo \"$HTML\" | python audit.py --stdin --source {url}\n\n"
        f"Do NOT retry by calling the script with the URL again — it will "
        f"fail the same way."
    )


def _fetch_url_urllib(url: str) -> str:
    """Standard CPython path: urllib + gzip/deflate decompression + charset
    detection. ssl + urllib.request are imported here so the rest of the module
    can load in Pyodide (which lacks socket/SSL support)."""
    import gzip
    import ssl
    import urllib.request
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    req = urllib.request.Request(url, headers={
        "User-Agent": BROWSER_UA,
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.9",
        "Accept-Encoding": "gzip, deflate",
    })
    with urllib.request.urlopen(req, timeout=30, context=ctx) as r:
        body = r.read()
        encoding = (r.headers.get("Content-Encoding") or "").lower()
        content_type = r.headers.get("Content-Type") or ""

    # Handle compressed responses (urllib doesn't decompress automatically).
    if encoding == "gzip":
        body = gzip.decompress(body)
    elif encoding == "deflate":
        import zlib
        try:
            body = zlib.decompress(body)
        except zlib.error:
            body = zlib.decompress(body, -zlib.MAX_WBITS)

    # Detect charset: prefer Content-Type header, else look for <meta charset>,
    # else UTF-8.
    charset = None
    m = re.search(r"charset=([\w\-]+)", content_type, re.I)
    if m:
        charset = m.group(1).lower()
    if not charset:
        head = body[:4096]
        m = re.search(rb'<meta[^>]+charset=["\']?([\w\-]+)', head, re.I)
        if m:
            charset = m.group(1).decode("ascii", errors="replace").lower()
    if not charset:
        charset = "utf-8"
    try:
        return body.decode(charset, errors="replace")
    except LookupError:
        return body.decode("utf-8", errors="replace")


# ---------------------------------------------------------------------------
# Pick article root
# ---------------------------------------------------------------------------

def pick_root(soup: BeautifulSoup, main_selector: str | None) -> Tag:
    if main_selector:
        picked = soup.select_one(main_selector)
        if picked is not None:
            return picked
    for sel in AUTO_ROOT_SELECTORS:
        picked = soup.select_one(sel)
        if picked is not None and picked.get_text(strip=True):
            return picked
    if soup.body is not None:
        return soup.body
    return soup


# ---------------------------------------------------------------------------
# Pass 1: cognitive structure
# ---------------------------------------------------------------------------

def audit_cognitive(soup: BeautifulSoup, root: Tag, result: AuditResult) -> None:
    cat = "cognitive"

    # 1. H1 uniqueness — exactly 1 H1 in the document
    h1s = soup.find_all("h1")
    if len(h1s) == 0:
        result.add(Finding("critical", cat,
            "No H1 found",
            "The document contains zero <h1> tags.",
            "AI search relies on the H1 to identify the page topic. Without one, the engine falls back to <title> or guesses, often picking the wrong subject.",
            "Add a single descriptive <h1> at the top of the article that states what the page is about.",
        ))
    elif len(h1s) > 1:
        result.add(Finding("recommended", cat,
            f"Multiple H1s ({len(h1s)})",
            f"Found {len(h1s)} <h1> tags: {', '.join(repr(h.get_text(' ', strip=True)[:40]) for h in h1s[:3])}.",
            "Multiple H1s confuse topic detection. AI engines may pick the wrong one as the page title.",
            "Use exactly one <h1>. Demote the others to <h2> or <h3>.",
        ))
    else:
        result.strengths.append(f"Single, clear H1: {h1s[0].get_text(' ', strip=True)[:80]!r}")

    # 2. Heading hierarchy — no skipped levels
    headings = root.find_all(["h1", "h2", "h3", "h4", "h5", "h6"])
    levels = [int(h.name[1]) for h in headings]
    skips = []
    for i in range(1, len(levels)):
        diff = levels[i] - levels[i - 1]
        if diff > 1:
            skips.append((levels[i - 1], levels[i], headings[i].get_text(" ", strip=True)[:50]))
    if skips:
        sample = "; ".join(f"H{a}→H{b} at {t!r}" for a, b, t in skips[:3])
        result.add(Finding("recommended", cat,
            f"Heading hierarchy skips ({len(skips)})",
            f"Headings jump levels {len(skips)} time(s): {sample}.",
            "Skipped heading levels break the outline tree LLMs use to weight section importance.",
            "Don't jump levels. After H2, use H3, not H4. Demote/promote to fill the gap.",
        ))

    # 3. Section length distribution
    sections = _split_sections(root)
    result.stats["section_count"] = len(sections)
    thin = [s for s in sections if 0 < s["word_count"] < 40]
    bloated = [s for s in sections if s["word_count"] > 400]
    empty = [s for s in sections if s["word_count"] == 0]
    if empty:
        result.add(Finding("recommended", cat,
            f"Empty sections ({len(empty)})",
            f"{len(empty)} heading(s) have no body text before the next heading: "
            + "; ".join(repr(s['heading'][:40]) for s in empty[:3]),
            "Empty sections look like broken structure to AI engines and waste prime snippet real estate.",
            "Either remove the empty heading or write at least a short lead paragraph under it.",
        ))
    if thin:
        result.add(Finding("recommended", cat,
            f"Thin sections ({len(thin)})",
            f"{len(thin)} section(s) under 40 words: "
            + "; ".join(f"{s['heading'][:30]!r} ({s['word_count']}w)" for s in thin[:3]),
            "AI overviews extract 50–150 word snippets. Sections under 40 words rarely get cited because there's nothing substantial to lift.",
            "Expand each thin section to 80–250 words with a thesis sentence + supporting detail.",
        ))
    if bloated:
        result.add(Finding("nice-to-have", cat,
            f"Bloated sections ({len(bloated)})",
            f"{len(bloated)} section(s) over 400 words: "
            + "; ".join(f"{s['heading'][:30]!r} ({s['word_count']}w)" for s in bloated[:3]),
            "Very long sections force AI engines to summarize aggressively, often losing the precise claim you want cited.",
            "Split into subsections (H3) of 80–250 words each.",
        ))
    if not (thin or bloated or empty) and sections:
        result.strengths.append(f"All {len(sections)} sections are appropriately sized (40–400 words).")

    # 4. Lead-sentence check — first 25 words of each section's first paragraph
    weak_leads = []
    for s in sections:
        if s["word_count"] < 30:
            continue
        first_para = s["first_paragraph"]
        if not first_para:
            continue
        words = first_para.split()[:25]
        # Heuristic: lead is weak if it doesn't repeat a content word from the heading.
        heading_words = {w.lower() for w in re.findall(r"\w+", s["heading"]) if len(w) > 3}
        para_words = {w.lower() for w in re.findall(r"\w+", " ".join(words))}
        if heading_words and not heading_words & para_words:
            weak_leads.append(s)
    if weak_leads and len(sections) >= 3:
        ratio = len(weak_leads) / len(sections)
        if ratio > 0.5:
            result.add(Finding("recommended", cat,
                f"Weak lead sentences ({len(weak_leads)}/{len(sections)})",
                f"In {len(weak_leads)} of {len(sections)} sections, the first 25 words don't echo the heading's topic.",
                "AI engines weight the first sentence heavily when picking snippets. A lead that doesn't address the heading buries the answer.",
                "Open each section with a sentence that restates the heading's topic and provides the section's thesis. Don't bury it in paragraph 2+.",
            ))

    # 5. List & table density
    lists = root.find_all(["ul", "ol"])
    tables = root.find_all("table")
    enum_density = (len(lists) + len(tables)) / max(1, len(sections))
    result.stats["list_count"] = len(lists)
    result.stats["table_count"] = len(tables)
    if enum_density < 0.15 and len(sections) >= 4:
        result.add(Finding("recommended", cat,
            "Low enumeration density",
            f"Only {len(lists)} list(s) and {len(tables)} table(s) across {len(sections)} sections.",
            "AI overviews quote lists and tables ~3× more often than equivalent prose because they're easier to extract verbatim.",
            "Convert enumerable claims (steps, options, comparisons, criteria) into <ul>/<ol> or <table>.",
        ))
    elif enum_density >= 0.3:
        result.strengths.append(f"Good enumeration density ({len(lists)} lists, {len(tables)} tables).")

    # 6. Citation density — % of paragraphs containing an outbound link
    paragraphs = root.find_all("p")
    paras_with_link = sum(
        1 for p in paragraphs
        if any((a.get("href") or "").startswith(("http://", "https://"))
               and not _same_host(a.get("href"), result.stats.get("source_host"))
               for a in p.find_all("a"))
    )
    if paragraphs:
        cite_ratio = paras_with_link / len(paragraphs)
        result.stats["citation_density"] = round(cite_ratio, 3)
        if cite_ratio < 0.10:
            result.add(Finding("recommended", cat,
                f"Low citation density ({cite_ratio:.0%})",
                f"Only {paras_with_link} of {len(paragraphs)} paragraphs link to an external source.",
                "AI engines favor pages that cite their claims — it's a strong authority signal and matches the 'show your work' pattern they themselves are tuned to reward.",
                "Link supporting claims to primary sources. Aim for ≥30% of factual paragraphs to contain a citation.",
            ))
        elif cite_ratio >= 0.30:
            result.strengths.append(f"Strong citation density ({cite_ratio:.0%} of paragraphs cite external sources).")

    # 7. Long paragraphs — bad for snippet extraction
    long_paras = [p for p in paragraphs if len(p.get_text().split()) > 120]
    if long_paras and paragraphs:
        ratio = len(long_paras) / len(paragraphs)
        if ratio > 0.20:
            result.add(Finding("nice-to-have", cat,
                f"Long paragraphs ({len(long_paras)})",
                f"{len(long_paras)} of {len(paragraphs)} paragraphs are over 120 words.",
                "Long paragraphs force LLMs to summarize, which loses precision. Short paragraphs are quoted verbatim.",
                "Break paragraphs over 120 words. One claim per paragraph is the snippet sweet spot.",
            ))

    # 8. HTML5 semantic landmarks
    # Major landmarks help parsers separate primary content from chrome.
    # AI engines use them to locate the article body; without them they fall
    # back to heuristics that often include nav/sidebar/footer text.
    body = soup.find("body") or soup
    has_main = soup.find("main") is not None or soup.find(attrs={"role": "main"}) is not None
    has_article = soup.find("article") is not None
    has_header = soup.find("header") is not None
    has_nav = soup.find("nav") is not None or soup.find(attrs={"role": "navigation"}) is not None
    has_footer = soup.find("footer") is not None

    if not has_main and not has_article:
        result.add(Finding("critical", cat,
            "No <main> or <article> landmark",
            "The page uses neither <main> nor <article> to delimit primary content.",
            "AI crawlers locate the main content region via these landmarks. Without them they include nav/sidebar/footer text in the 'article', diluting topic signals and producing worse citations.",
            "Wrap the primary content in <main> (one per page) or <article> (for self-contained content like a blog post or news story).",
        ))

    missing_landmarks = []
    if not has_header: missing_landmarks.append("<header>")
    if not has_nav: missing_landmarks.append("<nav>")
    if not has_footer: missing_landmarks.append("<footer>")
    if missing_landmarks:
        result.add(Finding("recommended", cat,
            f"Missing semantic landmark(s): {', '.join(missing_landmarks)}",
            f"The page has no {', '.join(missing_landmarks)} element(s).",
            "Semantic landmarks let parsers separate chrome (masthead, navigation, footer) from content. Pages built entirely with <div>s force AI engines to guess what's content vs. boilerplate.",
            "Replace structural <div>s with the appropriate HTML5 landmark: site banner → <header>, primary nav → <nav>, site footer → <footer>.",
        ))
    elif (has_main or has_article) and has_header and has_nav and has_footer:
        result.strengths.append("Full HTML5 landmark set (main/article, header, nav, footer).")

    # 9. Div-soup detection — page uses no semantic structural tags at all
    semantic_tags = body.find_all(["article", "section", "header", "footer", "nav", "main", "aside"])
    if not semantic_tags:
        result.add(Finding("critical", cat,
            "No semantic HTML5 tags (div soup)",
            "The body contains zero semantic elements (no <article>, <section>, <header>, <footer>, <nav>, <main>, or <aside>).",
            "Pages built entirely from <div>s leave AI parsers with no structural signals beyond headings. Section boundaries, content vs. chrome, hierarchy all disappear.",
            "Refactor the major structural <div>s to semantic equivalents: page wrapper → <main>, top region → <header>, repeated content units → <article>, sub-units → <section>.",
        ))

    # 10. Content not wrapped in <p> tags
    # If the body has substantial visible text but only a handful of <p>s, the
    # text is likely in <div>s or <span>s — invisible-as-paragraphs to snippet
    # extractors.
    body_text_words = len(body.get_text(" ", strip=True).split()) if body else 0
    root_p_count = len(root.find_all("p"))
    expected_p = max(3, body_text_words // 200)
    if body_text_words >= 300 and root_p_count < expected_p:
        result.add(Finding("recommended", cat,
            f"Little content in <p> tags ({root_p_count} <p>(s) for ~{body_text_words} body words)",
            f"The body has ~{body_text_words} words of visible text but only {root_p_count} <p> element(s) in the article root (expected ≥{expected_p}).",
            "AI engines extract snippets primarily from <p> tags. Prose sitting in <div>s or <span>s is harder to lift cleanly and may be skipped entirely.",
            "Wrap paragraph-like content in <p> tags rather than <div>s. One <p> per paragraph of prose.",
        ))

    # 11. Headings faked with styled <div>/<span>
    # Heuristic: <div>/<span> whose class names look heading-ish (h1..h6,
    # heading, title, headline). Catches the obvious "designer styled a div
    # like a heading" pattern. Nice-to-have because some false positives are
    # expected.
    fake_heading_re = re.compile(
        r"(?:^|[\s_-])(?:h[1-6]|heading|title|headline)(?:$|[\s_-])",
        re.I,
    )
    fake_headings = []
    for el in body.find_all(["div", "span"], class_=True):
        cls = " ".join(el.get("class") or [])
        if fake_heading_re.search(cls):
            text = el.get_text(" ", strip=True)
            if text and len(text) < 200:
                fake_headings.append((cls, text[:50]))
    if fake_headings:
        result.add(Finding("nice-to-have", cat,
            f"Headings styled as <div>/<span> ({len(fake_headings)})",
            f"{len(fake_headings)} element(s) look like headings (class names: 'h1', 'title', 'heading'…) but use <div> or <span> instead of real heading tags. Examples: "
            + "; ".join(f"{cls!r}: {text!r}" for cls, text in fake_headings[:3]),
            "Visually-styled headings are invisible to AI engines that walk the heading outline (H1→H2→H3). They contribute no structural signal even though they look hierarchical to a human reader.",
            "Replace <div class='h2'>…</div> with <h2>…</h2>. CSS can preserve the styling.",
        ))


_CARD_CLASS_RE = re.compile(
    r"(?:^|[\s_-])(card|tile|grid-item|grid__item|item-card|teaser|thumb|thumbnail)(?:$|[\s_-])",
    re.I,
)


def _is_card_descendant(el: Tag) -> bool:
    """True if the element sits inside a card/tile/grid-item container.

    Card grids (article tiles, project tiles) wrap each entry in its own
    container with a short heading + blurb. Treating each card heading as a
    distinct content section produces dozens of false-positive 'thin section'
    findings. We skip them so card content folds into the parent section."""
    for ancestor in el.parents:
        if not isinstance(ancestor, Tag):
            continue
        cls = ancestor.get("class")
        if cls and _CARD_CLASS_RE.search(" ".join(cls)):
            return True
    return False


def _split_sections(root: Tag) -> list[dict]:
    """Walk the root and segment into sections by H2/H3. Returns dicts with
    heading text, word count of body text, and the first paragraph text."""
    sections = []
    current = None
    for el in root.descendants:
        if not isinstance(el, Tag):
            continue
        if el.name in ("h2", "h3"):
            if _is_card_descendant(el):
                # Card heading — fold its blurb into the surrounding section
                # rather than treating it as its own content section.
                continue
            if current is not None:
                sections.append(current)
            current = {"heading": el.get_text(" ", strip=True), "level": int(el.name[1]),
                       "body_text": "", "first_paragraph": "", "word_count": 0}
        elif current is not None and el.name == "p":
            text = el.get_text(" ", strip=True)
            if text:
                if not current["first_paragraph"]:
                    current["first_paragraph"] = text
                current["body_text"] += " " + text
    if current is not None:
        sections.append(current)
    for s in sections:
        s["word_count"] = len(s["body_text"].split())
    return sections


def _same_host(href: str | None, host: str | None) -> bool:
    if not href or not host:
        return False
    try:
        return urlparse(href).netloc.lower() == host.lower()
    except Exception:
        return False


# ---------------------------------------------------------------------------
# Pass 2: machine-readable metadata
# ---------------------------------------------------------------------------

def audit_metadata(soup: BeautifulSoup, result: AuditResult) -> None:
    cat = "metadata"

    # JSON-LD schema
    ld_blocks = soup.find_all("script", type="application/ld+json")
    types: list[str] = []
    flat_items: list[dict] = []   # all leaf items, used for required-property validation
    for b in ld_blocks:
        if not b.string:
            continue
        try:
            data = json.loads(b.string)
        except json.JSONDecodeError:
            result.add(Finding("recommended", cat,
                "Malformed JSON-LD",
                "Found a <script type='application/ld+json'> block that doesn't parse as JSON.",
                "AI engines silently skip broken structured data. The page loses any signal it would have provided.",
                "Validate the JSON-LD against schema.org. Common issues: trailing commas, unescaped quotes inside string values.",
            ))
            continue
        items = data if isinstance(data, list) else [data]
        for item in items:
            if not isinstance(item, dict):
                continue
            if item.get("@type"):
                t = item["@type"]
                types.extend(t if isinstance(t, list) else [t])
                flat_items.append(item)
            # Walk into @graph envelope (common pattern: outer object has no
            # @type, real entities live nested under @graph).
            nested = item.get("@graph")
            if isinstance(nested, list):
                for sub in nested:
                    if isinstance(sub, dict) and sub.get("@type"):
                        t = sub["@type"]
                        types.extend(t if isinstance(t, list) else [t])
                        flat_items.append(sub)

    # Microdata / RDFa fallback detection — only flag when no JSON-LD exists
    # OR when these formats coexist with JSON-LD (worth noting in either case).
    microdata_items = soup.find_all(attrs={"itemscope": True})
    rdfa_items = soup.find_all(attrs={"typeof": True})

    if not ld_blocks:
        if microdata_items or rdfa_items:
            fmt = "Microdata" if microdata_items else "RDFa"
            sample_types = [el.get("itemtype") or el.get("typeof") for el in (microdata_items[:3] if microdata_items else rdfa_items[:3])]
            sample_types = [t for t in sample_types if t]
            result.add(Finding("critical", cat,
                f"Structured data uses {fmt}, not JSON-LD",
                f"Found {len(microdata_items or rdfa_items)} {fmt} block(s) but no JSON-LD. Sample type(s): {', '.join(sample_types) or 'unspecified'}.",
                "Google explicitly prefers JSON-LD; Microdata and RDFa are still parsed but receive less robust support and are harder for AI engines to extract cleanly because the markup is interleaved with HTML.",
                f"Migrate the {fmt} declarations to a JSON-LD block in <head> with the same @type values. Most CMS schema plugins can output JSON-LD directly.",
            ))
        else:
            result.add(Finding("critical", cat,
                "No JSON-LD structured data",
                "No <script type='application/ld+json'> block in the page.",
                "Schema.org JSON-LD is the single highest-impact AI-search signal. It's what tells Google's AI Overview that this is an Article (vs a product page vs a recipe), who wrote it, and when. Without it the page is effectively anonymous to structured-data consumers.",
                "Add a JSON-LD block with at minimum: @type Article, headline, datePublished, author. For products use Product + Offer; for businesses use Organization or LocalBusiness.",
            ))
    else:
        result.stats["jsonld_types"] = types
        result.strengths.append(f"JSON-LD present with @type(s): {', '.join(types) or 'unspecified'}")

        # Coexisting Microdata/RDFa with JSON-LD — informational
        if microdata_items or rdfa_items:
            other_fmt = "Microdata" if microdata_items else "RDFa"
            result.add(Finding("nice-to-have", cat,
                f"{other_fmt} coexists with JSON-LD",
                f"Page has both JSON-LD ({len(ld_blocks)} block(s)) and {other_fmt} ({len(microdata_items or rdfa_items)} item(s)).",
                "Duplicate structured-data formats don't compound — they risk drift between formats. AI engines extract from the most reliable source and skip the rest.",
                f"Pick JSON-LD as the single source of truth. Remove the {other_fmt} declarations once you've confirmed JSON-LD covers the same @type values.",
            ))

    # Deprecated schema types (Google retired their rich-result support)
    deprecated_present = [t for t in types if t in DEPRECATED_SCHEMA_TYPES]
    if deprecated_present:
        details = "; ".join(f"{t} ({DEPRECATED_SCHEMA_TYPES[t]})" for t in deprecated_present)
        result.add(Finding("recommended", cat,
            f"Deprecated schema type(s): {', '.join(deprecated_present)}",
            f"Page declares: {details}.",
            "Deprecated rich-result types yield no SERP enhancement. They still parse, but the markup contributes no AI-discoverability value beyond generic schema and may signal stale maintenance to crawlers.",
            "Remove the deprecated @type(s) and replace with an actively supported alternative (e.g. HowTo → Article with step list inside the body, SpecialAnnouncement → NewsArticle).",
        ))

    # Restricted schema types — info-level (FAQPage still helps AI citation)
    restricted_present = [t for t in types if t in RESTRICTED_SCHEMA_TYPES]
    if restricted_present:
        details = "; ".join(f"{t} ({RESTRICTED_SCHEMA_TYPES[t]})" for t in restricted_present)
        result.add(Finding("nice-to-have", cat,
            f"Restricted schema type(s): {', '.join(restricted_present)}",
            f"Page declares: {details}.",
            "FAQPage/QAPage no longer trigger Google rich results outside government/healthcare/community-Q&A verticals, but AI engines (ChatGPT, Perplexity, Bing Copilot) still extract structured Q&A blocks for citation.",
            "Keep FAQPage if AI citation is a goal; remove it if you only care about Google rich results. Don't add new FAQPage markup expecting Google SERP enhancement on commercial sites.",
        ))

    # Required-property validation per @type
    missing_props_findings: list[tuple[str, list[str]]] = []
    for item in flat_items:
        ts = item.get("@type")
        ts = ts if isinstance(ts, list) else [ts]
        for t in ts:
            if t in SCHEMA_REQUIRED_PROPS:
                missing = [p for p in SCHEMA_REQUIRED_PROPS[t] if not item.get(p)]
                if missing:
                    missing_props_findings.append((t, missing))
    if missing_props_findings:
        # Collapse duplicates by @type
        by_type: dict[str, set[str]] = {}
        for t, missing in missing_props_findings:
            by_type.setdefault(t, set()).update(missing)
        sample = "; ".join(f"{t} missing {', '.join(sorted(props))}" for t, props in list(by_type.items())[:5])
        result.add(Finding("recommended", cat,
            f"JSON-LD missing required properties ({len(by_type)} type(s))",
            f"At least one schema block fails Google's required-property check: {sample}.",
            "Missing required properties make the markup invalid for the relevant rich-result eligibility. Google may still index the page but the structured-data signal is wasted.",
            "Fill in the missing properties. Use the schema.org type page and Google's structured-data documentation as the authoritative spec.",
        ))

    # Twitter Card
    twitter_meta = soup.find_all("meta", attrs={"name": re.compile(r"^twitter:", re.I)})
    twitter_present = {m.get("name", "").lower(): (m.get("content") or "").strip() for m in twitter_meta}
    if not twitter_meta:
        result.add(Finding("nice-to-have", cat,
            "No Twitter Card meta tags",
            "Page declares no <meta name='twitter:*'> tags.",
            "Twitter/X (and Slack, Discord, LinkedIn, many AI crawlers) read Twitter Card metadata to render link previews. Pages without it render as bare URLs in those surfaces.",
            "Add at minimum: <meta name='twitter:card' content='summary_large_image'>, <meta name='twitter:title'>, <meta name='twitter:description'>, <meta name='twitter:image'>.",
        ))
    elif "twitter:card" in twitter_present and not twitter_present.get("twitter:title"):
        result.add(Finding("nice-to-have", cat,
            "Twitter Card incomplete",
            f"twitter:card declared ('{twitter_present.get('twitter:card')}') but twitter:title is missing.",
            "Without twitter:title the social preview falls back to og:title or the <title> tag — usable, but specifying explicitly avoids surprises when one drifts from the other.",
            "Add <meta name='twitter:title'>, <meta name='twitter:description'>, and <meta name='twitter:image'> to round out the card.",
        ))

    # Meta description
    md = soup.find("meta", attrs={"name": "description"})
    md_content = (md.get("content") or "").strip() if md else ""
    if not md_content:
        result.add(Finding("critical", cat,
            "No meta description",
            "<meta name='description'> is missing or empty.",
            "AI overviews and search engines often quote the meta description as the snippet when no better one is found in the body.",
            "Add a 120–160 character <meta name='description'> that summarizes the article's thesis in one sentence.",
        ))
    elif len(md_content) < 70:
        result.add(Finding("recommended", cat,
            f"Meta description too short ({len(md_content)} chars)",
            f"The description is only {len(md_content)} characters: {md_content!r}",
            "Short descriptions don't fill the snippet slot, leaving the engine to guess from body text.",
            "Aim for 120–160 characters. Include the page's specific topic, not generic phrases.",
        ))
    elif len(md_content) > 200:
        result.add(Finding("nice-to-have", cat,
            f"Meta description too long ({len(md_content)} chars)",
            f"Description is {len(md_content)} characters; engines truncate around 160.",
            "Beyond ~160 chars the tail is hidden in SERP snippets and AI engines may treat the truncation point as a sentence boundary, breaking meaning.",
            "Trim to 120–160 characters and put the most important phrase first.",
        ))
    else:
        result.strengths.append(f"Meta description well-sized ({len(md_content)} chars).")

    # Open Graph
    og_required = {"og:title", "og:description", "og:type"}
    og_present = {m.get("property"): (m.get("content") or "").strip()
                  for m in soup.find_all("meta", property=re.compile(r"^og:"))}
    missing_og = og_required - {k for k, v in og_present.items() if v}
    if missing_og:
        result.add(Finding("critical", cat,
            f"Missing Open Graph tag(s): {', '.join(sorted(missing_og))}",
            f"Found OG tags: {', '.join(og_present.keys()) or 'none'}.",
            "Open Graph is what AI engines (and social previews) read to extract the page's identity. Missing OG = the page renders as a blob with no clear title or summary in any preview surface.",
            "Add at least: <meta property='og:title'>, <meta property='og:description'>, <meta property='og:type' content='article'>.",
        ))
    elif "og:image" not in og_present:
        result.add(Finding("nice-to-have", cat,
            "No og:image",
            "All required OG tags present, but og:image is missing.",
            "AI overviews increasingly render image previews; pages with og:image surface more prominently.",
            "Add <meta property='og:image' content='https://…'> pointing to a representative 1200×630 image.",
        ))
    else:
        result.strengths.append("Complete Open Graph tags (title, description, type, image).")

    # Canonical
    canonical = soup.find("link", rel="canonical")
    if not canonical or not (canonical.get("href") or "").strip():
        result.add(Finding("recommended", cat,
            "No canonical link",
            "<link rel='canonical'> is missing.",
            "Without a canonical, AI engines may treat duplicate URLs (with/without trailing slash, with tracking params) as separate pages, splitting authority signals.",
            "Add <link rel='canonical' href='https://your-domain/your-url'> in <head>.",
        ))

    # Robots
    robots = soup.find("meta", attrs={"name": "robots"})
    robots_content = ((robots.get("content") if robots else "") or "").lower()
    if "noindex" in robots_content:
        result.add(Finding("critical", cat,
            "Page set to noindex",
            f"<meta name='robots' content='{robots_content}'> blocks indexing.",
            "If 'noindex' is intentional this is fine. If not, the page is invisible to all search and AI engines.",
            "Remove 'noindex' from the robots meta if you want the page to be cited.",
        ))

    # Language attribute
    html_tag = soup.find("html")
    if html_tag and not html_tag.get("lang"):
        result.add(Finding("nice-to-have", cat,
            "No lang attribute on <html>",
            "<html> tag has no 'lang' attribute.",
            "Language detection helps AI engines route the page to the right linguistic audience.",
            "Add e.g. <html lang='en'> or 'fr', 'es', etc.",
        ))


# ---------------------------------------------------------------------------
# Pass 2b: content quality (E-E-A-T, readability, freshness, link ratios)
# ---------------------------------------------------------------------------

# Vowel groups for the syllable counter. The Flesch formula is a heuristic;
# accuracy in the 60-90% range against gold-standard counts is enough for
# scoring at the page level.
_VOWEL_GROUP = re.compile(r"[aeiouy]+", re.I)
_SILENT_E = re.compile(r"e$", re.I)


def _count_syllables(word: str) -> int:
    """Crude English syllable counter — drops trailing silent 'e', counts vowel
    groups, floors at 1. Good enough for Flesch at the document level."""
    w = word.lower().strip()
    if not w:
        return 0
    if len(w) <= 3:
        return 1
    w = _SILENT_E.sub("", w)
    groups = _VOWEL_GROUP.findall(w)
    return max(1, len(groups))


def _flesch_reading_ease(text: str) -> tuple[float, int, int, int] | None:
    """Compute Flesch Reading Ease score on raw text.

    Returns (score, sentence_count, word_count, syllable_count) or None when
    there isn't enough text to score meaningfully (< 100 words).
    """
    # Sentence count: split on .!? (handles common abbreviations crudely).
    sentences = [s for s in re.split(r"[.!?]+", text) if s.strip()]
    sentence_count = max(1, len(sentences))
    words = re.findall(r"[A-Za-z]+(?:'[A-Za-z]+)?", text)
    word_count = len(words)
    if word_count < 100:
        return None
    syllable_count = sum(_count_syllables(w) for w in words)
    # Standard Flesch Reading Ease formula
    score = 206.835 - 1.015 * (word_count / sentence_count) - 84.6 * (syllable_count / word_count)
    return (round(score, 1), sentence_count, word_count, syllable_count)


def _flesch_band(score: float) -> str:
    if score >= 90: return "very easy (5th grade)"
    if score >= 80: return "easy (6th grade)"
    if score >= 70: return "fairly easy (7th grade)"
    if score >= 60: return "plain English (8-9th grade)"
    if score >= 50: return "fairly difficult (10-12th grade)"
    if score >= 30: return "difficult (college)"
    return "very difficult (college graduate)"


def _detect_author_signal(soup: BeautifulSoup) -> tuple[bool, list[str]]:
    """Return (has_author, evidence_list).

    Looks for: <meta name="author">, <link rel="author">, JSON-LD Person/author,
    elements with rel="author" or class names matching byline patterns.
    """
    evidence: list[str] = []

    meta_author = soup.find("meta", attrs={"name": "author"})
    if meta_author and (meta_author.get("content") or "").strip():
        evidence.append(f"meta[name=author] = {(meta_author.get('content') or '')[:60]!r}")

    link_author = soup.find("link", attrs={"rel": "author"})
    if link_author and link_author.get("href"):
        evidence.append("link[rel=author]")

    rel_author = soup.find(attrs={"rel": "author"})
    if rel_author and rel_author.name != "link":
        evidence.append(f"<{rel_author.name} rel='author'>")

    # JSON-LD author / Person
    for block in soup.find_all("script", type="application/ld+json"):
        if not block.string:
            continue
        try:
            data = json.loads(block.string)
        except json.JSONDecodeError:
            continue
        items = data if isinstance(data, list) else [data]
        stack = list(items)
        while stack:
            it = stack.pop()
            if not isinstance(it, dict):
                continue
            t = it.get("@type")
            if t == "Person" or (isinstance(t, list) and "Person" in t):
                evidence.append(f"JSON-LD Person ({(it.get('name') or '')[:40]!r})")
                break
            author = it.get("author")
            if isinstance(author, dict):
                stack.append(author)
                continue
            if isinstance(author, list):
                stack.extend(a for a in author if isinstance(a, dict))
                continue
            if isinstance(author, str) and author.strip():
                evidence.append(f"JSON-LD author = {author[:40]!r}")
                break
            graph = it.get("@graph")
            if isinstance(graph, list):
                stack.extend(g for g in graph if isinstance(g, dict))

    # Byline-class heuristic — common patterns: 'byline', 'author', 'writer',
    # 'post-author', 'article-author', 'meta-author'
    byline_re = re.compile(r"(^|[\s_-])(byline|author|writer|by-line|post-author|article-author|meta-author)($|[\s_-])", re.I)
    for el in soup.find_all(class_=byline_re):
        text = el.get_text(" ", strip=True)
        if text and len(text) < 200:
            evidence.append(f"byline element: {text[:50]!r}")
            break

    return (bool(evidence), evidence)


_DATE_PROPS = ("datePublished", "dateModified", "dateCreated")


def _extract_dates(soup: BeautifulSoup) -> dict[str, str]:
    """Return a dict of date-kind → ISO string when discoverable.

    Sources, in priority order:
      1. JSON-LD @type Article/BlogPosting/NewsArticle datePublished + dateModified
      2. <meta property="article:published_time"> and "article:modified_time"
      3. <meta name="date">
    """
    found: dict[str, str] = {}

    # JSON-LD
    for block in soup.find_all("script", type="application/ld+json"):
        if not block.string:
            continue
        try:
            data = json.loads(block.string)
        except json.JSONDecodeError:
            continue
        items = data if isinstance(data, list) else [data]
        stack = list(items)
        while stack:
            it = stack.pop()
            if not isinstance(it, dict):
                continue
            for prop in _DATE_PROPS:
                if isinstance(it.get(prop), str) and prop not in found:
                    found[prop] = it[prop]
            graph = it.get("@graph")
            if isinstance(graph, list):
                stack.extend(g for g in graph if isinstance(g, dict))

    # OpenGraph article timestamps
    for prop, key in (("article:published_time", "datePublished"),
                       ("article:modified_time", "dateModified")):
        if key in found:
            continue
        m = soup.find("meta", attrs={"property": prop})
        if m and (m.get("content") or "").strip():
            found[key] = (m.get("content") or "").strip()

    # Generic <meta name="date">
    if "datePublished" not in found:
        m = soup.find("meta", attrs={"name": re.compile(r"^date$", re.I)})
        if m and (m.get("content") or "").strip():
            found["datePublished"] = (m.get("content") or "").strip()

    return found


def _parse_iso_date(s: str) -> Optional[time.struct_time]:
    """Lenient ISO-ish date parser. Returns None on failure. Pyodide-safe."""
    s = s.strip()
    for fmt in ("%Y-%m-%dT%H:%M:%S%z", "%Y-%m-%dT%H:%M:%S",
                "%Y-%m-%d %H:%M:%S", "%Y-%m-%d"):
        try:
            # %z handling differs across Python versions; strip trailing Z.
            cleaned = s.rstrip("Z").split("+", 1)[0].split(".", 1)[0]
            return time.strptime(cleaned[:19], fmt[:11] if "T" not in fmt and " " not in fmt else fmt.replace("%z", "").rstrip())
        except (ValueError, IndexError):
            continue
    # Last-ditch: try first 10 chars as YYYY-MM-DD
    try:
        return time.strptime(s[:10], "%Y-%m-%d")
    except ValueError:
        return None


def audit_content(soup: BeautifulSoup, root: Tag, result: AuditResult) -> None:
    cat = "content"

    # 1. Flesch Reading Ease — derive from the article root, not the whole body
    body_text = root.get_text(" ", strip=True)
    flesch = _flesch_reading_ease(body_text)
    if flesch is not None:
        score, sentences, words, syllables = flesch
        result.stats["flesch_reading_ease"] = score
        result.stats["word_count"] = words
        result.stats["sentence_count"] = sentences
        band = _flesch_band(score)
        if score < 30:
            result.add(Finding("recommended", cat,
                f"Very difficult reading level (Flesch {score})",
                f"Article scores {score}/100 on Flesch Reading Ease — {band}. {words} words, ~{words/sentences:.0f} words per sentence, ~{syllables/words:.2f} syllables per word.",
                "Dense prose is harder for AI engines to chunk into citable passages and harder for human readers to skim — both hurt the citation/click-through pipeline. Not a direct Google ranking factor, but a useful proxy for accessibility.",
                "Shorten sentences (target 15-20 words). Replace multi-syllable jargon with simpler synonyms where the meaning permits. Break long paragraphs into 2-4 sentence units.",
            ))
        elif score > 80:
            result.add(Finding("nice-to-have", cat,
                f"Very easy reading level (Flesch {score})",
                f"Article scores {score}/100 — {band}. May be too informal for technical or professional audiences.",
                "Extremely simple prose can read as unauthoritative on technical topics, lowering E-E-A-T signals.",
                "If the audience is technical, allow more precise terminology — this is informational, not necessarily a fix.",
            ))
        else:
            result.strengths.append(f"Readable prose (Flesch {score} — {band}, {words} words).")

    # 2. Author byline / E-E-A-T signal
    has_author, evidence = _detect_author_signal(soup)
    if not has_author:
        result.add(Finding("recommended", cat,
            "No author byline / E-E-A-T signal",
            "No <meta name='author'>, rel='author' element, byline-class element, or JSON-LD Person/author was found.",
            "Google's Quality Rater Guidelines (Sept 2025) weigh Experience and Expertise heavily, both of which require an identifiable author. Anonymous content is also weakly cited by AI engines, which prefer attributed sources.",
            "Add an author block with name + credentials + bio link. Mirror in JSON-LD: \"author\": {\"@type\": \"Person\", \"name\": \"...\", \"url\": \"...\"}. For multi-author publications, include the article's individual author, not just the publication.",
        ))
    else:
        result.strengths.append(f"Author signal present: {evidence[0]}.")
        result.stats["author_evidence"] = evidence[:3]

    # 3. Freshness — publication + last-updated dates
    dates = _extract_dates(soup)
    if not dates:
        result.add(Finding("recommended", cat,
            "No publication / modification dates",
            "No datePublished, dateModified, article:published_time, or <meta name='date'> found.",
            "AI engines deprioritize undated content because they can't gauge currency. For evergreen topics this matters less; for any topic with a recency dimension (news, software, regulations, statistics) it's a major citation barrier.",
            "Add JSON-LD datePublished and dateModified, plus visible date stamps in the article. ISO 8601 format (YYYY-MM-DDTHH:MM:SSZ).",
        ))
    else:
        result.stats["dates"] = dates
        # Staleness check — flag pages older than 18 months without a recent dateModified
        pub = dates.get("datePublished")
        mod = dates.get("dateModified")
        most_recent = mod or pub
        if most_recent:
            parsed = _parse_iso_date(most_recent)
            if parsed:
                age_days = (time.time() - time.mktime(parsed)) / 86400
                result.stats["content_age_days"] = int(age_days)
                if age_days > 540:  # ~18 months
                    result.add(Finding("nice-to-have", cat,
                        f"Content is stale (~{int(age_days/30)} months old)",
                        f"Most recent timestamp is {most_recent}; no newer dateModified detected.",
                        "AI engines weight freshness for many topics. Stale content gets passed over in citation even when otherwise authoritative.",
                        "Review and update the article. If still accurate, bump dateModified to today; if not, revise the content before doing so.",
                    ))
                else:
                    result.strengths.append(f"Recent content ({int(age_days)} days old).")

    # 4. Internal / external link ratio in the article body
    internal_links = 0
    external_links = 0
    source_host = result.stats.get("source_host")
    for a in root.find_all("a", href=True):
        href = a.get("href", "").strip()
        if not href or href.startswith("#") or href.startswith(("javascript:", "mailto:", "tel:")):
            continue
        # Same-host check — relative URLs are always internal
        if href.startswith(("http://", "https://")):
            link_host = urlparse(href).netloc
            if source_host and link_host == source_host:
                internal_links += 1
            else:
                external_links += 1
        else:
            internal_links += 1
    result.stats["internal_link_count"] = internal_links
    result.stats["external_link_count"] = external_links

    word_count = result.stats.get("word_count", 0)
    if word_count >= 500:
        per_1000 = (internal_links / word_count) * 1000
        if internal_links == 0:
            result.add(Finding("recommended", cat,
                "No internal links in article body",
                f"Article body contains {word_count} words but zero internal links.",
                "Internal linking is the strongest topical-authority signal AI engines and traditional crawlers both use. Pages with no internal links read as orphan content disconnected from any topic cluster.",
                "Add 2-5 internal links per 1000 words to related articles using descriptive anchor text. Link from supporting claims to relevant pillar pages.",
            ))
        elif per_1000 < 1.5 and word_count >= 1000:
            result.add(Finding("nice-to-have", cat,
                f"Sparse internal linking ({internal_links} link(s) in {word_count} words)",
                f"~{per_1000:.1f} internal links per 1000 words; aim for 2-5.",
                "Under-linked content underweighs the topical-authority signal even when individual content is strong.",
                "Add more internal links to related articles, especially from key claims and definitions to detailed reference pages.",
            ))
        if external_links == 0 and word_count >= 800:
            result.add(Finding("recommended", cat,
                "No external citations",
                f"Article body contains {word_count} words but zero outbound links to external sources.",
                "AI engines reward content that cites primary sources — it pattern-matches their own training to favor 'shows its work' content.",
                "Cite supporting claims, statistics, and quotes with outbound links to authoritative primary sources. Target ≥1 external citation per major claim.",
            ))


# ---------------------------------------------------------------------------
# Pass 2c: GEO — AI-citation-specific signals
# ---------------------------------------------------------------------------

# Definition-opening patterns AI engines lift directly into snippets.
_DEFINITION_PATTERN = re.compile(
    r"^\s*(?:the\s+)?\S+(?:\s+\S+){0,3}\s+(?:is|are|refers? to|means|stands for|denotes)\s+",
    re.I,
)

# Question-form headings that should be answered in their lead sentence.
_QUESTION_HEADING = re.compile(
    r"^\s*(?:how|what|why|when|where|who|which|can|do|does|is|are|should|will|did)\b.*\?\s*$",
    re.I,
)


def audit_geo(soup: BeautifulSoup, root: Tag, result: AuditResult) -> None:
    cat = "geo"

    # Reuse _split_sections from audit_cognitive — they segment the same way.
    sections = _split_sections(root)
    if not sections:
        return

    # 1. Passage-length citability — AI engines pull 50-200 word self-contained
    # blocks. The sweet spot is 134-167 words (Backlinko analysis of AIO
    # citations, Dec 2024). Flag sections wildly outside that band.
    too_short = [s for s in sections if 20 <= s["word_count"] < 80]
    too_long = [s for s in sections if s["word_count"] > 300]
    in_band = [s for s in sections if 134 <= s["word_count"] <= 167]

    if too_short and len(too_short) >= len(sections) / 2:
        result.add(Finding("recommended", cat,
            f"Many sections too short for AI citation ({len(too_short)}/{len(sections)})",
            f"{len(too_short)} section(s) sit in the 20-80 word range. AI overviews favor 100-200 word self-contained blocks.",
            "Too-short sections force AI engines to combine multiple blocks to form a citable passage, which often introduces inaccuracy or causes the engine to skip the source entirely.",
            "Expand short sections to 100-200 words each. The 134-167 word band is the empirical sweet spot for AI Overview citations.",
        ))
    if too_long:
        result.add(Finding("nice-to-have", cat,
            f"Sections too long for AI citation ({len(too_long)})",
            f"{len(too_long)} section(s) exceed 300 words. AI engines summarize aggressively at that length, losing the specific claim.",
            "Long sections get paraphrased rather than quoted verbatim. Paraphrasing breaks attribution and reduces the chance of being cited.",
            "Split sections over 300 words into multiple H3 subsections of 100-200 words each.",
        ))
    if in_band and len(in_band) >= 2:
        result.strengths.append(f"{len(in_band)} section(s) in the optimal 134-167 word AI-citation band.")

    # 2. Definition-pattern detection — first sentence of each section
    definition_sections = 0
    for s in sections:
        first = s.get("first_paragraph") or ""
        first_sentence = re.split(r"[.!?]", first, maxsplit=1)[0]
        if _DEFINITION_PATTERN.search(first_sentence):
            definition_sections += 1
    result.stats["definition_pattern_sections"] = definition_sections
    if len(sections) >= 4 and definition_sections == 0:
        result.add(Finding("recommended", cat,
            "No definition-pattern opening sentences",
            f"None of the {len(sections)} sections opens with a 'X is …', 'X refers to …', or 'X means …' pattern.",
            "AI Overviews preferentially cite sentences that follow definition patterns because the structure makes the entity-claim relationship unambiguous. Sections without one rely on the engine inferring the topic.",
            "Open key sections with a definition sentence — 'A schema markup is …', 'Core Web Vitals refer to …'. The pattern works for both factual and conceptual subjects.",
        ))
    elif definition_sections >= 2:
        result.strengths.append(f"{definition_sections} section(s) open with a definition pattern.")

    # 3. Answer-first headings — question headings should be answered in the
    # first 40 words of their section.
    question_sections = []
    weak_answers = []
    for s in sections:
        heading = (s.get("heading") or "").strip()
        if _QUESTION_HEADING.match(heading):
            question_sections.append(s)
            first_para = s.get("first_paragraph") or ""
            first_40 = " ".join(first_para.split()[:40])
            # Heuristic for weak answer: question-form heading where first 40
            # words don't contain a content word from the heading other than
            # the leading wh-word.
            heading_words = {w.lower() for w in re.findall(r"\w+", heading) if len(w) > 3}
            head_word = re.match(r"\w+", heading.lstrip())
            if head_word:
                heading_words.discard(head_word.group(0).lower())
            answer_words = {w.lower() for w in re.findall(r"\w+", first_40)}
            if heading_words and not (heading_words & answer_words):
                weak_answers.append(s)
    if question_sections:
        result.stats["question_headings"] = len(question_sections)
        if weak_answers:
            sample = "; ".join(repr(s["heading"][:50]) for s in weak_answers[:3])
            result.add(Finding("recommended", cat,
                f"Question headings without answer-first openings ({len(weak_answers)}/{len(question_sections)})",
                f"{len(weak_answers)} of {len(question_sections)} question-form headings don't answer in the first 40 words. Examples: {sample}.",
                "Question headings are explicit user-intent signals. When the answer is buried, AI engines either skip the section or extract a downstream paragraph that may not be the canonical answer.",
                "Open each question section with a direct answer in the first sentence. Use the form: 'Yes/No, because…' or 'X is Y because Z.' Save context and qualifications for paragraphs 2+.",
            ))
        else:
            result.strengths.append(f"All {len(question_sections)} question heading(s) answer in their lead sentence.")


# ---------------------------------------------------------------------------
# Pass 2d: technical signals (URL structure, mobile viewport, JS rendering, IndexNow)
#
# Per-page static-HTML technical checks lifted from claude-seo's seo-technical
# skill. Core Web Vitals (lab data) and multi-page crawl-depth checks are
# intentionally NOT included — they belong in a future cwv-audit skill (PSI API)
# and in sitemap-audit (multi-page) respectively.
# ---------------------------------------------------------------------------

# Common SSR/SPA framework markers in initial HTML — presence suggests
# server-side rendering or hydration was used. Absence + low text content
# suggests an empty SPA shell.
_SSR_MARKERS = (
    "data-server-rendered",       # Vue SSR
    "data-reactroot",              # legacy React SSR
    "data-react-helmet",           # React Helmet SSR
    "ng-server-context",           # Angular Universal
    "data-nuxt-render",            # Nuxt
    "__NEXT_DATA__",               # Next.js
    "__NUXT__",                    # Nuxt 3
    "__sveltekit_",                # SvelteKit
)

# Empty-shell selectors a SPA typically mounts into. When the body contains
# only one of these (with no rendered text) it's a strong CSR-only signal.
_EMPTY_SHELL_SELECTORS = ("#root", "#app", "#__next", "#__nuxt", "[data-reactroot]")


def audit_technical(soup: BeautifulSoup, source_url: str, result: AuditResult) -> None:
    cat = "technical"

    # 1. URL structure — only meaningful when source is a real URL
    if re.match(r"^https?://", source_url, re.I):
        parsed = urlparse(source_url)
        url_len = len(source_url)
        if url_len > 100:
            result.add(Finding("nice-to-have", cat,
                f"URL is {url_len} characters",
                f"Source URL has {url_len} characters: {source_url}",
                "Long URLs are harder to share, less click-worthy in SERPs, and signal poor URL hygiene. The widely-cited 100-character soft limit reflects empirical readability and shareability data; there is no Google-imposed maximum.",
                "Trim path segments and remove tracking parameters from canonical URLs. Use short, descriptive slugs (`/blog/seo-audit-guide` not `/blog/2024/05/19/how-to-do-an-seo-audit-for-your-website-step-by-step`).",
            ))

        # Trailing-slash consistency: compare the source URL's trailing-slash
        # state to the canonical's trailing-slash state. Different patterns
        # are a redirect risk + canonicalization split.
        canon_link = soup.find("link", rel="canonical")
        if canon_link and (canon_link.get("href") or "").strip():
            canon_href = (canon_link.get("href") or "").strip()
            source_trailing = source_url.rstrip("?").endswith("/")
            canon_trailing = canon_href.rstrip("?").endswith("/")
            # Only flag if both have non-root paths and differ
            source_path = parsed.path
            canon_path = urlparse(canon_href).path
            if (len(source_path) > 1 and len(canon_path) > 1
                    and source_trailing != canon_trailing):
                result.add(Finding("nice-to-have", cat,
                    "Trailing-slash inconsistency between URL and canonical",
                    f"Source URL trailing slash: {source_trailing}; canonical trailing slash: {canon_trailing}.",
                    "Inconsistent trailing-slash policy across the same canonical URL forces an extra redirect for every visit that uses the 'wrong' form. Each redirect costs crawl budget and a small UX delay.",
                    "Pick one policy site-wide (with or without trailing slash) and enforce it via redirects. Update the canonical to match.",
                ))

    # 2. Mobile viewport meta tag
    viewport = soup.find("meta", attrs={"name": re.compile(r"^viewport$", re.I)})
    if not viewport:
        result.add(Finding("critical", cat,
            "No mobile viewport meta tag",
            "Page has no <meta name='viewport'> tag.",
            "Without a viewport meta, mobile browsers render the page at desktop width and scale down — text becomes unreadable, taps miss targets. Google's mobile-first indexing (100% rollout July 5 2024) means Googlebot crawls the mobile version exclusively, and missing viewport is treated as a mobile-unfriendly signal.",
            "Add: <meta name='viewport' content='width=device-width, initial-scale=1'>. This is the modern, accessibility-friendly default.",
        ))
    else:
        content = (viewport.get("content") or "").lower()
        if "user-scalable=no" in content or "maximum-scale=1" in content:
            result.add(Finding("recommended", cat,
                "Viewport prevents user scaling",
                f"Viewport meta declares zoom restriction: {viewport.get('content')!r}",
                "Disabling pinch-to-zoom violates WCAG 1.4.4 (Resize Text) and harms accessibility — users with low vision cannot enlarge the page. Modern best practice is to never restrict zoom.",
                "Remove 'user-scalable=no' and 'maximum-scale=1'. Trust users to zoom when needed; design layouts that survive zoom gracefully.",
            ))
        elif "width=device-width" not in content:
            result.add(Finding("recommended", cat,
                "Viewport doesn't use width=device-width",
                f"Viewport content: {viewport.get('content')!r}",
                "Fixed-width viewports render at desktop dimensions on mobile, causing horizontal scrolling and tiny text. width=device-width tells the browser to use the actual device viewport width.",
                "Set content='width=device-width, initial-scale=1' to use the device's natural viewport.",
            ))

    # 3. JavaScript-rendering detection
    body = soup.find("body")
    if body is not None:
        body_text = body.get_text(" ", strip=True)
        body_word_count = len(re.findall(r"\b\w+\b", body_text))
        body_html = str(body)

        has_ssr_marker = any(marker in body_html for marker in _SSR_MARKERS)
        has_noscript = bool(soup.find("noscript"))

        # Empty-shell pattern: body has very few words AND contains an empty
        # SPA mount point. Strong CSR-only signal.
        empty_shell_found = None
        for sel in _EMPTY_SHELL_SELECTORS:
            target = soup.select_one(sel)
            if target is not None and len(target.get_text(strip=True)) < 50:
                empty_shell_found = sel
                break

        if body_word_count < 200 and empty_shell_found and not has_ssr_marker:
            result.add(Finding("critical", cat,
                "Page appears to be a client-rendered SPA without SSR",
                f"Body has only {body_word_count} words, contains an empty {empty_shell_found!r} mount point, and shows no SSR markers ({', '.join(_SSR_MARKERS[:3])}…).",
                "AI crawlers (GPTBot, ClaudeBot, PerplexityBot) and most search engines do not execute JavaScript reliably. A page that renders content client-side is effectively invisible — no content extraction, no schema, no citation. Google does render JS but with significant delay and at lower priority.",
                "Add server-side rendering (SSR) or static-site generation (SSG). Frameworks: Next.js, Nuxt, SvelteKit, Astro, Remix. As a stopgap, use prerendering tools (Prerender.io, Rendertron) that serve pre-rendered HTML to crawlers.",
            ))
        elif body_word_count < 200 and not has_noscript:
            result.add(Finding("nice-to-have", cat,
                f"Low body text ({body_word_count} words) with no <noscript> fallback",
                f"Body has {body_word_count} words. No <noscript> element provides content for crawlers that don't run JS.",
                "Pages with minimal initial HTML and no <noscript> fallback give crawlers nothing to extract. If JS-rendered content is intentional, a <noscript> block providing core text helps crawler understanding.",
                "Either add SSR for the main content, OR include a <noscript> block summarizing the page's key information for non-JS clients.",
            ))

    # 4. IndexNow protocol declaration
    indexnow_meta = soup.find("meta", attrs={"name": re.compile(r"^indexnow$", re.I)})
    if indexnow_meta and (indexnow_meta.get("content") or "").strip():
        result.strengths.append("IndexNow API key declared via <meta name='indexnow'>")
    else:
        # IndexNow is also commonly declared via a key file at the site root.
        # That requires an extra fetch (audit_external runs separately); here
        # we just note when it's NOT in the meta tag, which is purely an FYI.
        # Don't emit a finding — IndexNow is optional and only flag when the
        # site clearly has indexing-velocity needs (news, e-commerce).
        pass


# ---------------------------------------------------------------------------
# Pass 3: asset hygiene
# ---------------------------------------------------------------------------

def audit_assets(soup: BeautifulSoup, root: Tag, result: AuditResult) -> None:
    cat = "assets"

    # Title tag
    title = soup.find("title")
    title_text = (title.get_text(strip=True) if title else "")
    if not title_text:
        result.add(Finding("critical", cat,
            "No <title> tag",
            "The <head> contains no <title> or it is empty.",
            "<title> is the strongest signal for what the page is about. Missing title = the page is invisible in tab bars, bookmarks, and search results.",
            "Add a descriptive <title>30–60 characters summarizing the page topic.",
        ))
    elif not (30 <= len(title_text) <= 60):
        sev = "nice-to-have" if 20 <= len(title_text) <= 80 else "recommended"
        result.add(Finding(sev, cat,
            f"<title> length suboptimal ({len(title_text)} chars)",
            f"Title: {title_text!r}",
            "Titles under 30 chars waste the snippet slot; over 60 chars get truncated mid-word in SERPs.",
            "Aim for 30–60 characters. Front-load the keyword.",
        ))

    # Image alt text coverage
    imgs = root.find_all("img")
    if imgs:
        with_alt = [i for i in imgs if (i.get("alt") or "").strip()]
        ratio = len(with_alt) / len(imgs)
        result.stats["alt_coverage"] = round(ratio, 3)
        if ratio < 0.50:
            result.add(Finding("critical", cat,
                f"Poor image alt coverage ({ratio:.0%})",
                f"Only {len(with_alt)} of {len(imgs)} images have alt text.",
                "AI engines parse alt text to understand image content for both image search and contextual relevance. Missing alt = images contribute nothing to ranking.",
                "Add descriptive alt text to every content image. Decorative images can use alt='' explicitly.",
            ))
        elif ratio < 0.80:
            result.add(Finding("recommended", cat,
                f"Incomplete image alt coverage ({ratio:.0%})",
                f"{len(imgs) - len(with_alt)} of {len(imgs)} images have no alt.",
                "Each missing alt is a missed indexing opportunity.",
                "Audit the images without alt text and either describe them or mark decorative ones with alt=''.",
            ))
        else:
            result.strengths.append(f"Strong alt coverage ({ratio:.0%} of {len(imgs)} images).")

    # Empty / fragment-only links
    bad_links = []
    for a in root.find_all("a"):
        href = (a.get("href") or "").strip()
        if not href or href == "#":
            text = a.get_text(strip=True)[:40]
            if text:  # ignore unfilled anchors with no text
                bad_links.append(text)
    if bad_links:
        sev = "recommended" if len(bad_links) > 5 else "nice-to-have"
        result.add(Finding(sev, cat,
            f"Empty or '#' links ({len(bad_links)})",
            f"Examples: {', '.join(repr(l) for l in bad_links[:5])}.",
            "Broken-looking links signal low quality and can be penalized in crawl scoring.",
            "Either give each link a real href or convert to a <span> if it's just a label.",
        ))


# ---------------------------------------------------------------------------
# Pass 4: external signals (robots.txt AI crawlers, /llms.txt, security headers)
#
# This pass requires HTTP fetches beyond the main HTML — robots.txt, llms.txt,
# and a HEAD request to the source URL for response headers. Pyodide's
# cross-origin XHR is blocked by CORS, so we route through the host backend's
# /gpt/backend/api/v1/fetch-url proxy (same pattern as serp-shape uses for its
# top-N page fetches). CPython uses urllib directly.
#
# All three checks are skipped gracefully if the source isn't an http(s) URL
# (e.g. the script was invoked on a local .html file).
# ---------------------------------------------------------------------------

PROXY_ENDPOINT = "/gpt/backend/api/v1/fetch-url"


async def _fetch_extra_pyodide(url: str, want_headers: bool = False) -> Optional[dict]:
    """Pyodide path: POST {url, method?: 'HEAD'} to the host fetch-url proxy.

    Returns {"body": str, "headers": dict, "status": int} or None on any failure.
    The proxy is same-origin so CORS does not apply. JWT is picked up from
    window.authManager — same mechanism the bridge uses internally.
    """
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
    body_payload = {"url": url}
    if want_headers:
        body_payload["method"] = "HEAD"
    try:
        resp = await pyodide.http.pyfetch(
            PROXY_ENDPOINT,
            method="POST",
            headers=headers,
            body=json.dumps(body_payload),
        )
        text = await resp.string()
        payload = json.loads(text)
    except Exception:
        return None
    if not payload.get("success"):
        return None
    data = payload.get("data", {}) or {}
    return {
        "body": data.get("html", "") or data.get("body", "") or "",
        "headers": data.get("headers", {}) or {},
        "status": data.get("status", 0) or 0,
    }


def _fetch_extra_urllib(url: str, want_headers: bool = False) -> Optional[dict]:
    """CPython path: urllib request returning body + response headers."""
    import ssl
    import urllib.request
    import gzip
    import zlib
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    method = "HEAD" if want_headers else "GET"
    req = urllib.request.Request(url, method=method, headers={
        "User-Agent": BROWSER_UA,
        "Accept": "*/*",
        "Accept-Encoding": "gzip, deflate",
    })
    try:
        with urllib.request.urlopen(req, timeout=15, context=ctx) as r:
            response_headers = {k: v for k, v in r.headers.items()}
            status = r.status
            body_bytes = b"" if want_headers else r.read()
            encoding = (r.headers.get("Content-Encoding") or "").lower()
    except Exception:
        return None
    if body_bytes:
        if encoding == "gzip":
            try:
                body_bytes = gzip.decompress(body_bytes)
            except OSError:
                pass
        elif encoding == "deflate":
            try:
                body_bytes = zlib.decompress(body_bytes)
            except zlib.error:
                try:
                    body_bytes = zlib.decompress(body_bytes, -zlib.MAX_WBITS)
                except zlib.error:
                    pass
    try:
        body_text = body_bytes.decode("utf-8", errors="replace")
    except Exception:
        body_text = ""
    return {"body": body_text, "headers": response_headers, "status": status}


async def _fetch_extra(url: str, want_headers: bool = False) -> Optional[dict]:
    """Unified entry point — picks the right transport per runtime."""
    if _is_pyodide():
        return await _fetch_extra_pyodide(url, want_headers=want_headers)
    # CPython is sync — wrap in a thread so we don't block the event loop
    # when called from inside asyncio.run().
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(None, _fetch_extra_urllib, url, want_headers)


def _audit_robots_ai_crawlers(robots_txt: str, result: AuditResult) -> None:
    """Parse robots.txt and check which AI crawlers are allowed vs blocked."""
    cat = "external"
    if not robots_txt:
        result.add(Finding("nice-to-have", cat,
            "No /robots.txt found",
            "robots.txt was not reachable at the site root (404 or fetch error).",
            "Without a robots.txt, every crawler — search, AI, scraper — defaults to crawling everything. That's usually fine, but you also lose the ability to selectively allow or block AI crawlers for training-corpus vs live-search use cases.",
            "Add a /robots.txt. At minimum: 'User-agent: *' + 'Allow: /'. Use specific rules to allow or block AI crawlers (GPTBot, ClaudeBot, PerplexityBot, etc.) per your AI-visibility strategy.",
        ))
        return

    # Parse user-agent blocks. A 'block' starts at each 'User-agent:' line and
    # continues until the next blank line or next User-agent declaration.
    blocks: dict[str, list[str]] = {}  # ua → list of disallow rules
    current_uas: list[str] = []
    for raw in robots_txt.splitlines():
        line = raw.split("#", 1)[0].strip()
        if not line:
            current_uas = []
            continue
        if ":" not in line:
            continue
        key, _, val = line.partition(":")
        key = key.strip().lower()
        val = val.strip()
        if key == "user-agent":
            current_uas.append(val)
            blocks.setdefault(val, [])
        elif key == "disallow" and current_uas:
            for ua in current_uas:
                blocks.setdefault(ua, []).append(val)

    def is_blocked(ua: str) -> bool:
        """True if any of ua's Disallow rules block the site root."""
        rules = blocks.get(ua, [])
        for r in rules:
            if r in ("/", ""):
                # '/' = block all; '' = explicit allow-all per spec
                if r == "/":
                    return True
        return False

    blocked: list[tuple[str, str, str]] = []
    allowed: list[tuple[str, str, str]] = []
    for token, (owner, purpose) in AI_CRAWLERS.items():
        # robots.txt user-agent matching is case-insensitive in practice; the
        # spec is technically case-sensitive but every crawler matches loosely.
        token_lower = token.lower()
        matched_ua = None
        for declared_ua in blocks.keys():
            if declared_ua.lower() == token_lower:
                matched_ua = declared_ua
                break
        if matched_ua is None:
            allowed.append((token, owner, purpose))  # not mentioned = implicit allow
        else:
            if is_blocked(matched_ua):
                blocked.append((token, owner, purpose))
            else:
                allowed.append((token, owner, purpose))

    # Wildcard block
    wildcard_blocks = blocks.get("*", [])
    wildcard_blocks_all = any(r == "/" for r in wildcard_blocks)
    if wildcard_blocks_all:
        result.add(Finding("critical", cat,
            "robots.txt blocks ALL crawlers (User-agent: * Disallow: /)",
            "robots.txt declares 'User-agent: *' with 'Disallow: /' — every crawler that respects robots.txt is being told to skip the entire site.",
            "This blocks Googlebot, Bingbot, AI crawlers, and everything else from indexing the page. It is invisible to AI search until the rule is removed.",
            "If the site is meant to be indexed, remove the 'Disallow: /' rule or scope it to specific paths. If the site is intentionally private, this finding is expected.",
        ))

    result.stats["ai_crawlers_blocked"] = [t for t, _, _ in blocked]
    result.stats["ai_crawlers_allowed"] = [t for t, _, _ in allowed]

    # High-impact blocks: search-index crawlers (not just training)
    search_index_crawlers = {"OAI-SearchBot", "ChatGPT-User", "PerplexityBot", "Perplexity-User", "Claude-Web"}
    blocked_search = [b for b in blocked if b[0] in search_index_crawlers]
    if blocked_search:
        names = ", ".join(b[0] for b in blocked_search)
        result.add(Finding("critical", cat,
            f"AI search crawlers blocked: {names}",
            f"robots.txt blocks {len(blocked_search)} live AI-search crawler(s): {names}.",
            "These crawlers power real-time AI search results (ChatGPT Search, Perplexity, Claude web). Blocking them removes the page from those answer surfaces entirely. This is different from blocking GPTBot/Google-Extended (training-only crawlers).",
            "Remove the Disallow rules for these user-agents if AI search visibility matters. Keep training-corpus crawlers (GPTBot, Google-Extended, CCBot) blocked only if you specifically want to opt out of model training.",
        ))

    # Training-corpus crawlers blocked — info, not critical
    training_crawlers = {"GPTBot", "ClaudeBot", "anthropic-ai", "Google-Extended", "Bytespider", "CCBot", "cohere-ai"}
    blocked_training = [b for b in blocked if b[0] in training_crawlers]
    if blocked_training and not blocked_search:
        names = ", ".join(b[0] for b in blocked_training)
        result.add(Finding("nice-to-have", cat,
            f"AI training crawlers blocked: {names}",
            f"robots.txt blocks {len(blocked_training)} training-corpus crawler(s): {names}.",
            "This is a deliberate choice many sites make to opt out of AI model training. It does NOT block AI search visibility — live AI-search crawlers are separate user-agents.",
            "Informational. Keep these blocked if opting out of training is the goal. Verify live-search crawlers (OAI-SearchBot, ChatGPT-User, PerplexityBot, Claude-Web) remain allowed.",
        ))

    if not blocked:
        result.strengths.append(f"All {len(AI_CRAWLERS)} known AI crawler(s) allowed by robots.txt.")


def _audit_llms_txt(llms_text: str, base_url: str, result: AuditResult) -> None:
    cat = "external"
    if not llms_text:
        result.add(Finding("recommended", cat,
            "No /llms.txt file",
            f"No llms.txt found at {base_url}/llms.txt.",
            "llms.txt is an emerging standard (2024-2025) that gives LLM crawlers a structured map of the site's most important content. Without it, crawlers fall back to general HTML extraction, which is noisier and less targeted than the curated llms.txt entries.",
            "Add a /llms.txt at the site root. Minimal template:\n```\n# Site Name\n> One-line description.\n\n## Main pages\n- [Page title](https://...): short description\n- [Another page](https://...): short description\n```\nKeep entries focused on canonical, high-authority pages — not navigation chrome.",
        ))
        return

    # Structural validation — llms.txt should have an H1 title, a blockquote
    # description, and at least one section with bullet list entries.
    has_title = bool(re.search(r"^#\s+\S", llms_text, re.M))
    has_description = bool(re.search(r"^>\s+\S", llms_text, re.M))
    has_sections = bool(re.search(r"^##\s+\S", llms_text, re.M))
    has_links = bool(re.search(r"^\s*-\s*\[.+\]\(.+\)", llms_text, re.M))
    word_count = len(re.findall(r"\b\w+\b", llms_text))

    issues = []
    if not has_title:
        issues.append("no H1 title")
    if not has_description:
        issues.append("no `>` description blockquote")
    if not has_sections:
        issues.append("no `## section` headers")
    if not has_links:
        issues.append("no `- [link](url)` entries")

    if issues:
        result.add(Finding("nice-to-have", cat,
            "llms.txt present but structurally incomplete",
            f"llms.txt exists ({word_count} words) but is missing: {', '.join(issues)}.",
            "The llms.txt format is loose, but malformed entries reduce its utility — crawlers either fall back to general extraction or skip the file. Following the standard template maximizes the parsing yield.",
            "Add the missing elements. The recommended structure: H1 title, `>` blockquote one-line description, then `## Section` headers with `- [Page title](url): description` bullet entries.",
        ))
    else:
        result.strengths.append(f"llms.txt present and well-structured ({word_count} words).")


def _audit_security_headers(headers: dict, result: AuditResult) -> None:
    cat = "external"
    if not headers:
        return  # nothing to audit; the fetch probably failed
    # Normalize keys to lowercase for case-insensitive lookup
    headers_lower = {k.lower(): v for k, v in headers.items()}
    missing: list[tuple[str, str, str]] = []
    for header, (severity, reason) in SECURITY_HEADERS.items():
        if header.lower() not in headers_lower:
            missing.append((header, severity, reason))
    present_count = len(SECURITY_HEADERS) - len(missing)
    result.stats["security_headers_present"] = present_count
    result.stats["security_headers_total"] = len(SECURITY_HEADERS)

    if missing:
        # Group by severity so we don't emit five separate findings.
        by_sev: dict[str, list[str]] = {}
        for name, sev, _reason in missing:
            by_sev.setdefault(sev, []).append(name)
        for sev, names in by_sev.items():
            result.add(Finding(sev, cat,
                f"Missing security header(s): {', '.join(names)}",
                f"Response does not include: {', '.join(names)}.",
                "Security headers are a trust signal weighed by both classic SERP ranking and AI crawl-quality scoring. Their absence is also a real security gap — CSP and HSTS materially reduce XSS and downgrade-attack risk.",
                "Add the headers via server configuration or CDN edge rules. For CSP, start with `Content-Security-Policy-Report-Only` to identify violations before enforcing. HSTS: `max-age=31536000; includeSubDomains`.",
            ))
    if present_count == len(SECURITY_HEADERS):
        result.strengths.append("All recommended security headers present.")


async def audit_external(source_url: str, result: AuditResult) -> None:
    """Pass 4: AI-crawler robots.txt audit, /llms.txt check, security headers.

    Skipped silently when source_url isn't an http(s) URL (e.g. local file).
    All three sub-checks are independent — failure to fetch one doesn't block
    the others. Idempotent: re-invocation with the same URL is a no-op so
    callers can safely run this after a sync audit_html() if they later
    learn the real source URL (e.g. bridge-prefetched file + --source flag).
    """
    if not re.match(r"^https?://", source_url, re.I):
        return
    if result.stats.get("external_audited"):
        return
    result.stats["external_audited"] = True
    parsed = urlparse(source_url)
    base = f"{parsed.scheme}://{parsed.netloc}"

    # Fan out the three fetches concurrently.
    robots_task = asyncio.create_task(_fetch_extra(f"{base}/robots.txt"))
    llms_task   = asyncio.create_task(_fetch_extra(f"{base}/llms.txt"))
    head_task   = asyncio.create_task(_fetch_extra(source_url, want_headers=True))

    robots_result, llms_result, head_result = await asyncio.gather(
        robots_task, llms_task, head_task, return_exceptions=False,
    )

    robots_txt = ""
    if robots_result and robots_result.get("status", 0) in (200, 0):
        robots_txt = robots_result.get("body") or ""
    _audit_robots_ai_crawlers(robots_txt, result)

    llms_text = ""
    if llms_result and llms_result.get("status", 0) in (200, 0):
        # Some servers return 200 with HTML content when the path doesn't exist.
        # Sanity-check: llms.txt should be markdown, not HTML.
        body = llms_result.get("body") or ""
        if body and not body.lstrip().lower().startswith("<!doctype") and "<html" not in body[:200].lower():
            llms_text = body
    _audit_llms_txt(llms_text, base, result)

    head_headers = {}
    if head_result and head_result.get("headers"):
        head_headers = head_result.get("headers") or {}
    _audit_security_headers(head_headers, result)


# ---------------------------------------------------------------------------
# Report rendering
# ---------------------------------------------------------------------------

def render_report(result: AuditResult, source: str) -> str:
    score = result.score()
    grade = result.grade()
    counts = Counter(f.severity for f in result.findings)

    lines: list[str] = []
    lines.append(f"# AI Search Readiness Audit")
    lines.append("")
    lines.append(f"**Source:** {source}")
    lines.append("")
    lines.append(f"## Score: {score}/100 ({grade})")
    lines.append("")
    lines.append(f"- Critical issues: {counts.get('critical', 0)} (−{counts.get('critical', 0) * SEVERITY_WEIGHT['critical']} pts)")
    lines.append(f"- Recommended fixes: {counts.get('recommended', 0)} (−{counts.get('recommended', 0) * SEVERITY_WEIGHT['recommended']} pts)")
    lines.append(f"- Nice-to-have: {counts.get('nice-to-have', 0)} (−{counts.get('nice-to-have', 0) * SEVERITY_WEIGHT['nice-to-have']} pts)")
    lines.append("")

    if result.strengths:
        lines.append("## Strengths")
        lines.append("")
        for s in result.strengths:
            lines.append(f"- {s}")
        lines.append("")

    for category, label in (
        ("cognitive", "Cognitive structure"),
        ("metadata", "Machine-readable metadata"),
        ("content", "Content quality (E-E-A-T, readability, freshness)"),
        ("geo", "AI-citation signals (GEO)"),
        ("technical", "Technical signals (URL structure, mobile viewport, JS rendering)"),
        ("assets", "Asset hygiene"),
        ("external", "External signals (robots.txt, llms.txt, security headers)"),
    ):
        items = result.by(category)
        if not items:
            lines.append(f"## {label}")
            lines.append("")
            lines.append("_No issues found in this category._")
            lines.append("")
            continue
        lines.append(f"## {label}")
        lines.append("")
        # Sort by severity (critical first)
        sev_order = {s: i for i, s in enumerate(SEVERITIES)}
        for f in sorted(items, key=lambda x: sev_order[x.severity]):
            lines.append(f"### [{f.severity.upper()}] {f.title}")
            lines.append("")
            lines.append(f"**What:** {f.detail}")
            lines.append("")
            lines.append(f"**Why it matters:** {f.why}")
            lines.append("")
            lines.append(f"**Fix:** {f.fix}")
            lines.append("")

    if result.stats:
        lines.append("## Stats")
        lines.append("")
        for k, v in result.stats.items():
            lines.append(f"- `{k}`: {v}")
        lines.append("")

    return "\n".join(lines).rstrip() + "\n"


# ---------------------------------------------------------------------------
# Public Python API — call these from another Python program (or from Pyodide
# in a browser frontend, after fetching the HTML server-side).
# ---------------------------------------------------------------------------

def audit_html(html: str, source: str = "<inline>",
               main_selector: str | None = None) -> AuditResult:
    """Audit pre-fetched HTML content (in-process passes only).

    Runs all sync passes: cognitive, metadata, content, GEO, assets. Does NOT
    fetch external resources (robots.txt, llms.txt, response headers). Use
    `audit_html_full()` for the async version that includes external signals.

    The primary entry point for environments where URL fetching has to happen
    outside the script (Pyodide in the browser, where CORS blocks most cross-
    origin fetches — the frontend should fetch via PHP/Node and pass the HTML
    here directly).

    Args:
        html: The full HTML document text.
        source: Label for the report (typically the original URL).
        main_selector: Optional CSS selector to scope the cognitive-structure
            pass to a specific article container.

    Returns:
        AuditResult with all in-process audit passes applied.
    """
    soup = BeautifulSoup(html, "html.parser")
    root = pick_root(soup, main_selector)
    result = AuditResult()
    if re.match(r"^https?://", source, re.I):
        result.stats["source_host"] = urlparse(source).netloc
    audit_cognitive(soup, root, result)
    audit_metadata(soup, result)
    audit_content(soup, root, result)
    audit_geo(soup, root, result)
    audit_technical(soup, source, result)
    audit_assets(soup, root, result)
    return result


async def audit_html_full(html: str, source: str = "<inline>",
                           main_selector: str | None = None) -> AuditResult:
    """Audit pre-fetched HTML + external signals (full audit).

    Same as audit_html() but additionally runs the external pass: fetches
    robots.txt, /llms.txt, and HEAD response headers from the source URL's
    origin. When source is not an http(s) URL, the external pass is skipped
    silently (the result is identical to audit_html()).

    Async because the external fetches use pyodide.http.pyfetch in Pyodide
    (via the host /api/v1/fetch-url proxy) and urllib in CPython (wrapped in
    run_in_executor to keep the event loop responsive).
    """
    result = audit_html(html, source, main_selector)
    await audit_external(source, result)
    return result


def audit_html_to_markdown(html: str, source: str = "<inline>",
                            main_selector: str | None = None) -> str:
    """One-shot: pre-fetched HTML in, Markdown report out. Useful for frontends
    that just want the rendered string. In-process passes only (no external)."""
    result = audit_html(html, source, main_selector)
    return render_report(result, source)


async def audit_html_to_markdown_full(html: str, source: str = "<inline>",
                                       main_selector: str | None = None) -> str:
    """Async equivalent of audit_html_to_markdown that includes external signals."""
    result = await audit_html_full(html, source, main_selector)
    return render_report(result, source)


# ---------------------------------------------------------------------------
# Per-page audit runner (used by single, batch, and crawl modes)
# ---------------------------------------------------------------------------

def audit_one(target: str, main_selector: str | None = None) -> tuple[AuditResult | None, str, str | None]:
    """Run a full audit on a single target. Returns
    (result, source_label, error_message_or_None).

    Uses the async audit_html_full under the hood so that external signals
    (robots.txt AI-crawlers, /llms.txt, security headers) are included when
    the source is an http(s) URL. Falls back to sync audit_html for local
    files / inline HTML where external fetches would be meaningless.
    """
    try:
        html, source = load_input(target)
    except Exception as e:
        return None, target, str(e)
    if re.match(r"^https?://", source, re.I):
        return asyncio.run(audit_html_full(html, source, main_selector)), source, None
    return audit_html(html, source, main_selector), source, None


def write_report(result: AuditResult, source: str, output: Path | None) -> Path:
    report = render_report(result, source)
    if output is None:
        DEFAULT_OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
        output = DEFAULT_OUTPUT_DIR / f"{_stem_from_source(source)}-audit.md"
    else:
        output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(report, encoding="utf-8")
    return output


# ---------------------------------------------------------------------------
# Batch helpers
# ---------------------------------------------------------------------------

def read_batch_file(path: Path) -> list[str]:
    """Read a list of URLs/file paths, one per line. Lines starting with '#'
    and blank lines are ignored."""
    targets = []
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        targets.append(line)
    return targets


# Skip non-HTML URLs by extension when crawling.
_SKIP_EXT = {
    ".jpg", ".jpeg", ".png", ".gif", ".webp", ".svg", ".ico",
    ".pdf", ".zip", ".tar", ".gz", ".rar", ".7z",
    ".mp4", ".mov", ".avi", ".webm", ".mp3", ".wav",
    ".css", ".js", ".json", ".xml", ".rss", ".atom",
    ".doc", ".docx", ".xls", ".xlsx", ".ppt", ".pptx",
}


def crawl_urls(start_url: str, max_pages: int = 25, delay: float = 0.5) -> list[str]:
    """BFS from start_url, following same-host internal links. Returns the
    discovered URL list (capped at max_pages, including start_url)."""
    start_host = urlparse(start_url).netloc.lower()
    discovered: list[str] = []
    seen: set[str] = set()
    queue: deque[str] = deque([_normalize(start_url)])

    while queue and len(discovered) < max_pages:
        url = queue.popleft()
        if url in seen:
            continue
        seen.add(url)

        try:
            html = _fetch_url(url)
        except Exception as e:
            print(f"  crawl: skip {url} ({e})", file=sys.stderr)
            continue

        discovered.append(url)
        print(f"  crawl: discovered {len(discovered)}/{max_pages}: {url}", file=sys.stderr)

        # Polite pacing — easier on the host and avoids rate limiters.
        if delay > 0:
            time.sleep(delay)

        # Extract same-host links from the body.
        soup = BeautifulSoup(html, "html.parser")
        for a in soup.find_all("a", href=True):
            href = a["href"].strip()
            if not href or href.startswith(("javascript:", "mailto:", "tel:")):
                continue
            absolute = urljoin(url, href)
            absolute, _ = urldefrag(absolute)  # drop fragments
            p = urlparse(absolute)
            if p.scheme not in ("http", "https"):
                continue
            if p.netloc.lower() != start_host:
                continue
            # Skip non-HTML resources
            if any(p.path.lower().endswith(ext) for ext in _SKIP_EXT):
                continue
            normalized = _normalize(absolute)
            if normalized not in seen:
                queue.append(normalized)

    return discovered


def _normalize(url: str) -> str:
    """Drop trailing slashes (except for the root path) and fragments for dedup."""
    url, _ = urldefrag(url)
    p = urlparse(url)
    path = p.path
    if len(path) > 1 and path.endswith("/"):
        path = path[:-1]
    return f"{p.scheme}://{p.netloc}{path}{('?' + p.query) if p.query else ''}"


# ---------------------------------------------------------------------------
# Batch summary report
# ---------------------------------------------------------------------------

def render_batch_summary(rows: list[dict], origin: str) -> str:
    """rows: each is {source, score, grade, critical, recommended, nice, top_issue, error, report_path}"""
    successful = [r for r in rows if r.get("error") is None]
    failed = [r for r in rows if r.get("error") is not None]

    lines: list[str] = []
    lines.append("# AI Search Audit — Batch Summary")
    lines.append("")
    lines.append(f"**Origin:** {origin}")
    lines.append(f"**Pages audited:** {len(successful)} OK / {len(failed)} failed")
    lines.append("")

    if successful:
        avg_score = sum(r["score"] for r in successful) / len(successful)
        avg_critical = sum(r["critical"] for r in successful) / len(successful)
        lines.append(f"**Average score:** {avg_score:.0f}/100")
        lines.append(f"**Average critical findings:** {avg_critical:.1f} per page")
        lines.append("")

        # Sorted worst first — these need the most attention
        successful_sorted = sorted(successful, key=lambda r: r["score"])
        lines.append("## Pages ranked by score (worst first)")
        lines.append("")
        lines.append("| Score | Grade | Critical | Recommended | Top issue | URL |")
        lines.append("|---|---|---|---|---|---|")
        for r in successful_sorted:
            top = (r.get("top_issue") or "—")[:60]
            url_short = r["source"][:80]
            lines.append(
                f"| {r['score']} | {r['grade']} | {r['critical']} "
                f"| {r['recommended']} | {top} | {url_short} |"
            )
        lines.append("")

        # Common issues across the batch
        all_titles: list[str] = []
        for r in successful:
            all_titles.extend(r.get("titles", []))
        if all_titles:
            common = Counter(all_titles).most_common(10)
            lines.append("## Most common issues across the batch")
            lines.append("")
            for title, count in common:
                lines.append(f"- **{count}× pages**: {title}")
            lines.append("")

        # Per-page report links
        lines.append("## Individual reports")
        lines.append("")
        for r in successful_sorted:
            lines.append(f"- [{r['score']}/100 ({r['grade']})] {r['source']}  →  `{r['report_path']}`")
        lines.append("")

    if failed:
        lines.append("## Failed to audit")
        lines.append("")
        for r in failed:
            lines.append(f"- {r['source']}  ({r['error']})")
        lines.append("")

    return "\n".join(lines).rstrip() + "\n"


def _audit_for_batch_row(target: str, main_selector: str | None) -> dict:
    """Run audit_one + write per-page report; return a row dict for the summary."""
    result, source, err = audit_one(target, main_selector=main_selector)
    if err is not None or result is None:
        return {"source": source, "error": err}
    out_path = write_report(result, source, output=None)
    counts = Counter(f.severity for f in result.findings)
    # Collect titles of critical+recommended findings for cross-batch trend analysis
    titles = [f.title for f in result.findings if f.severity in ("critical", "recommended")]
    # Top issue: the first critical, else the first recommended
    sev_order = {s: i for i, s in enumerate(SEVERITIES)}
    sorted_findings = sorted(result.findings, key=lambda x: sev_order[x.severity])
    top_issue = sorted_findings[0].title if sorted_findings else None
    return {
        "source": source,
        "score": result.score(),
        "grade": result.grade(),
        "critical": counts.get("critical", 0),
        "recommended": counts.get("recommended", 0),
        "nice": counts.get("nice-to-have", 0),
        "top_issue": top_issue,
        "titles": titles,
        "report_path": str(out_path),
        "error": None,
    }


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Audit an HTML page (or a whole site) for AI-search readiness."
    )
    parser.add_argument("input", nargs="?",
        help="Path to .html file OR a URL OR a bare domain. "
             "Required unless --batch-file or --stdin is given.")
    parser.add_argument("-o", "--output", type=Path,
        help=f"Output .md file (single mode only). Default: {DEFAULT_OUTPUT_DIR}/<stem>-audit.md")
    parser.add_argument("--main-selector", default=None,
        help="CSS selector for the article container (e.g. 'article'). "
             "If omitted, auto-detects <main>/<article>/etc., falling back to <body>.")
    parser.add_argument("--stdout", action="store_true",
        help="Single mode: print report to stdout instead of writing a file.")
    parser.add_argument("--stdin", action="store_true",
        help="Read HTML content from stdin instead of fetching/loading. "
             "Pair with --source to label what the HTML represents in the report. "
             "Designed for environments where URL fetching happens outside the script "
             "(e.g. Pyodide in the browser, where the frontend pre-fetches via PHP/JS).")
    parser.add_argument("--source", default=None,
        help="Source label for the report when reading from --stdin. "
             "Typically the original URL the HTML was fetched from.")

    # Batch modes
    parser.add_argument("--crawl", action="store_true",
        help="Treat the URL input as a starting point, crawl same-host internal links, "
             "and audit each discovered page. Requires the input to be an http(s) URL.")
    parser.add_argument("--max-pages", type=int, default=25,
        help="Crawl mode: maximum pages to audit (default: 25).")
    parser.add_argument("--crawl-delay", type=float, default=0.5,
        help="Crawl mode: seconds between page fetches during discovery (default: 0.5).")
    parser.add_argument("--batch-file", type=Path,
        help="Read a list of URLs/file paths (one per line, '#' comments allowed) and "
             "audit each. Mutually exclusive with --crawl.")
    parser.add_argument("--summary-output", type=Path,
        help="Batch mode: where to write the summary report. "
             f"Default: {DEFAULT_OUTPUT_DIR}/batch-summary.md")
    parser.add_argument("--workers", type=int, default=8,
        help="Batch mode: parallel audit workers (default: 8).")
    args = parser.parse_args(argv)

    # ---- Mode dispatch ----
    if args.crawl and args.batch_file:
        print("ERROR: --crawl and --batch-file are mutually exclusive.", file=sys.stderr)
        return 2
    if (args.crawl or args.batch_file) and args.stdout:
        print("ERROR: --stdout is incompatible with batch modes.", file=sys.stderr)
        return 2
    if args.stdin and (args.crawl or args.batch_file):
        print("ERROR: --stdin is incompatible with --crawl and --batch-file.", file=sys.stderr)
        return 2

    # ---- Stdin mode: HTML piped in, audit it as-is ----
    if args.stdin:
        html = sys.stdin.read()
        if not html.strip():
            print("ERROR: --stdin given but no HTML received.", file=sys.stderr)
            return 1
        source = args.source or "<stdin>"
        # If the caller labelled the HTML with a real URL, also run the
        # external-signals pass against that origin.
        if re.match(r"^https?://", source, re.I):
            result = asyncio.run(audit_html_full(html, source, main_selector=args.main_selector))
        else:
            result = audit_html(html, source, main_selector=args.main_selector)
        if args.stdout:
            sys.stdout.write(render_report(result, source))
        else:
            out_path = write_report(result, source, args.output)
            print(f"Wrote {out_path} (score: {result.score()}/100, grade {result.grade()})", file=sys.stderr)
        return 0

    if args.batch_file:
        if not args.batch_file.exists():
            print(f"ERROR: batch file not found: {args.batch_file}", file=sys.stderr)
            return 1
        targets = read_batch_file(args.batch_file)
        if not targets:
            print(f"ERROR: batch file {args.batch_file} contained no targets.", file=sys.stderr)
            return 1
        origin = f"batch-file:{args.batch_file}"
        return _run_batch(targets, args, origin)

    if args.crawl:
        if not args.input:
            print("ERROR: --crawl requires a URL or bare domain as input.", file=sys.stderr)
            return 2
        crawl_target = args.input.strip()
        if not re.match(r"^https?://", crawl_target, re.I):
            if _looks_like_bare_domain(crawl_target):
                crawl_target = "https://" + crawl_target
            else:
                print(
                    f"ERROR: --crawl input {args.input!r} is not a URL or recognizable domain. "
                    "Pass e.g. 'https://example.com' or 'example.com'.",
                    file=sys.stderr,
                )
                return 2
        print(f"Crawling {crawl_target} (max {args.max_pages} pages)…", file=sys.stderr)
        targets = crawl_urls(crawl_target, max_pages=args.max_pages, delay=args.crawl_delay)
        if not targets:
            print(f"ERROR: crawl from {crawl_target} discovered no pages.", file=sys.stderr)
            return 1
        origin = f"crawl:{crawl_target}"
        return _run_batch(targets, args, origin)

    # ---- Single mode ----
    if not args.input:
        parser.print_help(sys.stderr)
        return 2
    result, source, err = audit_one(args.input, main_selector=args.main_selector)
    if err is not None or result is None:
        print(f"ERROR auditing {args.input}: {err}", file=sys.stderr)
        return 1
    # When the frontend interceptor pre-fetched a URL and passed the local file
    # path here, --source carries the original URL. Use it as the report's
    # source label so the audit shows what the user actually asked about, not
    # the temp file path. Also re-stash source_host for citation density, and
    # run the external-signals pass against the real URL (audit_one ran the
    # sync path because it only saw the local file).
    if args.source:
        source = args.source
        if re.match(r"^https?://", source, re.I):
            result.stats["source_host"] = urlparse(source).netloc
            asyncio.run(audit_external(source, result))
    if args.stdout:
        sys.stdout.write(render_report(result, source))
    else:
        out_path = write_report(result, source, args.output)
        print(f"Wrote {out_path} (score: {result.score()}/100, grade {result.grade()})", file=sys.stderr)
    return 0


def _run_batch(targets: list[str], args, origin: str) -> int:
    print(f"Auditing {len(targets)} pages with {args.workers} worker(s)…", file=sys.stderr)
    rows: list[dict] = []
    with ThreadPoolExecutor(max_workers=max(1, args.workers)) as ex:
        futures = {ex.submit(_audit_for_batch_row, t, args.main_selector): t for t in targets}
        for fut in as_completed(futures):
            row = fut.result()
            rows.append(row)
            if row.get("error") is None:
                print(f"  done [{row['score']:>3}/100 {row['grade']}] {row['source']}", file=sys.stderr)
            else:
                print(f"  fail              {row['source']}  ({row['error']})", file=sys.stderr)

    summary_path = args.summary_output
    if summary_path is None:
        DEFAULT_OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
        summary_path = DEFAULT_OUTPUT_DIR / "batch-summary.md"
    else:
        summary_path.parent.mkdir(parents=True, exist_ok=True)
    summary_path.write_text(render_batch_summary(rows, origin), encoding="utf-8")
    successful = sum(1 for r in rows if r.get("error") is None)
    print(f"\nBatch complete: {successful}/{len(rows)} OK. Summary → {summary_path}", file=sys.stderr)
    return 0


def _stem_from_source(source: str) -> str:
    if re.match(r"^https?://", source, re.I):
        p = urlparse(source)
        path = p.path.strip("/").replace("/", "_") or "index"
        slug = re.sub(r"[^a-zA-Z0-9._-]", "_", f"{p.netloc}_{path}")
        return slug[:80]
    return Path(source).stem


if __name__ == "__main__":
    sys.exit(main())
