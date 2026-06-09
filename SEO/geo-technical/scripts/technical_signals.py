#!/usr/bin/env python3
"""
technical_signals.py — technical-SEO signal extraction for the geo-technical
skill.

Fetches a page, its robots.txt and sitemap, extracts SSR / canonical / hreflang
/ viewport / URL-structure / JSON-LD signals, and prints a structured report
to stdout. The LLM scores the 8-category technical rubric from the result.

What this script DOES NOT do (these need external tools):
- Core Web Vitals (LCP / INP / CLS) — needs PageSpeed Insights / CrUX
- TTFB, page weight, image-optimization, JS-bundle, CDN — needs PSI / Lighthouse
- Response-header inspection (HSTS, CSP, X-Frame-Options, etc.) — the Pyodide
  fetch proxy does not return response headers

Pyodide-safe: routes fetches through /gpt/backend/api/v1/fetch-url when
running under Pyodide; uses urllib directly under native CPython.

Usage:
    python technical_signals.py https://example.com/blog/my-post
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

# Quick AI-crawler subset for the brief robots.txt check (the full 14-crawler
# audit lives in geo-crawlers).
KEY_AI_CRAWLERS = {"gptbot", "oai-searchbot", "chatgpt-user", "claudebot",
                   "perplexitybot", "google-extended", "googlebot"}


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


def _ok(resp: dict | None) -> bool:
    return bool(resp) and resp.get("status", 0) == 200 and bool((resp.get("body") or "").strip())


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def normalize_url(u: str) -> str:
    """Normalize URL for self-ref canonical comparison."""
    if not u:
        return ""
    p = urlparse(u)
    path = (p.path or "/").rstrip("/") or "/"
    return f"{p.scheme.lower()}://{p.netloc.lower()}{path.lower()}"


def site_root(url: str) -> str:
    p = urlparse(url if "://" in url else "https://" + url)
    return urlunparse((p.scheme or "https", p.netloc, "", "", "", "")).rstrip("/")


def analyze_url_structure(url: str) -> dict:
    p = urlparse(url)
    path = p.path or "/"
    segments = [s for s in path.split("/") if s]
    return {
        "scheme": p.scheme,
        "https": p.scheme == "https",
        "path": path,
        "path_depth": len(segments),
        "has_underscores": "_" in path,
        "has_mixed_case": path != path.lower(),
        "has_query_params": bool(p.query),
        "query": p.query,
        "has_session_id": bool(
            re.search(r"\b(sid|sessionid|sess|jsessionid|phpsessid)=", url, re.I)),
    }


def parse_robots_brief(text: str) -> dict:
    """Light robots.txt parse — sitemaps + key AI crawlers fully blocked.

    For the full 14-crawler audit, call the geo-crawlers skill.
    """
    sitemaps: list[str] = []
    blocked: list[str] = []
    current: list[str] = []
    expecting = False
    for raw in text.splitlines():
        line = raw.split("#", 1)[0].strip()
        if not line or ":" not in line:
            continue
        field, _, value = line.partition(":")
        field, value = field.strip().lower(), value.strip()
        if field == "user-agent":
            if not expecting:
                current = []
                expecting = True
            current.append(value.lower())
        elif field == "disallow":
            expecting = False
            if value.strip() == "/":
                for a in current:
                    if a in KEY_AI_CRAWLERS and a not in blocked:
                        blocked.append(a)
        elif field == "allow":
            expecting = False
        elif field == "sitemap":
            sitemaps.append(value)
    return {"sitemaps": sitemaps, "ai_crawlers_blocked": blocked}


# ---------------------------------------------------------------------------
# Page-level extraction
# ---------------------------------------------------------------------------

def _is_inside(el, tag_names) -> bool:
    for parent in el.parents:
        if parent.name in tag_names:
            return True
    return False


def extract_signals(html: str, url: str) -> dict:
    """All signals derivable from one page fetch."""
    full = BeautifulSoup(html, "html.parser")
    # Separate soup for main-content / SSR signals so we can strip chrome.
    content_soup = BeautifulSoup(html, "html.parser")
    for tag in content_soup(["script", "style", "noscript"]):
        tag.decompose()
    root = (content_soup.find("main") or content_soup.find("article")
            or content_soup.find("body") or content_soup)
    # Capture H1 before stripping <header> (themes that put title H1 there).
    preserved_h1s = [h.get_text(" ", strip=True)
                     for h in root.find_all("h1") if h.get_text(strip=True)]
    for tag in root.find_all(["nav", "header", "footer", "aside", "form"]):
        tag.decompose()

    # SSR signals from main content
    paragraphs = [p for p in root.find_all("p")
                  if not _is_inside(p, ("li", "td", "th"))]
    body_text = "\n".join(p.get_text(" ", strip=True) for p in paragraphs
                          if p.get_text(strip=True))
    main_word_count = len(re.findall(r"\b\w+\b", body_text))
    h1_count = len(preserved_h1s) + sum(
        1 for h in root.find_all("h1")
        if h.get_text(" ", strip=True)
        and h.get_text(" ", strip=True) not in preserved_h1s)
    h2_count = sum(1 for _ in root.find_all("h2"))
    h3_count = sum(1 for _ in root.find_all("h3"))
    paragraph_count = len(paragraphs)
    internal_link_count = 0
    page_host = urlparse(url).netloc
    for a in root.find_all("a"):
        href = (a.get("href") or "").strip()
        if not href or href.startswith(("#", "mailto:", "tel:", "javascript:")):
            continue
        host = urlparse(href).netloc
        if not host or host == page_host:
            internal_link_count += 1

    has_jsonld = bool(full.find("script", type="application/ld+json"))
    if main_word_count >= 500 and h2_count >= 2 and paragraph_count >= 5:
        ssr_verdict = "strong"
    elif main_word_count >= 100 and (h1_count + h2_count + h3_count >= 1
                                       or paragraph_count >= 3):
        ssr_verdict = "partial"
    else:
        ssr_verdict = "weak"

    # Meta + structured data
    title = (full.title.string.strip()
             if full.title and full.title.string else None)
    meta_desc = None
    md = full.find("meta", attrs={"name": re.compile(r"^description$", re.I)})
    if md and md.get("content"):
        meta_desc = md["content"].strip()

    canonical_href = None
    canonical_self_ref = None
    canonical = full.find("link", rel="canonical")
    if canonical and canonical.get("href"):
        canonical_href = canonical["href"].strip()
        canonical_self_ref = (normalize_url(canonical_href) == normalize_url(url))

    viewport = None
    vp = full.find("meta", attrs={"name": re.compile(r"^viewport$", re.I)})
    if vp and vp.get("content"):
        viewport = vp["content"].strip()

    robots_meta = None
    rm = full.find("meta", attrs={"name": re.compile(r"^robots$", re.I)})
    if rm and rm.get("content"):
        robots_meta = rm["content"].strip()
    noindex = bool(robots_meta) and "noindex" in robots_meta.lower()

    # OG / Twitter — presence + values
    og = {}
    for prop in ("og:title", "og:description", "og:image", "og:type", "og:url"):
        m = full.find("meta", attrs={"property": prop})
        if m and m.get("content"):
            og[prop] = m["content"].strip()
    twitter = {}
    for name in ("twitter:card", "twitter:title", "twitter:description", "twitter:image"):
        m = full.find("meta", attrs={"name": name})
        if m and m.get("content"):
            twitter[name] = m["content"].strip()

    # hreflang
    hreflangs: list[dict] = []
    x_default = False
    for link in full.find_all("link", rel="alternate"):
        lang = link.get("hreflang")
        href = link.get("href")
        if lang and href:
            hreflangs.append({"lang": lang, "href": href})
            if lang.lower() == "x-default":
                x_default = True

    # Pagination
    rel_next = full.find("link", rel="next")
    rel_prev = full.find("link", rel="prev")
    pagination = {
        "next": rel_next.get("href") if rel_next and rel_next.get("href") else None,
        "prev": rel_prev.get("href") if rel_prev and rel_prev.get("href") else None,
    }

    # JSON-LD types
    jsonld_types: list[str] = []
    for script in full.find_all("script", type="application/ld+json"):
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
                    jsonld_types.extend(str(x) for x in t)
                else:
                    jsonld_types.append(str(t))
            g = it.get("@graph")
            if isinstance(g, list):
                stack.extend(x for x in g if isinstance(x, dict))
    # dedup
    seen = set()
    jsonld_types = [t for t in jsonld_types if not (t in seen or seen.add(t))]

    return {
        "url": url,
        "title": title,
        "meta_description": meta_desc,
        "canonical": {
            "href": canonical_href,
            "self_referencing": canonical_self_ref,
        },
        "robots_meta": robots_meta,
        "noindex": noindex,
        "viewport": viewport,
        "og": og,
        "twitter": twitter,
        "hreflang": {
            "tags": hreflangs,
            "count": len(hreflangs),
            "x_default": x_default,
        },
        "pagination": pagination,
        "jsonld_types": jsonld_types,
        "has_jsonld": has_jsonld,
        "ssr": {
            "verdict": ssr_verdict,
            "main_word_count": main_word_count,
            "h1_count": h1_count,
            "h2_count": h2_count,
            "h3_count": h3_count,
            "paragraph_count": paragraph_count,
            "internal_link_count_in_main": internal_link_count,
        },
        "url_structure": analyze_url_structure(url),
        "raw_text_sample": body_text[:1200],
    }


# ---------------------------------------------------------------------------
# Report rendering
# ---------------------------------------------------------------------------

def render(result: dict) -> str:
    s = result["signals"]
    L = [
        f"# Technical SEO signals: {s['title'] or '(no title)'}",
        "",
        f"**URL:** {s['url']}",
        f"**Fetched:** {result['fetched_at']}",
        "",
        "## Server-side rendering (Category 7 — CRITICAL for GEO)",
        "",
        f"- Verdict: **{s['ssr']['verdict'].upper()}**",
        f"- Main-content words in raw HTML: {s['ssr']['main_word_count']}",
        f"- Headings in raw HTML: H1={s['ssr']['h1_count']}, "
        f"H2={s['ssr']['h2_count']}, H3={s['ssr']['h3_count']}",
        f"- Paragraphs in raw HTML: {s['ssr']['paragraph_count']}",
        f"- Internal links in raw HTML: {s['ssr']['internal_link_count_in_main']}",
        f"- JSON-LD in raw HTML: {'yes' if s['has_jsonld'] else 'no'}",
        "",
    ]
    if s['ssr']['verdict'] == 'weak':
        L += ["⚠️ Likely client-rendered (SPA without SSR). AI crawlers do not "
              "execute JavaScript — content invisible to them. Recommend an SSR "
              "framework (Next.js / Nuxt / SvelteKit / Angular Universal / "
              "Astro / Remix) or a prerendering service (Prerender.io).", ""]

    L += [
        "## Indexability (Category 2)",
        "",
        f"- Canonical: "
        f"{('present -> ' + s['canonical']['href']) if s['canonical']['href'] else 'NOT SET'}",
        f"- Self-referencing: "
        f"{'yes' if s['canonical']['self_referencing'] else 'no'}",
        f"- robots meta: {s['robots_meta'] or 'not set'}",
        f"- noindex: {'YES (page will not be indexed)' if s['noindex'] else 'no'}",
        f"- hreflang tags: {s['hreflang']['count']} "
        + (f"(x-default: {'present' if s['hreflang']['x_default'] else 'MISSING'})"
           if s['hreflang']['count'] > 0 else "(none)"),
        f"- Pagination rel=next: {s['pagination']['next'] or '-'}  ·  "
        f"rel=prev: {s['pagination']['prev'] or '-'}",
        "",
        "## Mobile (Category 5 — partial; viewport only)",
        "",
        f"- viewport meta: {s['viewport'] or 'NOT SET (mobile rendering will break)'}",
        "",
        "## Security (Category 3 — partial; HTTPS only)",
        "",
        f"- HTTPS: {'yes' if s['url_structure']['https'] else 'NO (fatal)'}",
        "- Response-header audit (HSTS / CSP / X-Frame-Options / Referrer-Policy / "
        "X-Content-Type-Options) — **needs separate header check**, not returned "
        "by the Pyodide proxy.",
        "",
        "## URL Structure (Category 4)",
        "",
        f"- Path: {s['url_structure']['path']}",
        f"- Depth: {s['url_structure']['path_depth']} segment(s)",
        f"- Mixed case: {'YES (issue)' if s['url_structure']['has_mixed_case'] else 'no'}",
        f"- Underscores: {'YES (use hyphens)' if s['url_structure']['has_underscores'] else 'no'}",
        f"- Query params: "
        + (f"YES ({s['url_structure']['query']})"
           if s['url_structure']['has_query_params'] else "no"),
        f"- Session id in URL: {'YES (issue)' if s['url_structure']['has_session_id'] else 'no'}",
        "",
        "## Metadata",
        "",
        f"- Title: {s['title'] or 'MISSING'}",
        f"- Meta description: {(s['meta_description'] or 'MISSING')[:140]}",
        f"- og:title: {s['og'].get('og:title', '-')}",
        f"- og:image: {s['og'].get('og:image', '-')}",
        f"- og:type: {s['og'].get('og:type', '-')}",
        f"- twitter:card: {s['twitter'].get('twitter:card', '-')}",
        f"- JSON-LD @types: {s['jsonld_types'] if s['jsonld_types'] else 'none'}",
        "",
    ]

    r = result["robots"]
    L += [
        "## Crawlability (Category 1)",
        "",
        f"- robots.txt: {r['status_label']}",
    ]
    if r["sitemaps"]:
        L.append(f"- Sitemap directives in robots.txt: {len(r['sitemaps'])}")
        for sm in r["sitemaps"][:5]:
            L.append(f"  - {sm}")
        if len(r["sitemaps"]) > 5:
            L.append(f"  - … {len(r['sitemaps']) - 5} more")
    else:
        L.append("- Sitemap directives in robots.txt: none")
    if r["ai_crawlers_blocked"]:
        L.append(f"- AI crawlers fully blocked (sample): "
                 f"{', '.join(r['ai_crawlers_blocked'])} — "
                 f"call **geo-crawlers** for the full 14-crawler audit.")
    else:
        L.append("- No key AI crawler is fully blocked "
                 "(GPTBot / ClaudeBot / PerplexityBot / Google-Extended / "
                 "Googlebot). Call **geo-crawlers** for the full audit.")
    L += [
        f"- /sitemap.xml: {result['sitemap_xml']['status_label']}",
        "",
        "## Core Web Vitals (Category 6) — N/A",
        "",
        "Not measured by this script. Run a PageSpeed Insights / CrUX field-data "
        "audit for LCP / INP / CLS. Score this category `N/A` and recommend a "
        "PSI run.",
        "",
        "## Page Speed & Server (Category 8) — N/A",
        "",
        "Not measured by this script (page-weight, TTFB, image optimization, "
        "JS bundle size, CDN detection need full-resource crawling). Score "
        "`N/A` and recommend a Lighthouse / PSI audit.",
        "",
        "## Raw text sample (for SSR verification)",
        "",
    ]
    sample = s["raw_text_sample"] or "(no main-content text in raw HTML)"
    L += [sample, ""]
    return "\n".join(L).rstrip() + "\n"


# ---------------------------------------------------------------------------
# Output paths
# ---------------------------------------------------------------------------

def resolve_output_paths(output: Path | None) -> tuple[Path, Path]:
    if output is None:
        base = DEFAULT_OUTPUT_DIR
        return base / "GEO-TECHNICAL-EXTRACT.md", base / "GEO-TECHNICAL-EXTRACT.json"
    if output.suffix == "" or output.is_dir():
        return output / "GEO-TECHNICAL-EXTRACT.md", output / "GEO-TECHNICAL-EXTRACT.json"
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

    root_url = site_root(url)
    print(f"Auditing {url}…", file=sys.stderr)

    page, robots_resp, sitemap = await asyncio.gather(
        fetch(url),
        fetch(f"{root_url}/robots.txt"),
        fetch(f"{root_url}/sitemap.xml"),
    )

    if not page or not page.get("body"):
        print(f"ERROR: page fetch failed for {url} (HTTP "
              f"{page.get('status', 0) if page else 'no response'})",
              file=sys.stderr)
        return 1

    signals = extract_signals(page["body"], page.get("final_url") or url)

    robots_text = robots_resp.get("body", "") if _ok(robots_resp) else ""
    robots_info = parse_robots_brief(robots_text)
    if _ok(robots_resp):
        robots_status = "found"
    elif robots_resp is None:
        robots_status = "fetch failed"
    else:
        robots_status = f"not found (HTTP {robots_resp.get('status', 0)})"

    if _ok(sitemap):
        sitemap_status = "found"
    elif sitemap is None:
        sitemap_status = "fetch failed"
    else:
        sitemap_status = f"not found (HTTP {sitemap.get('status', 0)})"

    result = {
        "fetched_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "signals": signals,
        "robots": {
            "status_label": robots_status,
            "sitemaps": robots_info["sitemaps"],
            "ai_crawlers_blocked": robots_info["ai_crawlers_blocked"],
        },
        "sitemap_xml": {"status_label": sitemap_status},
    }

    report = render(result)
    md_path, json_path = resolve_output_paths(args.output)
    md_path.parent.mkdir(parents=True, exist_ok=True)
    md_path.write_text(report, encoding="utf-8")
    json_path.write_text(json.dumps(result, indent=2, ensure_ascii=False) + "\n",
                         encoding="utf-8")

    # Full extraction to stdout — what run_skill_script returns to the model.
    print(report)

    print(f"[geo-technical] ssr={signals['ssr']['verdict']} "
          f"words={signals['ssr']['main_word_count']} "
          f"canonical={'yes' if signals['canonical']['href'] else 'no'} "
          f"https={signals['url_structure']['https']} — wrote {md_path}",
          file=sys.stderr)
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Technical SEO signal extraction with GEO emphasis.")
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
