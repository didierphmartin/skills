#!/usr/bin/env python3
"""
citability_parse.py — fetch a web page and segment it into content blocks for
the geo-citability skill.

The script does the deterministic work the LLM is bad at: fetching the page,
stripping nav/header/footer/aside/script/style, segmenting at H2/H3 headings,
and computing exact per-block word counts plus paragraph / list / table /
statistic-density counts.

The full structured breakdown is printed to STDOUT — that is what
run_skill_script returns to the model, and what the LLM scores against the
geo-citability rubric.

Pyodide-safe: routes fetches through /gpt/backend/api/v1/fetch-url when running
under Pyodide; uses urllib directly under native CPython.

Usage:
    python citability_parse.py https://example.com/blog/my-post
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

# Patterns that count as "a statistic" for Category 4 (Statistical Density).
# Vague quantifiers ("many", "most") deliberately do NOT match.
STAT_RE = re.compile(
    "|".join([
        r"\b\d+(?:\.\d+)?\s*%",                                       # 73%, 4.5 %
        r"\$\s?\d[\d,]*(?:\.\d+)?",                                   # $4,500, $1.5
        r"\b\d+(?:,\d{3})+\b",                                        # 4,500
        r"\b\d+\+\b",                                                 # 340+
        r"\b(?:19|20)\d{2}s?\b",                                      # 2024, 1990s
        r"\b\d+(?:\.\d+)?\s*(?:million|billion|thousand|k|m|bn)\b",   # 2 million, 5k
        r"\b\d+\s*(?:hours?|days?|weeks?|months?|years?|minutes?)\b", # 6 weeks
    ]),
    re.IGNORECASE,
)


# ---------------------------------------------------------------------------
# Fetch helpers (same dual Pyodide/CPython pattern as geo-crawlers /
# schema-generate). Kept self-contained so each skill is portable.
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
# Block extraction
# ---------------------------------------------------------------------------

def _is_inside(el, tag_names) -> bool:
    for parent in el.parents:
        if parent.name in tag_names:
            return True
    return False


def extract_blocks(html: str, url: str) -> dict:
    """Segment the page into content blocks at each H2/H3.

    For every block: heading, level, text, exact word count, and counts of
    paragraphs / lists / tables / statistic patterns. Intro content before the
    first H2/H3 becomes the "(intro)" block (omitted if empty).
    """
    soup = BeautifulSoup(html, "html.parser")
    for tag in soup(["script", "style", "noscript"]):
        tag.decompose()

    # Pick a content root: <main> > <article> > <body>.
    root = soup.find("main") or soup.find("article") or soup.find("body") or soup
    for tag in root(["nav", "header", "footer", "aside", "form"]):
        tag.decompose()

    title = ""
    if soup.title and soup.title.string:
        title = soup.title.string.strip()
    if not title:
        h1 = root.find("h1")
        if h1:
            title = h1.get_text(" ", strip=True)

    # Walk all structural elements in document order. Filter out nested
    # duplicates so a <p> inside a <li> isn't counted twice.
    skip_when_inside = ("ul", "ol", "table", "blockquote", "li", "td", "th")
    raw_elems = root.find_all(
        ["h2", "h3", "p", "ul", "ol", "table", "blockquote"])
    elems = [el for el in raw_elems if not _is_inside(el, skip_when_inside)]

    blocks: list[dict] = []
    current = _new_block("(intro)", 0)

    for el in elems:
        if el.name in ("h2", "h3"):
            _flush(current, blocks)
            current = _new_block(el.get_text(" ", strip=True),
                                 int(el.name[1]))
        elif el.name == "p":
            t = el.get_text(" ", strip=True)
            if t:
                current["text_parts"].append(t)
                current["paragraphs"] += 1
        elif el.name in ("ul", "ol"):
            items = [li.get_text(" ", strip=True)
                     for li in el.find_all("li", recursive=False)]
            txt = "\n".join(f"- {i}" for i in items if i)
            if txt:
                current["text_parts"].append(txt)
                current["lists"] += 1
        elif el.name == "table":
            rows: list[str] = []
            for tr in el.find_all("tr"):
                cells = [c.get_text(" ", strip=True)
                         for c in tr.find_all(["td", "th"])]
                if cells:
                    rows.append(" | ".join(cells))
            if rows:
                # Compact representation; the LLM doesn't need the full table.
                current["text_parts"].append(
                    "(table) " + " / ".join(rows[:6])
                    + (" …" if len(rows) > 6 else ""))
                current["tables"] += 1
        elif el.name == "blockquote":
            t = el.get_text(" ", strip=True)
            if t:
                current["text_parts"].append("> " + t)
                current["paragraphs"] += 1

    _flush(current, blocks)

    # Drop an empty intro placeholder.
    if blocks and blocks[0]["heading"] == "(intro)" and blocks[0]["word_count"] == 0:
        blocks = blocks[1:]

    total_words = sum(b["word_count"] for b in blocks)
    return {
        "url": url,
        "title": title,
        "total_words": total_words,
        "block_count": len(blocks),
        "blocks": blocks,
    }


def _new_block(heading: str, level: int) -> dict:
    return {"heading": heading, "level": level,
            "text_parts": [], "paragraphs": 0, "lists": 0, "tables": 0}


def _flush(b: dict, blocks: list[dict]) -> None:
    text = "\n\n".join(p for p in b["text_parts"] if p)
    words = len(re.findall(r"\b\w+\b", text))
    blocks.append({
        "heading": b["heading"],
        "level": b["level"],
        "text": text,
        "word_count": words,
        "paragraphs": b["paragraphs"],
        "lists": b["lists"],
        "tables": b["tables"],
        "statistics": len(STAT_RE.findall(text)),
        "first60": " ".join(text.split()[:60]),
    })


# ---------------------------------------------------------------------------
# Report rendering — what the LLM consumes from stdout
# ---------------------------------------------------------------------------

def render_extract(result: dict) -> str:
    title = result["title"] or "(no title)"
    L: list[str] = [
        f"# Citability extraction: {title}",
        "",
        f"**URL:** {result['url']}",
        f"**Total words:** {result['total_words']}  ·  "
        f"**Blocks:** {result['block_count']}",
        "",
        "Apply the 5-category citability rubric (in SKILL.md) to each block "
        "below. Word counts are exact — use them for the 134-167-words optimal "
        "passage check and statistic-density calibration.",
        "",
    ]
    if not result["blocks"]:
        L += [
            "**No content blocks extracted.** The page has no H2/H3 structure "
            "or is JavaScript-rendered. Do not score — tell the user the page "
            "needs structural headings or server-side rendering.",
            "",
        ]
        return "\n".join(L).rstrip() + "\n"

    for i, b in enumerate(result["blocks"], 1):
        L += [
            f"## Block {i} — {b['heading']!r} (H{b['level']})",
            "",
            f"- Words: **{b['word_count']}**  ·  Paragraphs: {b['paragraphs']}  ·  "
            f"Lists: {b['lists']}  ·  Tables: {b['tables']}  ·  "
            f"Statistics: {b['statistics']}",
            f"- First 60 words: {b['first60']}",
            "",
            "**Text:**",
            "",
        ]
        text = b["text"] or "(empty)"
        if len(text) > 2500:
            text = text[:2500] + "  …[truncated]"
        L += [text, ""]
    return "\n".join(L).rstrip() + "\n"


# ---------------------------------------------------------------------------
# Output paths
# ---------------------------------------------------------------------------

def resolve_output_paths(output: Path | None) -> tuple[Path, Path]:
    if output is None:
        base = DEFAULT_OUTPUT_DIR
        return base / "GEO-CITABILITY-EXTRACT.md", base / "GEO-CITABILITY-EXTRACT.json"
    if output.suffix == "" or output.is_dir():
        return output / "GEO-CITABILITY-EXTRACT.md", output / "GEO-CITABILITY-EXTRACT.json"
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

    result = extract_blocks(body, resp.get("final_url") or url)
    result["fetched_at"] = time.strftime("%Y-%m-%d %H:%M:%S")
    result["http_status"] = status

    report = render_extract(result)

    md_path, json_path = resolve_output_paths(args.output)
    md_path.parent.mkdir(parents=True, exist_ok=True)
    md_path.write_text(report, encoding="utf-8")
    json_path.write_text(json.dumps(result, indent=2, ensure_ascii=False) + "\n",
                         encoding="utf-8")

    # The structured breakdown goes to STDOUT — what run_skill_script returns
    # to the model. The LLM scores from this; it must never invent blocks or
    # word counts.
    print(report)

    print(f"[geo-citability] {result['block_count']} block(s), "
          f"{result['total_words']} words — wrote {md_path}", file=sys.stderr)
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Fetch a page and segment it into citability-scored blocks.")
    parser.add_argument("input", nargs="?",
                        help="Page URL (positional).")
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
