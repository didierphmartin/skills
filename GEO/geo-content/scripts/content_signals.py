#!/usr/bin/env python3
"""
content_signals.py — extract E-E-A-T and content-quality signals from a web page.

The script does the deterministic extraction the LLM is bad at: word/sentence/
paragraph counts, heading hierarchy with skipped-level detection, author and
date signals from meta + JSON-LD + byline patterns, trust-page link detection,
internal/external link analysis with non-descriptive anchor counts, JSON-LD
@type discovery, and generic-AI-phrasing tells.

The full structured extraction is printed to STDOUT — what run_skill_script
returns to the model. The LLM applies the 4-pillar E-E-A-T rubric to it.

Pyodide-safe: routes fetches through /gpt/backend/api/v1/fetch-url when
running under Pyodide; uses urllib directly under native CPython.

Usage:
    python content_signals.py https://example.com/blog/my-post
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

NON_DESCRIPTIVE_ANCHORS = {
    "click here", "here", "read more", "learn more", "this", "this link",
    "more", "details", "link", "see more", "view more", "go", "go here",
}

GENERIC_PHRASING = [
    r"in today'?s fast[- ]paced world",
    r"it'?s important to note",
    r"at the end of the day",
    r"when it comes to",
    r"in the realm of",
    r"in the world of",
    r"in this article,? we (?:will )?(?:explore|discuss|delve|examine)",
    r"delve into",
    r"a deep dive (?:into|on)",
    r"in conclusion,",
    r"navigate the (?:complex|complicated) (?:landscape|world)",
    r"a treasure trove",
    r"the (?:digital|modern) landscape",
    r"unlock the (?:power|potential|secrets)",
    r"in the ever-?(?:evolving|changing)",
    r"the world we live in",
    r"that said,?",
    r"first and foremost",
    r"it (?:is|'s) worth (?:noting|mentioning)",
    r"in essence,",
]
GENERIC_RE = re.compile("|".join(GENERIC_PHRASING), re.IGNORECASE)


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
# Signal extraction
# ---------------------------------------------------------------------------

def extract_main(soup: BeautifulSoup):
    """Strip noise + pick a content root. Returns (root, preserved_h1_texts).

    Many themes (Wikipedia, several CMSes) put the article-title H1 inside a
    page <header>. We capture H1 text BEFORE stripping chrome so the heading
    audit doesn't false-negative `H1 count = 0`.
    """
    for tag in soup(["script", "style", "noscript"]):
        tag.decompose()
    root = soup.find("main") or soup.find("article") or soup.find("body") or soup
    preserved_h1s: list[str] = []
    for h in root.find_all("h1"):
        t = h.get_text(" ", strip=True)
        if t and t not in preserved_h1s:
            preserved_h1s.append(t)
    for tag in root.find_all(["nav", "header", "footer", "aside", "form"]):
        tag.decompose()
    return root, preserved_h1s


def extract_text_and_metrics(root) -> dict:
    """Collect main-content text + word/sentence/paragraph metrics."""
    paragraphs: list[str] = []
    for p in root.find_all("p"):
        # Skip <p>s nested in li/td/blockquote/figure caption duplicates.
        if any(parent.name in ("li", "td", "th") for parent in p.parents):
            continue
        t = p.get_text(" ", strip=True)
        if t:
            paragraphs.append(t)

    list_item_texts: list[str] = []
    for li in root.find_all("li"):
        t = li.get_text(" ", strip=True)
        if t:
            list_item_texts.append(t)

    body_text_parts = paragraphs + list_item_texts
    body_text = "\n".join(body_text_parts)

    word_count = len(re.findall(r"\b\w+\b", body_text))
    # Crude sentence counter — split on . ! ? followed by space or EOL.
    sentence_pieces = re.split(r"(?<=[.!?])\s+", body_text)
    sentence_count = sum(1 for s in sentence_pieces if len(s.strip()) > 5)

    avg_sentence_len = round(word_count / sentence_count, 1) if sentence_count else 0
    paragraph_count = len(paragraphs)
    avg_paragraph_sentences = (
        round(sentence_count / paragraph_count, 1) if paragraph_count else 0
    )

    return {
        "word_count": word_count,
        "sentence_count": sentence_count,
        "avg_sentence_length_words": avg_sentence_len,
        "paragraph_count": paragraph_count,
        "list_item_count": len(list_item_texts),
        "avg_paragraph_sentences": avg_paragraph_sentences,
        "body_text": body_text,
    }


def extract_headings(root, preserved_h1s: list[str]) -> dict:
    """Heading hierarchy + skipped-level detection."""
    headings: list[dict] = [{"level": 1, "text": t} for t in preserved_h1s]
    for h in root.find_all(["h1", "h2", "h3", "h4", "h5", "h6"]):
        level = int(h.name[1])
        text = h.get_text(" ", strip=True)
        if not text:
            continue
        # Avoid duplicating an H1 we already captured from the pre-strip pass.
        if level == 1 and any(hd["text"] == text for hd in headings):
            continue
        headings.append({"level": level, "text": text})

    h1_count = sum(1 for h in headings if h["level"] == 1)
    skipped: list[str] = []
    last_level = 0
    for h in headings:
        if last_level and h["level"] > last_level + 1:
            skipped.append(f"H{last_level} -> H{h['level']} at {h['text']!r}")
        last_level = h["level"]

    return {
        "h1_count": h1_count,
        "skipped_levels": skipped,
        "headings": headings,
    }


def extract_links(root, page_url: str) -> dict:
    """Count internal vs external links + non-descriptive anchors."""
    page_host = urlparse(page_url).netloc
    internal = external = 0
    non_descriptive_samples: list[str] = []
    non_descriptive_count = 0

    for a in root.find_all("a"):
        href = (a.get("href") or "").strip()
        if not href or href.startswith(("#", "mailto:", "tel:", "javascript:")):
            continue
        text = a.get_text(" ", strip=True).lower()

        host = urlparse(href).netloc
        if not host or host == page_host:
            internal += 1
        else:
            external += 1

        if text in NON_DESCRIPTIVE_ANCHORS:
            non_descriptive_count += 1
            if len(non_descriptive_samples) < 5:
                non_descriptive_samples.append(text)

    return {
        "internal": internal,
        "external": external,
        "non_descriptive_anchors": non_descriptive_count,
        "non_descriptive_samples": non_descriptive_samples,
    }


def extract_author(soup: BeautifulSoup, body_text: str) -> dict:
    """Multiple strategies: meta -> JSON-LD -> rel=author -> byline regex."""
    out = {"name": None, "source": None}

    # 1. <meta name="author">
    meta = soup.find("meta", attrs={"name": re.compile(r"^author$", re.I)})
    if meta and meta.get("content"):
        out["name"] = meta["content"].strip()
        out["source"] = "meta[name=author]"
        return out

    # 2. JSON-LD
    for script in soup.find_all("script", type="application/ld+json"):
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
                out["name"] = str(author["name"]).strip()
                out["source"] = "json-ld author.name"
                return out
            if isinstance(author, str) and author.strip():
                out["name"] = author.strip()
                out["source"] = "json-ld author (string)"
                return out
            graph = it.get("@graph")
            if isinstance(graph, list):
                stack.extend(g for g in graph if isinstance(g, dict))

    # 3. <a rel="author">
    rel_author = soup.find("a", attrs={"rel": lambda v: v and "author" in (v if isinstance(v, list) else [v])})
    if rel_author:
        text = rel_author.get_text(" ", strip=True)
        if text:
            out["name"] = text
            out["source"] = "<a rel=author>"
            return out

    # 4. byline regex on first 800 chars of body
    head = body_text[:800]
    m = re.search(r"\b[Bb]y\s+([A-Z][a-z]+(?:\s+[A-Z][a-z'\-]+){1,3})", head)
    if m:
        out["name"] = m.group(1)
        out["source"] = "byline regex"
        return out

    return out


def extract_dates(soup: BeautifulSoup) -> dict:
    """datePublished / dateModified from meta, JSON-LD, and <time>."""
    out = {"published": None, "modified": None, "sources": []}

    for prop, key in (("article:published_time", "published"),
                       ("article:modified_time", "modified")):
        m = soup.find("meta", attrs={"property": prop})
        if m and m.get("content"):
            out[key] = m["content"].strip()
            out["sources"].append(f"meta[{prop}] -> {key}")

    # JSON-LD
    for script in soup.find_all("script", type="application/ld+json"):
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
                out["sources"].append("json-ld datePublished")
            if not out["modified"] and isinstance(it.get("dateModified"), str):
                out["modified"] = it["dateModified"]
                out["sources"].append("json-ld dateModified")
            graph = it.get("@graph")
            if isinstance(graph, list):
                stack.extend(g for g in graph if isinstance(g, dict))

    # <time datetime="..."> as a last resort
    if not out["published"]:
        t = soup.find("time", attrs={"datetime": True})
        if t:
            out["published"] = t["datetime"]
            out["sources"].append("<time datetime>")

    return out


TRUST_LINK_PATTERNS = {
    "privacy": re.compile(r"\bprivacy\b", re.I),
    "terms":   re.compile(r"\bterms\b|terms of (?:service|use)", re.I),
    "contact": re.compile(r"\bcontact\b", re.I),
    "about":   re.compile(r"\babout\b", re.I),
}


def extract_trust_links(full_soup: BeautifulSoup) -> dict:
    """Scan ALL anchors (including footer/nav) for trust-page links."""
    found = {k: None for k in TRUST_LINK_PATTERNS}
    for a in full_soup.find_all("a"):
        text = a.get_text(" ", strip=True).lower()
        href = (a.get("href") or "").lower()
        for kind, pattern in TRUST_LINK_PATTERNS.items():
            if found[kind]:
                continue
            if pattern.search(text) or pattern.search(href):
                found[kind] = href or text
    return found


def extract_jsonld_types(soup: BeautifulSoup) -> list[str]:
    types: list[str] = []
    for script in soup.find_all("script", type="application/ld+json"):
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
            graph = it.get("@graph")
            if isinstance(graph, list):
                stack.extend(g for g in graph if isinstance(g, dict))
    # Dedup preserving order
    seen = set()
    out = []
    for t in types:
        if t not in seen:
            out.append(t); seen.add(t)
    return out


def count_generic_tells(text: str) -> tuple[int, list[str]]:
    matches = GENERIC_RE.findall(text)
    count = len(matches)
    samples: list[str] = []
    for m in matches[:6]:
        # m may be a string or a tuple (when alternation groups capture);
        # findall returns groups when the regex has groups.
        if isinstance(m, tuple):
            m = next((x for x in m if x), "")
        if m and m not in samples:
            samples.append(m)
    return count, samples


# ---------------------------------------------------------------------------
# Aggregation + rendering
# ---------------------------------------------------------------------------

def analyze(html: str, url: str) -> dict:
    full_soup = BeautifulSoup(html, "html.parser")
    # Make a separate soup for main-content extraction so we don't destroy
    # the footer/nav we need for trust-link detection.
    content_soup = BeautifulSoup(html, "html.parser")
    root, preserved_h1s = extract_main(content_soup)

    title = ""
    if full_soup.title and full_soup.title.string:
        title = full_soup.title.string.strip()

    metrics = extract_text_and_metrics(root)
    headings = extract_headings(root, preserved_h1s)
    links = extract_links(root, url)
    author = extract_author(full_soup, metrics["body_text"])
    dates = extract_dates(full_soup)
    trust = extract_trust_links(full_soup)
    jsonld_types = extract_jsonld_types(full_soup)
    generic_count, generic_samples = count_generic_tells(metrics["body_text"])

    https = urlparse(url).scheme == "https"

    return {
        "url": url,
        "title": title,
        "https": https,
        "fetched_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "metrics": {k: v for k, v in metrics.items() if k != "body_text"},
        "headings": headings,
        "links": links,
        "author": author,
        "dates": dates,
        "trust_links": trust,
        "jsonld_types": jsonld_types,
        "generic_phrasing": {"count": generic_count, "samples": generic_samples},
        "body_text_head": metrics["body_text"][:1200],
        "body_text_tail": metrics["body_text"][-600:] if len(metrics["body_text"]) > 1800 else "",
    }


def render(result: dict) -> str:
    m = result["metrics"]
    h = result["headings"]
    L = [
        f"# Content E-E-A-T extraction: {result['title'] or '(no title)'}",
        "",
        f"**URL:** {result['url']}",
        f"**Fetched:** {result['fetched_at']}",
        f"**HTTPS:** {'yes' if result['https'] else 'no'}",
        "",
    ]

    if m["word_count"] == 0:
        L += ["**No body text extracted.** The page may be JS-rendered or empty. "
              "Do not score — tell the user to enable SSR or pass a static page.",
              ""]
        return "\n".join(L)

    L += [
        "## Page metrics",
        "",
        f"- Word count: **{m['word_count']}**",
        f"- Sentences: {m['sentence_count']}  ·  avg length **{m['avg_sentence_length_words']}** words (ideal 15-20)",
        f"- Paragraphs: {m['paragraph_count']}  ·  avg **{m['avg_paragraph_sentences']}** sentences (ideal 2-4)",
        f"- List items: {m['list_item_count']}",
        "",
        "## Heading hierarchy",
        "",
        f"- H1 count: **{h['h1_count']}** (should be 1)",
        f"- Skipped levels: {h['skipped_levels'] if h['skipped_levels'] else 'none'}",
        "",
        "Headings in order:",
    ]
    for hd in h["headings"][:30]:
        L.append(f"- H{hd['level']}: {hd['text']}")
    if len(h["headings"]) > 30:
        L.append(f"- … {len(h['headings']) - 30} more headings")
    L.append("")

    a = result["author"]
    L += [
        "## Author signal",
        "",
        f"- Name: {a['name'] or 'NOT DETECTED'}",
        f"- Source: {a['source'] or '-'}",
        "",
    ]

    d = result["dates"]
    L += [
        "## Dates",
        "",
        f"- Published: {d['published'] or 'not declared'}",
        f"- Modified: {d['modified'] or 'not declared'}",
        f"- Sources: {', '.join(d['sources']) if d['sources'] else 'none'}",
        "",
    ]

    t = result["trust_links"]
    L += [
        "## Trust-page links (footer/nav scan)",
        "",
        f"- Privacy: {'found - ' + t['privacy'] if t['privacy'] else 'NOT FOUND'}",
        f"- Terms: {'found - ' + t['terms'] if t['terms'] else 'NOT FOUND'}",
        f"- Contact: {'found - ' + t['contact'] if t['contact'] else 'NOT FOUND'}",
        f"- About: {'found - ' + t['about'] if t['about'] else 'NOT FOUND'}",
        "",
    ]

    ln = result["links"]
    L += [
        "## Links (main content)",
        "",
        f"- Internal: {ln['internal']}  ·  External: {ln['external']}",
        f"- Non-descriptive anchors ('click here', 'here', 'read more'): "
        f"**{ln['non_descriptive_anchors']}**"
        + (f" — samples: {ln['non_descriptive_samples']}" if ln['non_descriptive_samples'] else ""),
        "",
    ]

    L += [
        "## Structured data",
        "",
        f"- JSON-LD @types: {result['jsonld_types'] if result['jsonld_types'] else 'none'}",
        "",
    ]

    g = result["generic_phrasing"]
    L += [
        "## Generic-AI-phrasing tells",
        "",
        f"- Matches: **{g['count']}**",
        f"- Samples: {g['samples'] if g['samples'] else 'none'}",
        "",
    ]

    L += ["## Body text — first 1200 chars", "", result["body_text_head"], ""]
    if result["body_text_tail"]:
        L += ["## Body text — last 600 chars", "", result["body_text_tail"], ""]

    return "\n".join(L).rstrip() + "\n"


# ---------------------------------------------------------------------------
# Output paths
# ---------------------------------------------------------------------------

def resolve_output_paths(output: Path | None) -> tuple[Path, Path]:
    if output is None:
        base = DEFAULT_OUTPUT_DIR
        return base / "GEO-CONTENT-EXTRACT.md", base / "GEO-CONTENT-EXTRACT.json"
    if output.suffix == "" or output.is_dir():
        return output / "GEO-CONTENT-EXTRACT.md", output / "GEO-CONTENT-EXTRACT.json"
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
    if not resp:
        print(f"ERROR: fetch failed for {url}", file=sys.stderr)
        return 1
    status = resp.get("status", 0)
    body = resp.get("body", "")
    if not body:
        print(f"ERROR: empty body for {url} (HTTP {status})", file=sys.stderr)
        return 1

    result = analyze(body, resp.get("final_url") or url)
    result["http_status"] = status
    report = render(result)

    md_path, json_path = resolve_output_paths(args.output)
    md_path.parent.mkdir(parents=True, exist_ok=True)
    md_path.write_text(report, encoding="utf-8")
    json_path.write_text(json.dumps(result, indent=2, ensure_ascii=False) + "\n",
                         encoding="utf-8")

    # Full extraction to stdout — what run_skill_script returns to the model.
    print(report)

    print(f"[geo-content] words={result['metrics']['word_count']} "
          f"H1={result['headings']['h1_count']} "
          f"author={'yes' if result['author']['name'] else 'no'} — wrote {md_path}",
          file=sys.stderr)
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Extract E-E-A-T and content-quality signals from a page.")
    parser.add_argument("input", nargs="?", help="Page URL (positional).")
    parser.add_argument("--url", dest="url_alias", default=None,
                        help="Alias for the positional URL argument.")
    parser.add_argument("--source", default=None,
                        help="Source URL label when the runner pre-fetches a "
                             "local file.")
    parser.add_argument("-o", "--output", type=Path, default=None,
                        help="Output path. Default: "
                             "~/Documents/synergyAI/outputs/ (/outputs/ in "
                             "Pyodide). The runner may inject "
                             "-o /outputs/<stem>-audit.md (a file path).")
    args = parser.parse_args(argv)

    if not args.input and args.url_alias:
        args.input = args.url_alias
    if args.source and re.match(r"^https?://", args.source, re.I):
        args.input = args.source

    return asyncio.run(amain(args))


if __name__ == "__main__":
    sys.exit(main())
