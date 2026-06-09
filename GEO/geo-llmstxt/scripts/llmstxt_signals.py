#!/usr/bin/env python3
"""
llmstxt_signals.py — fetch + parse /llms.txt and /llms-full.txt, validate
against the Jeremy-Howard spec, and gather generation context (homepage
signals + sitemap.xml URLs) for the geo-llmstxt skill.

The script handles both modes — analyze (file exists) and generate (file
absent) — in one pass: it fetches all four artifacts (llms.txt, llms-full.txt,
homepage, sitemap.xml) in parallel and emits a structured report. The LLM
decides which mode applies from the presence flags.

Pyodide-safe: routes fetches through /gpt/backend/api/v1/fetch-url under
Pyodide; uses urllib directly under native CPython.

Usage:
    python llmstxt_signals.py https://example.com
    python llmstxt_signals.py https://example.com/any/page   # site root derived
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
from urllib.parse import urlparse, urlunparse
from xml.etree import ElementTree

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

MAX_SITEMAP_URLS = 200  # cap for manageable stdout

# Entry line: "- [Title](URL): description"
ENTRY_RE = re.compile(r"^\s*-\s*\[([^\]]+)\]\(([^)]+)\)\s*(?::\s*(.*))?$")


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
        "Accept": "text/html,application/xhtml+xml,text/plain,text/markdown,"
                  "application/xml,*/*;q=0.8",
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


def _ok(resp: dict | None) -> bool:
    return bool(resp) and resp.get("status", 0) == 200 and bool((resp.get("body") or "").strip())


# ---------------------------------------------------------------------------
# llms.txt parsing
# ---------------------------------------------------------------------------

def parse_llmstxt(text: str) -> dict:
    """Parse a /llms.txt Markdown file per the spec.

    Returns:
      - title: H1 text (or None)
      - description: first blockquote line text (or None)
      - sections: [{name, entries: [{title, url, description, line}], line}]
      - key_facts: [str, ...] bullets under a "Key Facts" section
      - contact: [str, ...] bullets under a "Contact" section
      - line_count
      - warnings: list[str] structural issues
    """
    lines = text.splitlines()
    title: str | None = None
    description: str | None = None
    sections: list[dict] = []
    current_section: dict | None = None
    key_facts: list[str] = []
    contact: list[str] = []
    in_key_facts = False
    in_contact = False
    warnings: list[str] = []

    title_seen = False
    desc_expected = False

    for i, raw in enumerate(lines, 1):
        line = raw.rstrip()
        stripped = line.strip()
        if not stripped:
            desc_expected = title_seen and description is None
            continue

        # H1 title — the FIRST H1 wins; flag extras.
        if stripped.startswith("# ") and not stripped.startswith("## "):
            text_val = stripped[2:].strip()
            if title is None:
                title = text_val
                title_seen = True
                desc_expected = True
            else:
                warnings.append(f"line {i}: extra H1 found ({text_val!r}); "
                                 "spec allows only one")
            continue

        # H2 section header
        if stripped.startswith("## "):
            section_name = stripped[3:].strip()
            current_section = {"name": section_name, "entries": [], "line": i}
            sections.append(current_section)
            in_key_facts = (section_name.lower() == "key facts")
            in_contact = (section_name.lower() == "contact")
            continue

        # Blockquote (description, expected right after title)
        if stripped.startswith(">"):
            text_val = stripped[1:].strip()
            if description is None:
                description = text_val
                desc_expected = False
            else:
                warnings.append(f"line {i}: additional blockquote — only one "
                                 "description blockquote is canonical")
            continue

        # Entry / bullet within a section
        m = ENTRY_RE.match(line)
        if m and current_section is not None:
            entry_title, entry_url, entry_desc = m.group(1), m.group(2), (m.group(3) or "").strip()
            current_section["entries"].append({
                "title": entry_title.strip(),
                "url": entry_url.strip(),
                "description": entry_desc,
                "line": i,
            })
            continue

        # Plain bullets in Key Facts / Contact
        if stripped.startswith("- ") and (in_key_facts or in_contact):
            bullet = stripped[2:].strip()
            if in_key_facts:
                key_facts.append(bullet)
            else:
                contact.append(bullet)
            continue

    if title is None:
        warnings.append("missing H1 title on the first non-empty line")
    if description is None:
        warnings.append("missing blockquote description after the title")
    if not sections:
        warnings.append("no H2 section found — spec requires at least one")

    return {
        "title": title,
        "description": description,
        "description_length": len(description) if description else 0,
        "sections": sections,
        "section_count": len(sections),
        "total_entries": sum(len(s["entries"]) for s in sections),
        "key_facts": key_facts,
        "contact": contact,
        "line_count": len(lines),
        "warnings": warnings,
    }


def validate_entries(parsed: dict) -> dict:
    """Cross-cut validation of entries: absolute URLs, description presence,
    description length."""
    abs_url_re = re.compile(r"^https?://", re.I)
    issues = {
        "relative_or_malformed_urls": [],
        "missing_descriptions": [],
        "long_descriptions": [],     # > 50 words
        "short_descriptions": [],    # < 5 words
    }
    for section in parsed["sections"]:
        for e in section["entries"]:
            url = e["url"]
            if not abs_url_re.match(url):
                issues["relative_or_malformed_urls"].append(
                    f"line {e['line']}: {url!r} in section {section['name']!r}")
            desc = e["description"]
            words = len(re.findall(r"\b\w+\b", desc))
            if not desc:
                issues["missing_descriptions"].append(
                    f"line {e['line']}: {e['title']!r}")
            elif words > 50:
                issues["long_descriptions"].append(
                    f"line {e['line']}: {e['title']!r} ({words} words)")
            elif words < 5:
                issues["short_descriptions"].append(
                    f"line {e['line']}: {e['title']!r} ({words} words)")
    return issues


# ---------------------------------------------------------------------------
# Homepage signals + sitemap
# ---------------------------------------------------------------------------

def extract_homepage_signals(html: str) -> dict:
    soup = BeautifulSoup(html, "html.parser")
    for tag in soup(["script", "style", "noscript"]):
        tag.decompose()

    title = soup.title.string.strip() if soup.title and soup.title.string else None
    meta_desc = None
    md = soup.find("meta", attrs={"name": re.compile(r"^description$", re.I)})
    if md and md.get("content"):
        meta_desc = md["content"].strip()
    og_site_name = None
    osn = soup.find("meta", attrs={"property": "og:site_name"})
    if osn and osn.get("content"):
        og_site_name = osn["content"].strip()

    h1 = None
    h1_tag = soup.find("h1")
    if h1_tag:
        h1 = h1_tag.get_text(" ", strip=True)

    # First-level nav links — first <nav> or <ul role="navigation">.
    nav_links: list[dict] = []
    nav = soup.find("nav") or soup.find("ul", attrs={"role": "navigation"})
    if nav:
        for a in nav.find_all("a"):
            text = a.get_text(" ", strip=True)
            href = (a.get("href") or "").strip()
            if text and href and not href.startswith(("#", "mailto:", "tel:", "javascript:")):
                nav_links.append({"text": text, "href": href})
            if len(nav_links) >= 20:
                break

    return {
        "title": title,
        "og_site_name": og_site_name,
        "meta_description": meta_desc,
        "h1": h1,
        "nav_links": nav_links,
    }


def parse_sitemap_xml(text: str) -> list[str]:
    """Parse a sitemap.xml — accepts <urlset> (URL list) or <sitemapindex>
    (recursively listed sitemaps; we collect those locations too).

    Returns a flat list of URLs / sitemap locations, capped to MAX_SITEMAP_URLS.
    """
    out: list[str] = []
    if not text or not text.strip():
        return out
    try:
        root = ElementTree.fromstring(text)
    except ElementTree.ParseError:
        return out
    tag = root.tag.lower()
    # Strip namespace if present: "{http://...}urlset" -> "urlset"
    if "}" in tag:
        tag = tag.rsplit("}", 1)[-1]

    def _collect(elem, child_tag: str) -> None:
        for child in elem.iter():
            local = child.tag.rsplit("}", 1)[-1] if "}" in child.tag else child.tag
            if local == child_tag and child.text:
                out.append(child.text.strip())

    if tag == "urlset":
        _collect(root, "loc")
    elif tag == "sitemapindex":
        _collect(root, "loc")
    else:
        _collect(root, "loc")  # be permissive

    return out[:MAX_SITEMAP_URLS]


# ---------------------------------------------------------------------------
# Aggregation + rendering
# ---------------------------------------------------------------------------

def site_root(url: str) -> str:
    p = urlparse(url if "://" in url else "https://" + url)
    return urlunparse((p.scheme or "https", p.netloc, "", "", "", "")).rstrip("/")


def render(result: dict) -> str:
    L: list[str] = [
        f"# llms.txt analysis & generation context: {result['domain']}",
        "",
        f"**Site root:** {result['site_root']}",
        f"**Fetched:** {result['fetched_at']}",
        "",
        "## File presence",
        "",
        f"- /llms.txt: **{result['llms_txt']['status_label']}**",
        f"- /llms-full.txt: **{result['llms_full_txt']['status_label']}**",
        f"- /sitemap.xml: **{result['sitemap']['status_label']}** "
        f"({result['sitemap']['url_count']} URLs"
        + (" — capped" if result['sitemap']['capped'] else "") + ")",
        "",
    ]

    L += ["**Mode hint:** "]
    if result['llms_txt']['found']:
        L[-1] += ("ANALYZE — `/llms.txt` exists. Score Completeness / "
                   "Accuracy / Usefulness from the parsed structure below.")
    else:
        L[-1] += ("GENERATE — `/llms.txt` is absent. Build it from the homepage "
                   "signals + sitemap URLs below. Ask the user for Key Facts "
                   "you cannot derive from the homepage.")
    L.append("")

    # Parsed llms.txt section
    if result['llms_txt']['found']:
        p = result['llms_txt']['parsed']
        v = result['llms_txt']['validation']
        L += [
            "## /llms.txt — parsed structure",
            "",
            f"- Title (H1): {p['title'] or 'MISSING'}",
            f"- Description: {p['description'] or 'MISSING'}",
            f"  ({p['description_length']} chars; spec: under 200)",
            f"- Sections: **{p['section_count']}**",
            f"- Total entries: **{p['total_entries']}** (spec target 10-30)",
            f"- Line count: {p['line_count']} (spec target 30-200 lines for concise)",
            "",
        ]
        for s in p['sections']:
            L.append(f"### Section: {s['name']} ({len(s['entries'])} entries)")
            for e in s['entries'][:8]:
                desc_words = len(re.findall(r"\b\w+\b", e['description']))
                L.append(f"- [{e['title']}]({e['url']}) — "
                         f"{desc_words}-word desc: "
                         f"{e['description'][:160] or '(missing)'}")
            if len(s['entries']) > 8:
                L.append(f"- … {len(s['entries']) - 8} more entries")
            L.append("")
        if p['key_facts']:
            L += ["### Key Facts section", ""]
            L += [f"- {kf}" for kf in p['key_facts'][:10]]
            L.append("")
        if p['contact']:
            L += ["### Contact section", ""]
            L += [f"- {c}" for c in p['contact'][:10]]
            L.append("")

        # Validation
        L += ["## Validation issues", ""]
        if p['warnings']:
            for w in p['warnings']:
                L.append(f"- ⚠️ {w}")
        if v['relative_or_malformed_urls']:
            L.append(f"- ⚠️ {len(v['relative_or_malformed_urls'])} entry URL(s) "
                     "are relative or malformed (spec requires absolute https://):")
            for w in v['relative_or_malformed_urls'][:5]:
                L.append(f"  - {w}")
        if v['missing_descriptions']:
            L.append(f"- ⚠️ {len(v['missing_descriptions'])} entries missing a description:")
            for w in v['missing_descriptions'][:5]:
                L.append(f"  - {w}")
        if v['long_descriptions']:
            L.append(f"- ⚠️ {len(v['long_descriptions'])} descriptions exceed 50 words "
                     "(spec target 10-30):")
            for w in v['long_descriptions'][:5]:
                L.append(f"  - {w}")
        if v['short_descriptions']:
            L.append(f"- ⚠️ {len(v['short_descriptions'])} descriptions under 5 words "
                     "(too vague):")
            for w in v['short_descriptions'][:5]:
                L.append(f"  - {w}")
        if not (p['warnings'] or any(v.values())):
            L.append("- None — structurally clean.")
        L.append("")

        # Spec checklist
        L += ["## Spec checklist", ""]
        checks = [
            ("H1 title present", bool(p['title']), "Critical"),
            ("Blockquote description present", bool(p['description']), "High"),
            ("Description under 200 chars",
             0 < p['description_length'] <= 200, "Medium"),
            ("At least one H2 section", p['section_count'] >= 1, "Critical"),
            ("Total entries 10-30", 10 <= p['total_entries'] <= 30, "Medium"),
            ("Line count 30-200", 30 <= p['line_count'] <= 200, "Low"),
            ("Key Facts section present", bool(p['key_facts']), "Medium"),
            ("Contact section present", bool(p['contact']), "Low"),
            ("All URLs absolute", not v['relative_or_malformed_urls'], "High"),
            ("All entries have descriptions", not v['missing_descriptions'], "Medium"),
        ]
        L.append("| Check | Status | Severity |")
        L.append("|---|---|---|")
        for label, ok, sev in checks:
            L.append(f"| {label} | {'✅ pass' if ok else '❌ fail'} | {sev} |")
        L.append("")
    else:
        L += [
            "## /llms.txt — not found",
            "",
            "The file is absent. Switch to **GENERATE** mode using the homepage "
            "signals and sitemap URLs below.",
            "",
        ]

    # Homepage signals (always shown — useful for both modes)
    h = result['homepage']
    L += [
        "## Homepage signals (authoritative for site-name + description)",
        "",
        f"- `<title>`: {h.get('title') or 'MISSING'}",
        f"- `og:site_name`: {h.get('og_site_name') or '(not set)'}",
        f"- meta description: {h.get('meta_description') or 'MISSING'}",
        f"- First H1: {h.get('h1') or 'MISSING'}",
        f"- Nav links extracted ({len(h.get('nav_links', []))}):",
    ]
    for nl in h.get('nav_links', [])[:12]:
        L.append(f"  - [{nl['text']}]({nl['href']})")
    if len(h.get('nav_links', [])) > 12:
        L.append(f"  - … {len(h['nav_links']) - 12} more")
    L.append("")

    # Sitemap URLs (always shown — useful for both modes)
    sm = result['sitemap']
    L += [
        "## Sitemap URLs (page-selection pool for generation; "
        "completeness reference for analysis)",
        "",
        f"- Total in sitemap: **{sm['url_count']}**"
        + (" (capped — full sitemap is larger)" if sm['capped'] else ""),
        "",
    ]
    if sm['urls']:
        L.append("Sample (first 40):")
        for u in sm['urls'][:40]:
            L.append(f"- {u}")
        if len(sm['urls']) > 40:
            L.append(f"- … {len(sm['urls']) - 40} more")
    else:
        L.append("(no URLs parsed)")
    L.append("")

    return "\n".join(L).rstrip() + "\n"


# ---------------------------------------------------------------------------
# Output paths
# ---------------------------------------------------------------------------

def resolve_output_paths(output: Path | None) -> tuple[Path, Path]:
    if output is None:
        base = DEFAULT_OUTPUT_DIR
        return base / "GEO-LLMSTXT-EXTRACT.md", base / "GEO-LLMSTXT-EXTRACT.json"
    if output.suffix == "" or output.is_dir():
        return output / "GEO-LLMSTXT-EXTRACT.md", output / "GEO-LLMSTXT-EXTRACT.json"
    return output, output.with_suffix(".json")


# ---------------------------------------------------------------------------
# Main pipeline
# ---------------------------------------------------------------------------

async def amain(args) -> int:
    if not args.input:
        print("ERROR: a URL is required.", file=sys.stderr)
        return 2
    root = site_root(args.input)
    if not urlparse(root).netloc:
        print(f"ERROR: could not parse a host from {args.input!r}", file=sys.stderr)
        return 2
    domain = urlparse(root).netloc

    print(f"Auditing /llms.txt setup for {root}…", file=sys.stderr)

    llms_resp, llms_full_resp, home_resp, sitemap_resp = await asyncio.gather(
        fetch(f"{root}/llms.txt"),
        fetch(f"{root}/llms-full.txt"),
        fetch(root + "/"),
        fetch(f"{root}/sitemap.xml"),
    )

    def status_label(resp):
        if _ok(resp):
            return "found"
        if resp is None:
            return "fetch failed"
        return f"not found (HTTP {resp.get('status', 0)})"

    llms_found = _ok(llms_resp)
    llms_text = llms_resp.get("body", "") if llms_found else ""
    parsed = parse_llmstxt(llms_text) if llms_found else None
    validation = validate_entries(parsed) if parsed else None

    homepage = (extract_homepage_signals(home_resp["body"])
                if _ok(home_resp) else {})
    sitemap_urls = (parse_sitemap_xml(sitemap_resp["body"])
                    if _ok(sitemap_resp) else [])
    sitemap_capped = len(sitemap_urls) >= MAX_SITEMAP_URLS

    result = {
        "domain": domain,
        "site_root": root,
        "fetched_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "llms_txt": {
            "found": llms_found,
            "status_label": status_label(llms_resp),
            "parsed": parsed,
            "validation": validation,
            "raw": llms_text[:6000],  # cap raw content in the JSON
        },
        "llms_full_txt": {
            "found": _ok(llms_full_resp),
            "status_label": status_label(llms_full_resp),
            "line_count": (len(llms_full_resp["body"].splitlines())
                           if _ok(llms_full_resp) else 0),
        },
        "homepage": homepage,
        "sitemap": {
            "status_label": status_label(sitemap_resp),
            "url_count": len(sitemap_urls),
            "capped": sitemap_capped,
            "urls": sitemap_urls,
        },
    }

    report = render(result)
    md_path, json_path = resolve_output_paths(args.output)
    md_path.parent.mkdir(parents=True, exist_ok=True)
    md_path.write_text(report, encoding="utf-8")
    json_path.write_text(json.dumps(result, indent=2, ensure_ascii=False) + "\n",
                         encoding="utf-8")

    # Full extraction to stdout — what run_skill_script returns to the model.
    print(report)

    mode = "ANALYZE" if llms_found else "GENERATE"
    print(f"[geo-llmstxt] mode={mode} "
          f"sections={(parsed or {}).get('section_count', 0)} "
          f"entries={(parsed or {}).get('total_entries', 0)} "
          f"sitemap={len(sitemap_urls)} — wrote {md_path}", file=sys.stderr)
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="llms.txt analysis + generation context.")
    parser.add_argument("input", nargs="?",
                        help="Site URL (positional). Root is derived.")
    parser.add_argument("--url", dest="url_alias", default=None,
                        help="Alias for the positional URL argument.")
    parser.add_argument("--source", default=None,
                        help="Source URL label when the runner pre-fetches "
                             "a local file.")
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
