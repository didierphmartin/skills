#!/usr/bin/env python3
"""
platform_signals.py — extract platform-relevant on-page signals for the
geo-platform-optimizer skill.

Groups on-page features by which AI search platform values them:
- AIO:        question-headings, direct-answer candidates, tables, lists, FAQ,
              definition patterns, statistics, dates, author, hierarchy
- ChatGPT:    word count (≥2000), outbound entity links (Wikipedia/Wikidata/
              LinkedIn/Crunchbase), JSON-LD sameAs, author + credentials hint
- Perplexity: dates, multi-source link count, community-platform outbound links
              (Reddit/HN/Stack Overflow/Quora)
- Gemini:     JSON-LD @types + sameAs, image alt-coverage, YouTube embeds,
              multi-modal counts (image/video/audio/iframe), author
- Copilot:    meta description length, LinkedIn/GitHub outbound links, title/H1

What this script does NOT verify (off-page; LLM must mark N/A):
- Wikipedia article existence, Wikidata entity, Bing index coverage,
- Google Knowledge Panel, GBP, authoritative backlinks, top-10 SERP rank,
- Reddit / forum brand mentions in the wild, IndexNow registration,
- Page-load speed / Core Web Vitals.

Pyodide-safe: routes the fetch through /gpt/backend/api/v1/fetch-url under
Pyodide; uses urllib directly under native CPython.

Usage:
    python platform_signals.py https://example.com/blog/post
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
from urllib.parse import urlparse

try:
    from bs4 import BeautifulSoup  # type: ignore
except ImportError:
    print("ERROR: beautifulsoup4 is required (declare it in SKILL.md "
          "dependencies). Run: pip install beautifulsoup4", file=sys.stderr)
    sys.exit(1)


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

# Statistic regex reused across geo skills.
STAT_RE = re.compile(
    "|".join([
        r"\b\d+(?:\.\d+)?\s*%",
        r"\$\s?\d[\d,]*(?:\.\d+)?",
        r"\b\d+(?:,\d{3})+\b",
        r"\b\d+\+\b",
        r"\b(?:19|20)\d{2}s?\b",
        r"\b\d+(?:\.\d+)?\s*(?:million|billion|thousand|k|m|bn)\b",
        r"\b\d+\s*(?:hours?|days?|weeks?|months?|years?|minutes?)\b",
    ]),
    re.IGNORECASE,
)

# Definition patterns — "X is …", "X refers to …", "X means …".
DEFN_RE = re.compile(
    r"\b[A-Z][\w\- ]{2,40}\s+(?:is|are|refers? to|means|stands for)\s+\w",
)

# FAQ heading detection.
FAQ_HEADING_RE = re.compile(r"\b(FAQ|frequently asked|Q&A|common questions)\b",
                             re.IGNORECASE)

# Entity-grounding domains: where outbound anchors signal entity / authority.
ENTITY_DOMAINS = {
    "wikipedia": ("wikipedia.org",),
    "wikidata": ("wikidata.org",),
    "linkedin": ("linkedin.com",),
    "github": ("github.com",),
    "youtube": ("youtube.com", "youtu.be"),
    "crunchbase": ("crunchbase.com",),
    "reddit": ("reddit.com",),
    "hackernews": ("news.ycombinator.com",),
    "stackoverflow": ("stackoverflow.com", "stackexchange.com"),
    "quora": ("quora.com",),
}

GENERIC_IMG_FILENAME_RE = re.compile(
    r"^(img|dsc|dscn|photo|picture|screenshot|untitled|image|file)[_\-]?\d*"
    r"|^\d+(\.\w+)?$",
    re.IGNORECASE,
)


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
        "Accept": "text/html,application/xhtml+xml,*/*;q=0.8",
        "Accept-Encoding": "gzip",
    })
    try:
        with urllib.request.urlopen(req, timeout=20, context=ctx) as r:
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


async def fetch(url: str) -> dict | None:
    if _is_pyodide():
        return await _fetch_pyodide(url)
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(None, _fetch_urllib, url)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _is_inside(el, tag_names) -> bool:
    for parent in el.parents:
        if parent.name in tag_names:
            return True
    return False


def _classify_link(host: str, page_host: str) -> tuple[str, str | None]:
    """Return (kind, entity_key). kind in {'internal','external'}; entity_key
    is one of ENTITY_DOMAINS keys if the host matches a known entity domain."""
    if not host or host == page_host:
        return "internal", None
    host_l = host.lower()
    for key, domains in ENTITY_DOMAINS.items():
        for d in domains:
            if host_l == d or host_l.endswith("." + d):
                return "external", key
    return "external", None


def extract_jsonld(full_soup: BeautifulSoup) -> dict:
    """Return {'types': [...], 'sameAs': [...]}."""
    types: list[str] = []
    same_as: list[str] = []
    for script in full_soup.find_all("script", type="application/ld+json"):
        if not script.string:
            continue
        try:
            data = json.loads(script.string)
        except json.JSONDecodeError:
            continue
        stack = [data] if not isinstance(data, list) else list(data)
        while stack:
            it = stack.pop()
            if not isinstance(it, dict):
                continue
            t = it.get("@type")
            if t:
                if isinstance(t, list):
                    types.extend(str(x) for x in t)
                else:
                    types.append(str(t))
            sa = it.get("sameAs")
            if isinstance(sa, list):
                same_as.extend(str(x) for x in sa if isinstance(x, str))
            elif isinstance(sa, str):
                same_as.append(sa)
            g = it.get("@graph")
            if isinstance(g, list):
                stack.extend(x for x in g if isinstance(x, dict))
    # dedup preserving order
    seen = set()
    types = [t for t in types if not (t in seen or seen.add(t))]
    seen = set()
    same_as = [s for s in same_as if not (s in seen or seen.add(s))]
    return {"types": types, "sameAs": same_as}


def extract_author(full_soup: BeautifulSoup, body_text: str) -> dict:
    """Same multi-strategy as geo-content."""
    meta = full_soup.find("meta", attrs={"name": re.compile(r"^author$", re.I)})
    if meta and meta.get("content"):
        return {"name": meta["content"].strip(), "source": "meta[name=author]"}

    for script in full_soup.find_all("script", type="application/ld+json"):
        if not script.string:
            continue
        try:
            data = json.loads(script.string)
        except json.JSONDecodeError:
            continue
        stack = [data] if not isinstance(data, list) else list(data)
        while stack:
            it = stack.pop()
            if not isinstance(it, dict):
                continue
            author = it.get("author")
            if isinstance(author, dict) and author.get("name"):
                return {"name": str(author["name"]).strip(), "source": "json-ld author.name"}
            if isinstance(author, str) and author.strip():
                return {"name": author.strip(), "source": "json-ld author (string)"}
            g = it.get("@graph")
            if isinstance(g, list):
                stack.extend(x for x in g if isinstance(x, dict))

    rel_author = full_soup.find(
        "a", attrs={"rel": lambda v: v and "author" in (v if isinstance(v, list) else [v])})
    if rel_author:
        text = rel_author.get_text(" ", strip=True)
        if text:
            return {"name": text, "source": "<a rel=author>"}

    m = re.search(r"\b[Bb]y\s+([A-Z][a-z]+(?:\s+[A-Z][a-z'\-]+){1,3})", body_text[:800])
    if m:
        return {"name": m.group(1), "source": "byline regex"}

    return {"name": None, "source": None}


def extract_dates(full_soup: BeautifulSoup) -> dict:
    out = {"published": None, "modified": None}
    for prop, key in (("article:published_time", "published"),
                       ("article:modified_time", "modified")):
        m = full_soup.find("meta", attrs={"property": prop})
        if m and m.get("content"):
            out[key] = m["content"].strip()
    for script in full_soup.find_all("script", type="application/ld+json"):
        if not script.string:
            continue
        try:
            data = json.loads(script.string)
        except json.JSONDecodeError:
            continue
        stack = [data] if not isinstance(data, list) else list(data)
        while stack:
            it = stack.pop()
            if not isinstance(it, dict):
                continue
            if not out["published"] and isinstance(it.get("datePublished"), str):
                out["published"] = it["datePublished"]
            if not out["modified"] and isinstance(it.get("dateModified"), str):
                out["modified"] = it["dateModified"]
            g = it.get("@graph")
            if isinstance(g, list):
                stack.extend(x for x in g if isinstance(x, dict))
    return out


# ---------------------------------------------------------------------------
# Main extraction
# ---------------------------------------------------------------------------

def extract_signals(html: str, url: str) -> dict:
    full = BeautifulSoup(html, "html.parser")
    content_soup = BeautifulSoup(html, "html.parser")
    for tag in content_soup(["script", "style", "noscript"]):
        tag.decompose()
    root = (content_soup.find("main") or content_soup.find("article")
            or content_soup.find("body") or content_soup)
    preserved_h1s = [h.get_text(" ", strip=True)
                     for h in root.find_all("h1") if h.get_text(strip=True)]
    for tag in root.find_all(["nav", "header", "footer", "aside", "form"]):
        tag.decompose()

    title = full.title.string.strip() if full.title and full.title.string else None
    meta_desc = None
    md = full.find("meta", attrs={"name": re.compile(r"^description$", re.I)})
    if md and md.get("content"):
        meta_desc = md["content"].strip()

    # Headings (with question marker) + direct-answer candidates.
    headings: list[dict] = [{"level": 1, "text": t, "is_question": t.rstrip().endswith("?")}
                            for t in preserved_h1s]
    direct_answers: list[dict] = []
    flat = root.find_all(["h1", "h2", "h3", "p"])
    last_q_heading: str | None = None
    for el in flat:
        text = el.get_text(" ", strip=True)
        if not text:
            continue
        if el.name in ("h1", "h2", "h3"):
            level = int(el.name[1])
            is_q = text.rstrip().endswith("?")
            # Avoid duplicating an H1 already preserved.
            if level == 1 and any(h["text"] == text for h in headings):
                last_q_heading = text if is_q else None
                continue
            headings.append({"level": level, "text": text, "is_question": is_q})
            last_q_heading = text if is_q else None
        elif el.name == "p" and last_q_heading and not _is_inside(el, ("li", "td", "th")):
            # First p after a Q-heading is the direct-answer candidate.
            first_sentence = re.split(r"(?<=[.!?])\s+", text)[0]
            direct_answers.append({
                "question": last_q_heading,
                "first_sentence": first_sentence,
                "first_sentence_word_count":
                    len(re.findall(r"\b\w+\b", first_sentence)),
            })
            last_q_heading = None  # only the first p

    h1_count = sum(1 for h in headings if h["level"] == 1)
    h2_count = sum(1 for h in headings if h["level"] == 2)
    h3_count = sum(1 for h in headings if h["level"] == 3)
    question_headings = [h for h in headings if h["is_question"]]
    skipped: list[str] = []
    last = 0
    for h in headings:
        if last and h["level"] > last + 1:
            skipped.append(f"H{last} -> H{h['level']} at {h['text']!r}")
        last = h["level"]

    # FAQ detection.
    faq_heading_present = any(FAQ_HEADING_RE.search(h["text"]) for h in headings)
    details_count = len(root.find_all("details"))

    # Body text + counts.
    paragraphs = [p for p in root.find_all("p")
                  if not _is_inside(p, ("li", "td", "th"))]
    body_text = "\n".join(p.get_text(" ", strip=True) for p in paragraphs
                          if p.get_text(strip=True))
    word_count = len(re.findall(r"\b\w+\b", body_text))
    tables = len(root.find_all("table"))
    ul_count = sum(1 for ul in root.find_all("ul")
                   if not _is_inside(ul, ("ul", "ol")))
    ol_count = sum(1 for ol in root.find_all("ol")
                   if not _is_inside(ol, ("ul", "ol")))

    statistic_count = len(STAT_RE.findall(body_text))
    definition_count = len(DEFN_RE.findall(body_text))

    # Images + alt-coverage.
    imgs = root.find_all("img")
    img_total = len(imgs)
    img_with_alt = sum(1 for i in imgs if (i.get("alt") or "").strip())
    img_generic_filename = 0
    for i in imgs:
        src = (i.get("src") or i.get("data-src") or "")
        if src:
            fname = src.rsplit("/", 1)[-1].split("?", 1)[0]
            stem = fname.rsplit(".", 1)[0]
            if GENERIC_IMG_FILENAME_RE.match(stem):
                img_generic_filename += 1
    alt_coverage_pct = round((img_with_alt / img_total) * 100) if img_total else None

    # Multi-modal.
    iframes = root.find_all("iframe")
    youtube_embeds = sum(1 for f in iframes
                          if "youtube.com" in (f.get("src") or "")
                          or "youtu.be" in (f.get("src") or ""))
    video_count = len(root.find_all("video"))
    audio_count = len(root.find_all("audio"))

    # Outbound links + entity-grounding counts.
    page_host = urlparse(url).netloc
    internal_links = external_links = 0
    entity_counts = {k: 0 for k in ENTITY_DOMAINS}
    for a in root.find_all("a"):
        href = (a.get("href") or "").strip()
        if not href or href.startswith(("#", "mailto:", "tel:", "javascript:")):
            continue
        host = urlparse(href).netloc
        kind, ekey = _classify_link(host, page_host)
        if kind == "internal":
            internal_links += 1
        else:
            external_links += 1
            if ekey:
                entity_counts[ekey] += 1

    jsonld = extract_jsonld(full)
    author = extract_author(full, body_text)
    dates = extract_dates(full)

    return {
        "url": url,
        "title": title,
        "meta_description": meta_desc,
        "meta_description_length": len(meta_desc) if meta_desc else 0,
        "headings": headings,
        "heading_counts": {"H1": h1_count, "H2": h2_count, "H3": h3_count},
        "question_headings_count": len(question_headings),
        "question_headings": [h["text"] for h in question_headings],
        "skipped_levels": skipped,
        "direct_answers": direct_answers,
        "faq_section_heading": faq_heading_present,
        "details_summary_blocks": details_count,
        "word_count": word_count,
        "paragraph_count": len(paragraphs),
        "table_count": tables,
        "ul_count": ul_count,
        "ol_count": ol_count,
        "statistic_count": statistic_count,
        "definition_pattern_count": definition_count,
        "images": {
            "total": img_total,
            "with_alt": img_with_alt,
            "alt_coverage_pct": alt_coverage_pct,
            "generic_filename_count": img_generic_filename,
        },
        "multimodal": {
            "iframes": len(iframes),
            "youtube_embeds": youtube_embeds,
            "video_tags": video_count,
            "audio_tags": audio_count,
        },
        "links": {"internal": internal_links, "external": external_links},
        "entity_grounding": entity_counts,
        "jsonld": jsonld,
        "author": author,
        "dates": dates,
        "url_host": page_host,
        "raw_text_sample": body_text[:1200],
    }


# ---------------------------------------------------------------------------
# Report rendering — grouped by platform
# ---------------------------------------------------------------------------

def render(result: dict) -> str:
    s = result
    L = [
        f"# Platform optimization signals: {s['title'] or '(no title)'}",
        "",
        f"**URL:** {s['url']}",
        f"**Fetched:** {s['fetched_at']}",
        f"**Word count:** {s['word_count']}  ·  **Paragraphs:** {s['paragraph_count']}",
        "",
    ]
    if s["word_count"] == 0:
        L += ["**No body text extracted.** Page may be JS-rendered or empty. "
              "Do not score — tell the user the page needs SSR.", ""]
        return "\n".join(L)

    # AIO
    L += [
        "## Google AI Overviews — relevant signals",
        "",
        f"- Question-based headings: **{s['question_headings_count']}** "
        f"(H1+H2+H3 ending in '?')",
    ]
    for q in s["question_headings"][:8]:
        L.append(f"  - {q}")
    if len(s["question_headings"]) > 8:
        L.append(f"  - … {len(s['question_headings']) - 8} more")
    L += [
        f"- Direct-answer candidates (first sentence after Q-headings): "
        f"**{len(s['direct_answers'])}**",
    ]
    for da in s["direct_answers"][:5]:
        L.append(f"  - Q: {da['question']!r}")
        L.append(f"    → A ({da['first_sentence_word_count']} words): "
                 f"{da['first_sentence'][:200]}")
    L += [
        f"- Tables: **{s['table_count']}**  ·  Ordered lists: "
        f"{s['ol_count']}  ·  Unordered lists: {s['ul_count']}",
        f"- FAQ section detected: "
        f"{'yes (heading)' if s['faq_section_heading'] else 'no'}"
        + (f"  ·  <details> blocks: {s['details_summary_blocks']}"
           if s['details_summary_blocks'] else ""),
        f"- Definition patterns ('X is …'): **{s['definition_pattern_count']}**",
        f"- Statistics detected: **{s['statistic_count']}**",
        f"- Author byline: {s['author']['name'] or 'NOT DETECTED'} "
        f"({s['author']['source'] or '-'})",
        f"- Published: {s['dates']['published'] or 'not declared'}  ·  "
        f"Modified: {s['dates']['modified'] or 'not declared'}",
        f"- Heading hierarchy: H1={s['heading_counts']['H1']}, "
        f"H2={s['heading_counts']['H2']}, H3={s['heading_counts']['H3']}  ·  "
        f"Skipped levels: {s['skipped_levels'] if s['skipped_levels'] else 'none'}",
        "",
        "Off-page (mark N/A unless user supplied SERP data): top-10 organic "
        "ranking for target queries.",
        "",
    ]

    # ChatGPT
    e = s["entity_grounding"]
    L += [
        "## ChatGPT Web Search — relevant signals",
        "",
        f"- Word count vs. 2000-word comprehensive threshold: "
        f"**{s['word_count']}** "
        f"({'meets' if s['word_count'] >= 2000 else 'BELOW'} threshold)",
        f"- Outbound entity-grounding anchors:",
        f"  - Wikipedia: {e['wikipedia']}  ·  Wikidata: {e['wikidata']}  ·  "
        f"LinkedIn: {e['linkedin']}  ·  Crunchbase: {e['crunchbase']}",
        f"- JSON-LD sameAs URLs ({len(s['jsonld']['sameAs'])}): "
        f"{s['jsonld']['sameAs'][:6] if s['jsonld']['sameAs'] else 'none'}",
        f"- Author byline: {s['author']['name'] or 'NOT DETECTED'} — "
        f"check body text for credentials (LLM judgment)",
        "",
        "Off-page (mark N/A unless verified externally): Wikipedia article "
        "existence, Wikidata entity, Bing index coverage, Reddit brand "
        "mentions, authoritative backlinks.",
        "",
    ]

    # Perplexity
    L += [
        "## Perplexity — relevant signals",
        "",
        f"- Dates: published {s['dates']['published'] or 'not declared'}  ·  "
        f"modified {s['dates']['modified'] or 'not declared'} "
        f"(Perplexity weights freshness heavily)",
        f"- External links (multi-source claim validation): "
        f"**{s['links']['external']}**",
        f"- Community-platform outbound anchors:",
        f"  - Reddit: {e['reddit']}  ·  Hacker News: {e['hackernews']}  ·  "
        f"Stack Overflow: {e['stackoverflow']}  ·  Quora: {e['quora']}",
        f"- YouTube embeds on page: {s['multimodal']['youtube_embeds']}",
        "",
        "Off-page (mark N/A unless verified externally): brand's actual Reddit "
        "presence, HN/forum mentions, YouTube channel existence.",
        "",
    ]

    # Gemini
    img = s["images"]
    mm = s["multimodal"]
    L += [
        "## Gemini — relevant signals",
        "",
        f"- JSON-LD @types ({len(s['jsonld']['types'])}): "
        f"{s['jsonld']['types'] if s['jsonld']['types'] else 'none'}",
        f"- JSON-LD sameAs: {len(s['jsonld']['sameAs'])} URL(s)",
        f"- Images: {img['total']} total  ·  with alt: {img['with_alt']}  ·  "
        f"alt coverage: "
        + (f"{img['alt_coverage_pct']}%" if img['alt_coverage_pct'] is not None else "n/a"),
        f"- Generic-filename images (IMG_xxxx etc.): {img['generic_filename_count']}",
        f"- YouTube embeds: {mm['youtube_embeds']}  ·  <video>: {mm['video_tags']}  ·  "
        f"<audio>: {mm['audio_tags']}  ·  <iframe>: {mm['iframes']}",
        f"- Multi-modal score (text + images + video): "
        + ("rich (≥1 image AND ≥1 video/iframe)"
           if img['total'] > 0 and (mm['youtube_embeds'] + mm['video_tags'] + mm['iframes']) > 0
           else "partial (images only)" if img['total'] > 0
           else "text-only"),
        f"- E-E-A-T author signal: {s['author']['name'] or 'NOT DETECTED'}",
        "",
        "Off-page (mark N/A unless verified externally): Google Knowledge "
        "Panel, Google Business Profile, YouTube channel content, Google "
        "Merchant Center.",
        "",
    ]

    # Copilot
    L += [
        "## Bing Copilot — relevant signals",
        "",
        f"- Meta description: "
        + (f"**{s['meta_description_length']} chars** — "
           + ("OK (120-160 ideal)" if 100 <= s['meta_description_length'] <= 170
              else "OUTSIDE 120-160 ideal range")
           if s['meta_description'] else "MISSING"),
        f"- LinkedIn outbound links: {e['linkedin']}  ·  GitHub: {e['github']}",
        f"- Title: {s['title'] or 'MISSING'}",
        f"- First H1: {next((h['text'] for h in s['headings'] if h['level'] == 1), 'MISSING')}",
        "",
        "Off-page (mark N/A unless verified externally): Bing Webmaster Tools, "
        "IndexNow registration, Bing index coverage, LinkedIn company page, "
        "page-load speed.",
        "",
        "## Cross-platform context",
        "",
        f"- HTTPS: {'yes' if urlparse(s['url']).scheme == 'https' else 'no'}",
        f"- Internal links: {s['links']['internal']}  ·  External: {s['links']['external']}",
        "",
        "## Raw text sample (for LLM judgment on credentials, original "
        "research, tone)",
        "",
        s["raw_text_sample"] or "(empty)",
        "",
    ]
    return "\n".join(L).rstrip() + "\n"


# ---------------------------------------------------------------------------
# Output paths
# ---------------------------------------------------------------------------

def resolve_output_paths(output: Path | None) -> tuple[Path, Path]:
    if output is None:
        base = DEFAULT_OUTPUT_DIR
        return base / "GEO-PLATFORM-EXTRACT.md", base / "GEO-PLATFORM-EXTRACT.json"
    if output.suffix == "" or output.is_dir():
        return output / "GEO-PLATFORM-EXTRACT.md", output / "GEO-PLATFORM-EXTRACT.json"
    return output, output.with_suffix(".json")


# ---------------------------------------------------------------------------
# Main pipeline
# ---------------------------------------------------------------------------

async def amain(args) -> int:
    if not args.input:
        print("ERROR: a page URL is required.", file=sys.stderr)
        return 2
    url = args.input
    if not urlparse(url).scheme:
        url = "https://" + url
    if not urlparse(url).netloc:
        print(f"ERROR: could not parse a host from {args.input!r}", file=sys.stderr)
        return 2

    print(f"Fetching {url}…", file=sys.stderr)
    resp = await fetch(url)
    if not resp or not resp.get("body"):
        print(f"ERROR: page fetch failed for {url}", file=sys.stderr)
        return 1

    result = extract_signals(resp["body"], resp.get("final_url") or url)
    result["fetched_at"] = time.strftime("%Y-%m-%d %H:%M:%S")
    result["http_status"] = resp.get("status", 0)

    report = render(result)
    md_path, json_path = resolve_output_paths(args.output)
    md_path.parent.mkdir(parents=True, exist_ok=True)
    md_path.write_text(report, encoding="utf-8")
    json_path.write_text(json.dumps(result, indent=2, ensure_ascii=False) + "\n",
                         encoding="utf-8")

    # Full extraction to stdout — what run_skill_script returns to the model.
    print(report)

    print(f"[geo-platform-optimizer] words={result['word_count']} "
          f"q-headings={result['question_headings_count']} "
          f"tables={result['table_count']} "
          f"images={result['images']['total']} "
          f"entity-links={sum(result['entity_grounding'].values())} "
          f"— wrote {md_path}", file=sys.stderr)
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Platform-relevant on-page signal extraction "
                    "(AIO / ChatGPT / Perplexity / Gemini / Copilot).")
    parser.add_argument("input", nargs="?", help="Page URL (positional).")
    parser.add_argument("--url", dest="url_alias", default=None,
                        help="Alias for the positional URL argument.")
    parser.add_argument("--source", default=None,
                        help="Source URL label when the runner pre-fetches a "
                             "local file.")
    parser.add_argument("-o", "--output", type=Path, default=None,
                        help="Output path. Default: "
                             "~/Documents/synergyAI/outputs/ (/outputs/ in "
                             "Pyodide).")
    args = parser.parse_args(argv)

    if not args.input and args.url_alias:
        args.input = args.url_alias
    if args.source and re.match(r"^https?://", args.source, re.I):
        args.input = args.source

    return asyncio.run(amain(args))


if __name__ == "__main__":
    sys.exit(main())
