#!/usr/bin/env python3
"""
audit.py — Sitemap analysis and generation.

Two purposes:
  1. ANALYZE an existing XML sitemap (or sitemap index). Discovers sitemaps
     via /sitemap.xml, /sitemap_index.xml, and robots.txt "Sitemap:" directives.
     Validates XML, 50k URL cap, lastmod realism, deprecated <priority>/<changefreq>
     tags, and samples URLs to verify HTTP 200 / non-noindex / canonical / non-3xx.

  2. GENERATE a new sitemap. Three sub-modes:
       --generate                # BFS-crawl internal links from a seed URL
       --from-list FILE          # read URLs from a text file
       --hreflang                # multi-language sitemap with xhtml:link variants

Pyodide-safe: async fetches route through /gpt/backend/api/v1/fetch-url
(same pattern as serp-shape and ai-search-audit's external pass).

Usage:
    python audit.py URL_OR_SITEMAP                    # analyze
    python audit.py URL --generate [--max-pages N]    # crawl + generate
    python audit.py --from-list urls.txt --generate   # generate from URL list
    python audit.py URL --generate --hreflang         # multi-language sitemap
"""

from __future__ import annotations

import os
import argparse
import asyncio
import json
import re
import sys
import time
import xml.etree.ElementTree as ET
from collections import Counter, defaultdict, deque
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional
from urllib.parse import urlparse, urljoin, urldefrag

from bs4 import BeautifulSoup


# Pyodide mounts the host's outputs/ folder at /outputs (see
# pyodide-runner.js::ensureOutputsMounted). Writing anywhere else under
# /home/pyodide/ lands in MEMFS, which evaporates at script exit and is
# invisible to the host. For skills with fetches_urls=true the bridge
# injects `-o /outputs/<stem>-audit.md` automatically; this skill has
# fetches_urls=false so we have to route the default ourselves.
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

# Default data-* attributes the crawler treats as link sources, in addition
# to plain <a href>. Many JavaScript-driven sites store navigation targets
# in attributes like data-article="articles/foo.html" — the link is real,
# but only resolves when JS runs. Without checking these, the BFS misses
# every page the user wants in their sitemap.
DEFAULT_DATA_ATTRS = (
    "data-href", "data-url", "data-link",
    "data-article", "data-page", "data-route",
    "data-target",
)

# Common file extensions to skip during crawls (non-HTML resources)
_SKIP_EXT = {
    ".jpg", ".jpeg", ".png", ".gif", ".webp", ".svg", ".ico", ".bmp",
    ".pdf", ".zip", ".tar", ".gz", ".rar", ".7z", ".dmg", ".exe",
    ".mp4", ".mov", ".avi", ".webm", ".mp3", ".wav", ".ogg", ".flac",
    ".css", ".js", ".json", ".xml", ".rss", ".atom",
    ".doc", ".docx", ".xls", ".xlsx", ".ppt", ".pptx",
    ".woff", ".woff2", ".ttf", ".eot",
}

# Location-page URL patterns — used by the quality-gate detector.
_LOCATION_PATH_RE = re.compile(
    r"/(?:locations?|cities|service-areas?|near-me|areas-served|st|state|region)/[^/]+/?",
    re.I,
)

# Sitemap protocol limits (RFC + Google)
MAX_URLS_PER_SITEMAP = 50_000
MAX_SITEMAP_BYTES = 50 * 1024 * 1024  # 50 MB uncompressed

# Sitemap XML namespaces
NS_SM = "http://www.sitemaps.org/schemas/sitemap/0.9"
NS_XHTML = "http://www.w3.org/1999/xhtml"


# ---------------------------------------------------------------------------
# Findings (analyze mode)
# ---------------------------------------------------------------------------

SEVERITIES = ("critical", "recommended", "nice-to-have", "info")
SEVERITY_WEIGHT = {"critical": 10, "recommended": 3, "nice-to-have": 1, "info": 0}


@dataclass
class Finding:
    severity: str
    title: str
    detail: str
    why: str
    fix: str


@dataclass
class AnalyzeResult:
    findings: list[Finding] = field(default_factory=list)
    strengths: list[str] = field(default_factory=list)
    stats: dict = field(default_factory=dict)
    urls_by_sitemap: dict[str, list[dict]] = field(default_factory=dict)

    def add(self, f: Finding) -> None:
        self.findings.append(f)

    def score(self) -> int:
        deduction = sum(SEVERITY_WEIGHT[f.severity] for f in self.findings)
        return max(0, 100 - deduction)


# ---------------------------------------------------------------------------
# HTTP / fetch helpers
# ---------------------------------------------------------------------------

def _is_pyodide() -> bool:
    return sys.platform == "emscripten"


async def _fetch_extra_pyodide(url: str, want_headers: bool = False) -> Optional[dict]:
    """Pyodide path: POST {url, method?: 'HEAD'} to the host fetch-url proxy.

    Returns {"body": str, "headers": dict, "status": int, "final_url": str}
    or None on failure. final_url is the URL after redirects.
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
        "final_url": data.get("final_url", "") or url,
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
            final_url = r.url
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
    return {"body": body_text, "headers": response_headers, "status": status, "final_url": final_url}


async def fetch(url: str, want_headers: bool = False) -> Optional[dict]:
    """Unified fetch — picks Pyodide or CPython transport per runtime."""
    if _is_pyodide():
        return await _fetch_extra_pyodide(url, want_headers=want_headers)
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(None, _fetch_extra_urllib, url, want_headers)


# ---------------------------------------------------------------------------
# Sitemap discovery + parsing
# ---------------------------------------------------------------------------

async def discover_sitemaps(base_url: str) -> list[str]:
    """Find sitemap URLs for a site.

    Sources, in order:
      1. /sitemap.xml
      2. /sitemap_index.xml
      3. robots.txt 'Sitemap:' directives

    Returns deduplicated list of sitemap URLs. Empty list if none found.
    """
    parsed = urlparse(base_url)
    base = f"{parsed.scheme}://{parsed.netloc}"
    candidates = []

    # Try the two canonical default locations
    for path in ("/sitemap.xml", "/sitemap_index.xml"):
        result = await fetch(f"{base}{path}")
        if result and result.get("status", 0) in (200, 0) and result.get("body"):
            body = result["body"]
            if body.lstrip().startswith("<"):
                candidates.append(f"{base}{path}")

    # Parse robots.txt for Sitemap: directives
    robots_result = await fetch(f"{base}/robots.txt")
    if robots_result and robots_result.get("body"):
        for line in robots_result["body"].splitlines():
            line = line.split("#", 1)[0].strip()
            if line.lower().startswith("sitemap:"):
                url = line.split(":", 1)[1].strip()
                if url and url not in candidates:
                    candidates.append(url)

    return candidates


@dataclass
class SitemapEntry:
    loc: str
    lastmod: Optional[str] = None
    priority: Optional[str] = None
    changefreq: Optional[str] = None
    hreflang: list[dict] = field(default_factory=list)  # [{lang, href}, ...]


@dataclass
class ParsedSitemap:
    source: str               # the URL the XML came from
    kind: str                 # 'urlset' or 'sitemapindex'
    entries: list[SitemapEntry] = field(default_factory=list)
    sub_sitemaps: list[str] = field(default_factory=list)  # for sitemapindex
    has_priority: bool = False
    has_changefreq: bool = False
    parse_error: Optional[str] = None


def _strip_ns(tag: str) -> str:
    """Strip XML namespace from an element tag."""
    return tag.split("}", 1)[1] if "}" in tag else tag


def parse_sitemap_xml(xml_text: str, source: str) -> ParsedSitemap:
    """Parse a sitemap.xml or sitemap_index.xml into a ParsedSitemap."""
    result = ParsedSitemap(source=source, kind="urlset")
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError as e:
        result.parse_error = str(e)
        return result

    root_tag = _strip_ns(root.tag).lower()
    if root_tag == "sitemapindex":
        result.kind = "sitemapindex"
        for sitemap_el in root:
            if _strip_ns(sitemap_el.tag).lower() != "sitemap":
                continue
            for child in sitemap_el:
                if _strip_ns(child.tag).lower() == "loc" and (child.text or "").strip():
                    result.sub_sitemaps.append(child.text.strip())
        return result

    if root_tag != "urlset":
        result.parse_error = f"Unexpected root element <{root_tag}>; expected <urlset> or <sitemapindex>."
        return result

    for url_el in root:
        if _strip_ns(url_el.tag).lower() != "url":
            continue
        entry = SitemapEntry(loc="")
        for child in url_el:
            t = _strip_ns(child.tag).lower()
            val = (child.text or "").strip()
            if t == "loc":
                entry.loc = val
            elif t == "lastmod":
                entry.lastmod = val
            elif t == "priority":
                entry.priority = val
                result.has_priority = True
            elif t == "changefreq":
                entry.changefreq = val
                result.has_changefreq = True
            elif t == "link":
                # xhtml:link rel="alternate" hreflang="..." href="..."
                rel = child.get("rel", "")
                hreflang = child.get("hreflang", "")
                href = child.get("href", "")
                if rel == "alternate" and hreflang and href:
                    entry.hreflang.append({"lang": hreflang, "href": href})
        if entry.loc:
            result.entries.append(entry)

    return result


async def fetch_and_parse_sitemap(url: str) -> ParsedSitemap:
    """Fetch a sitemap URL and parse it. Handles gzip if Content-Encoding hints."""
    result = await fetch(url)
    if not result or not result.get("body"):
        ps = ParsedSitemap(source=url, kind="urlset")
        ps.parse_error = "Could not fetch sitemap (no body returned)"
        return ps
    return parse_sitemap_xml(result["body"], url)


# ---------------------------------------------------------------------------
# Analyze mode
# ---------------------------------------------------------------------------

async def analyze(base_url: str, verify_sample: int = 50) -> AnalyzeResult:
    """Run the full analyze pass against base_url.

    Discovers sitemaps, parses each (recursing into sitemap indexes),
    runs validation checks, samples URLs for HTTP / canonical / noindex
    verification, and returns an AnalyzeResult.
    """
    result = AnalyzeResult()
    base_parsed = urlparse(base_url)
    base = f"{base_parsed.scheme}://{base_parsed.netloc}"
    result.stats["base"] = base

    # If the user gave us a direct sitemap URL (path ends .xml or contains 'sitemap'),
    # use that as the starting point. Otherwise discover.
    if base_url.lower().endswith(".xml") or "sitemap" in base_parsed.path.lower():
        seed_sitemaps = [base_url]
    else:
        seed_sitemaps = await discover_sitemaps(base)

    if not seed_sitemaps:
        result.add(Finding("critical",
            "No sitemap found",
            f"No /sitemap.xml, /sitemap_index.xml, or robots.txt 'Sitemap:' directive at {base}.",
            "Without a sitemap, search engines and AI crawlers fall back to link discovery via internal links. Orphan pages and large sites are systematically under-discovered.",
            "Generate a sitemap with: `python audit.py {url} --generate --max-pages 500` and submit it via robots.txt 'Sitemap:' or Google Search Console.".format(url=base),
        ))
        return result

    result.stats["sitemaps_discovered"] = seed_sitemaps

    # Walk all sitemaps, recursing into indexes (cap recursion at depth 3).
    all_entries: list[SitemapEntry] = []
    visited_sitemaps: set[str] = set()
    sitemap_files: list[ParsedSitemap] = []

    async def walk(sm_url: str, depth: int = 0) -> None:
        if depth > 3 or sm_url in visited_sitemaps:
            return
        visited_sitemaps.add(sm_url)
        parsed = await fetch_and_parse_sitemap(sm_url)
        sitemap_files.append(parsed)
        if parsed.parse_error:
            result.add(Finding("critical",
                f"Cannot parse sitemap at {sm_url}",
                f"XML parse error: {parsed.parse_error}",
                "Malformed sitemaps are silently dropped by Google. None of the URLs in the file are submitted for indexing.",
                "Validate the XML at https://www.xml-sitemaps.com/validate-xml-sitemap.html. Common issues: trailing commas inside CDATA, unescaped ampersands in URLs, BOM bytes at file start.",
            ))
            return
        if parsed.kind == "sitemapindex":
            for sub in parsed.sub_sitemaps:
                await walk(sub, depth + 1)
        else:
            all_entries.extend(parsed.entries)

    for sm in seed_sitemaps:
        await walk(sm)

    result.stats["sitemap_files"] = len(sitemap_files)
    result.stats["total_urls"] = len(all_entries)

    # Validation checks
    _check_size_cap(sitemap_files, result)
    _check_deprecated_tags(sitemap_files, result)
    _check_lastmod_realism(all_entries, result)
    _check_protocol_consistency(all_entries, base, result)
    _check_location_pages(all_entries, result)

    # Sample-based URL verification
    if all_entries:
        await _verify_url_sample(all_entries, verify_sample, result)

    # Store the URL list grouped by sitemap (for the structure report)
    for ps in sitemap_files:
        if ps.kind == "urlset":
            result.urls_by_sitemap[ps.source] = [
                {"loc": e.loc, "lastmod": e.lastmod} for e in ps.entries
            ]

    if not result.findings:
        result.strengths.append(f"Sitemap is well-formed across {len(sitemap_files)} file(s), {len(all_entries)} URL(s).")

    return result


def _check_size_cap(sitemap_files: list[ParsedSitemap], result: AnalyzeResult) -> None:
    """Flag any single sitemap file exceeding the 50k URL protocol cap."""
    over = [ps for ps in sitemap_files if ps.kind == "urlset" and len(ps.entries) > MAX_URLS_PER_SITEMAP]
    for ps in over:
        result.add(Finding("critical",
            f"Sitemap {ps.source} exceeds 50,000 URLs ({len(ps.entries)})",
            f"This sitemap file contains {len(ps.entries)} URLs; protocol limit is 50,000.",
            "Google ignores sitemap files over the protocol cap. Every URL in the file is invisible to indexing.",
            "Split into multiple sub-sitemaps and reference them from a sitemapindex file. Use this script's --generate mode to auto-split: `python audit.py {url} --generate`.".format(url=urlparse(ps.source).scheme + "://" + urlparse(ps.source).netloc),
        ))


def _check_deprecated_tags(sitemap_files: list[ParsedSitemap], result: AnalyzeResult) -> None:
    """Info-level flag for <priority> and <changefreq> — Google has ignored both since 2017."""
    has_prio = any(ps.has_priority for ps in sitemap_files)
    has_cf = any(ps.has_changefreq for ps in sitemap_files)
    deprecated = []
    if has_prio: deprecated.append("<priority>")
    if has_cf:   deprecated.append("<changefreq>")
    if deprecated:
        result.add(Finding("info",
            f"Deprecated sitemap tag(s) in use: {', '.join(deprecated)}",
            f"At least one entry uses {', '.join(deprecated)}.",
            "Google has ignored both <priority> and <changefreq> since 2017. They add noise to the sitemap and have no ranking effect.",
            "Strip these tags from your sitemap. They're harmless if left in, but removing them shrinks the file and signals modern conventions.",
        ))


def _check_lastmod_realism(entries: list[SitemapEntry], result: AnalyzeResult) -> None:
    """Flag identical <lastmod> across all entries — reads as fake/auto-generated."""
    lastmods = [e.lastmod for e in entries if e.lastmod]
    if not lastmods:
        result.add(Finding("nice-to-have",
            "No <lastmod> dates on any URL",
            f"None of {len(entries)} URL(s) include a <lastmod> tag.",
            "Google uses <lastmod> as a recency signal during crawl prioritization. Without it, crawlers fall back to less-accurate heuristics.",
            "Emit <lastmod> in ISO 8601 (YYYY-MM-DD) per URL. Use the actual page modification date — Google catches obvious fakes.",
        ))
        return

    unique_mods = set(lastmods)
    if len(unique_mods) == 1 and len(lastmods) >= 10:
        result.add(Finding("nice-to-have",
            "All <lastmod> dates are identical",
            f"All {len(lastmods)} <lastmod> values are {next(iter(unique_mods))!r}.",
            "Identical lastmod across thousands of URLs reads as auto-generated and degrades the signal's usefulness. Google has stated they treat suspicious lastmod patterns as missing data.",
            "Use the actual per-page modification dates. If you don't track them, omit <lastmod> entirely rather than emit a uniform fake.",
        ))


def _check_protocol_consistency(entries: list[SitemapEntry], base: str, result: AnalyzeResult) -> None:
    """Flag HTTP/HTTPS mismatch — sitemap URLs should match the canonical site protocol."""
    base_scheme = urlparse(base).scheme
    mismatched = [e for e in entries if urlparse(e.loc).scheme and urlparse(e.loc).scheme != base_scheme]
    if mismatched:
        sample = "; ".join(e.loc[:60] for e in mismatched[:3])
        result.add(Finding("recommended",
            f"Protocol mismatch: {len(mismatched)} URL(s) use a different scheme than the canonical site",
            f"Canonical site uses {base_scheme}://; {len(mismatched)} URLs use the other protocol. Examples: {sample}.",
            "Mixed HTTP/HTTPS URLs in a sitemap confuse canonicalization and waste crawl budget on duplicate-content redirects.",
            "Standardize all sitemap URLs to the canonical protocol (https:// preferred).",
        ))


def _check_location_pages(entries: list[SitemapEntry], result: AnalyzeResult) -> None:
    """Quality gate: warn at 30+ location-pattern URLs, hard-stop at 50+."""
    location_urls = [e for e in entries if _LOCATION_PATH_RE.search(urlparse(e.loc).path)]
    count = len(location_urls)
    if count >= 50:
        result.add(Finding("critical",
            f"Location-page pattern detected at scale ({count} URLs)",
            f"{count} URLs match location-page patterns (/locations/, /cities/, /service-areas/, etc.).",
            "Google penalizes doorway pages — many near-duplicate location pages with only city/region names swapped. At 50+ this risks a manual action.",
            "Audit each location page for unique content (≥60% non-template prose). Remove pages that duplicate boilerplate. Consider consolidating into a single service-area page with structured data.",
        ))
    elif count >= 30:
        result.add(Finding("recommended",
            f"Many location pages ({count}) — review for thin-content risk",
            f"{count} URLs match location patterns. Below the doorway-page hard threshold but worth verifying uniqueness.",
            "Programmatic location pages without unique value violate Google's quality guidelines. The risk scales with count.",
            "Sample 10 location pages and verify each has ≥60% unique content (testimonials, photos, local-specific copy, addresses).",
        ))


async def _verify_url_sample(entries: list[SitemapEntry], sample_size: int, result: AnalyzeResult) -> None:
    """HEAD-fetch a random sample of URLs to check status, redirects, and basic content."""
    if sample_size <= 0:
        return
    import random
    sample = random.sample(entries, min(sample_size, len(entries)))
    result.stats["verify_sample_size"] = len(sample)

    # Concurrent HEAD requests
    head_results = await asyncio.gather(
        *(fetch(e.loc, want_headers=True) for e in sample),
        return_exceptions=False,
    )

    not_200 = []
    redirects = []
    for entry, head in zip(sample, head_results):
        if not head:
            not_200.append((entry.loc, "fetch failed"))
            continue
        status = head.get("status", 0)
        if status == 0:
            continue  # proxy probably stripped status — count as pass
        if 300 <= status < 400:
            redirects.append((entry.loc, status))
        elif status >= 400:
            not_200.append((entry.loc, str(status)))

    result.stats["verify_failed"] = len(not_200)
    result.stats["verify_redirects"] = len(redirects)

    if not_200:
        sample_str = "; ".join(f"{u} ({s})" for u, s in not_200[:3])
        result.add(Finding("recommended",
            f"{len(not_200)}/{len(sample)} sampled URLs return non-2xx",
            f"Sample check (n={len(sample)}) found {len(not_200)} URLs that return error status. Examples: {sample_str}.",
            "404 and 5xx URLs in a sitemap waste crawl budget and degrade the site's perceived freshness. Google may eventually demote the sitemap as low-quality if the ratio is high.",
            "Remove the failing URLs. Bump --verify-sample N to widen the check if the failure rate is high.",
        ))
    if redirects:
        sample_str = "; ".join(f"{u} ({s})" for u, s in redirects[:3])
        result.add(Finding("recommended",
            f"{len(redirects)}/{len(sample)} sampled URLs are 3xx redirects",
            f"Sample check (n={len(sample)}) found {len(redirects)} URLs that redirect. Examples: {sample_str}.",
            "Sitemaps should list final destination URLs, not redirect chain starts. Each redirect costs crawl budget.",
            "Update the sitemap to use the final URLs (the redirect targets).",
        ))


# ---------------------------------------------------------------------------
# Crawler (for generate-from-crawl mode)
# ---------------------------------------------------------------------------

@dataclass
class CrawledPage:
    url: str
    canonical: Optional[str] = None
    noindex: bool = False
    lastmod: Optional[str] = None
    hreflang: list[dict] = field(default_factory=list)
    status: int = 0
    redirect_to: Optional[str] = None


async def crawl(seed_url: str, max_pages: int = 500, concurrency: int = 5,
                 data_attrs: tuple[str, ...] = DEFAULT_DATA_ATTRS) -> list[CrawledPage]:
    """BFS-crawl same-host internal links from seed_url.

    Filters non-HTML extensions, drops fragments, respects robots.txt at the
    site level (if Disallow: / on User-agent: *). For each page, captures
    canonical, robots noindex, hreflang variants, HTTP status.

    Link sources scanned per page:
      - <a href="...">                  (always)
      - elements with any of `data_attrs` set (e.g. data-article="articles/foo.html")
        — handles JS-driven navigation patterns where the link target lives
        in a data-* attribute. Pass an empty tuple to disable.
    """
    parsed = urlparse(seed_url)
    start_host = parsed.netloc.lower()
    base = f"{parsed.scheme}://{parsed.netloc}"

    # Check robots.txt for site-wide Disallow: /
    robots = await fetch(f"{base}/robots.txt")
    if robots and robots.get("body"):
        if _robots_blocks_all(robots["body"]):
            print(f"  crawl: robots.txt blocks all crawlers; producing empty result", file=sys.stderr)
            return []

    discovered: list[CrawledPage] = []
    seen: set[str] = set()
    queue: deque[str] = deque([_normalize_url(seed_url)])

    while queue and len(discovered) < max_pages:
        # Pull a batch up to `concurrency`
        batch: list[str] = []
        while queue and len(batch) < concurrency and len(discovered) + len(batch) < max_pages:
            url = queue.popleft()
            if url in seen:
                continue
            seen.add(url)
            batch.append(url)

        if not batch:
            break

        # Concurrent fetches
        results = await asyncio.gather(*(fetch(u) for u in batch), return_exceptions=False)

        for url, result in zip(batch, results):
            # Use the final URL (after redirects) as the page's canonical URL
            # and as the base for relative-link resolution. Without this,
            # urljoin against "http://localhost/cv" resolves "articles/foo.html"
            # to "http://localhost/articles/foo.html" (wrong directory) instead
            # of "http://localhost/cv/articles/foo.html".
            final_url = (result or {}).get("final_url") or url
            page = CrawledPage(url=final_url)
            if not result:
                continue
            page.status = result.get("status", 0)
            body = result.get("body", "")
            if not body:
                discovered.append(page)
                continue

            # Extract canonical, robots, hreflang from page <head>
            try:
                soup = BeautifulSoup(body, "html.parser")
            except Exception:
                discovered.append(page)
                continue
            canon = soup.find("link", rel="canonical")
            if canon and canon.get("href"):
                page.canonical = urljoin(final_url, canon.get("href"))
            robots_meta = soup.find("meta", attrs={"name": re.compile(r"^robots$", re.I)})
            if robots_meta:
                content = (robots_meta.get("content") or "").lower()
                if "noindex" in content:
                    page.noindex = True
            for link in soup.find_all("link", rel="alternate"):
                hreflang = link.get("hreflang")
                href = link.get("href")
                if hreflang and href:
                    page.hreflang.append({"lang": hreflang, "href": urljoin(final_url, href)})

            # Last-Modified from response headers
            lm = result.get("headers", {}).get("Last-Modified") or result.get("headers", {}).get("last-modified")
            if lm:
                page.lastmod = _normalize_lastmod(lm)

            discovered.append(page)
            print(f"  crawl: discovered {len(discovered)}/{max_pages}: {final_url}", file=sys.stderr)

            # Extract candidate URLs from the page. Standard <a href> first,
            # then any data-* attributes the user opted into (default list
            # covers data-article, data-href, data-url, data-link, data-page,
            # data-route, data-target — common in JS-driven UIs).
            def queue_candidate(raw_href: str) -> None:
                href = (raw_href or "").strip()
                if not href or href.startswith(("javascript:", "mailto:", "tel:")):
                    return
                absolute, _ = urldefrag(urljoin(final_url, href))
                p = urlparse(absolute)
                if p.scheme not in ("http", "https"):
                    return
                if p.netloc.lower() != start_host:
                    return
                if any(p.path.lower().endswith(ext) for ext in _SKIP_EXT):
                    return
                normalized = _normalize_url(absolute)
                if normalized not in seen:
                    queue.append(normalized)

            for a in soup.find_all("a", href=True):
                queue_candidate(a.get("href") or "")

            for attr in data_attrs:
                for el in soup.find_all(attrs={attr: True}):
                    queue_candidate(el.get(attr) or "")

    return discovered


def _robots_blocks_all(robots_txt: str) -> bool:
    """True if robots.txt has User-agent: * Disallow: /"""
    in_star = False
    for line in robots_txt.splitlines():
        line = line.split("#", 1)[0].strip()
        if not line:
            in_star = False
            continue
        if ":" not in line:
            continue
        key, _, val = line.partition(":")
        key = key.strip().lower()
        val = val.strip()
        if key == "user-agent":
            in_star = (val == "*")
        elif key == "disallow" and in_star and val == "/":
            return True
    return False


def _normalize_url(url: str) -> str:
    url, _ = urldefrag(url)
    p = urlparse(url)
    path = p.path
    if len(path) > 1 and path.endswith("/"):
        path = path[:-1]
    return f"{p.scheme}://{p.netloc}{path}{('?' + p.query) if p.query else ''}"


def _normalize_lastmod(http_date: str) -> Optional[str]:
    """Convert HTTP Date header (RFC 2822) to ISO 8601 date (YYYY-MM-DD)."""
    try:
        from email.utils import parsedate_to_datetime
        dt = parsedate_to_datetime(http_date)
        return dt.strftime("%Y-%m-%d")
    except Exception:
        return None


# ---------------------------------------------------------------------------
# Filter crawl results into sitemap-eligible URLs
# ---------------------------------------------------------------------------

def filter_for_sitemap(pages: list[CrawledPage], ignore_canonical: bool = False) -> tuple[list[CrawledPage], dict]:
    """Drop pages that should NOT be in a sitemap.

    Returns (kept_pages, summary_dict). Filters:
      - HTTP status != 200 (drops 3xx, 4xx, 5xx)
      - noindex set in <meta name="robots">
      - Non-canonical (canonical exists and points elsewhere) — skipped when
        ignore_canonical=True (useful for local-dev mirrors of production sites).
    """
    kept: list[CrawledPage] = []
    dropped_status = 0
    dropped_noindex = 0
    dropped_noncanonical = 0
    noncanonical_samples: list[tuple[str, str]] = []

    for p in pages:
        if p.status != 0 and p.status != 200:
            dropped_status += 1
            continue
        if p.noindex:
            dropped_noindex += 1
            continue
        if (not ignore_canonical) and p.canonical and _normalize_url(p.canonical) != _normalize_url(p.url):
            dropped_noncanonical += 1
            if len(noncanonical_samples) < 5:
                noncanonical_samples.append((p.url, p.canonical))
            continue
        kept.append(p)

    return kept, {
        "discovered": len(pages),
        "kept": len(kept),
        "dropped_non_200": dropped_status,
        "dropped_noindex": dropped_noindex,
        "dropped_noncanonical": dropped_noncanonical,
        "noncanonical_samples": noncanonical_samples,
    }


# ---------------------------------------------------------------------------
# Sitemap emission
# ---------------------------------------------------------------------------

def emit_sitemap_xml(pages: list[CrawledPage], hreflang: bool = False) -> str:
    """Emit a single <urlset> sitemap XML string."""
    if hreflang:
        ns_attr = f' xmlns:xhtml="{NS_XHTML}"'
    else:
        ns_attr = ""
    lines = [
        '<?xml version="1.0" encoding="UTF-8"?>',
        f'<urlset xmlns="{NS_SM}"{ns_attr}>',
    ]
    for p in pages:
        lines.append("  <url>")
        lines.append(f"    <loc>{_xml_escape(p.url)}</loc>")
        if p.lastmod:
            lines.append(f"    <lastmod>{p.lastmod}</lastmod>")
        if hreflang and p.hreflang:
            for variant in p.hreflang:
                lang = _xml_escape(variant.get("lang", ""))
                href = _xml_escape(variant.get("href", ""))
                lines.append(f'    <xhtml:link rel="alternate" hreflang="{lang}" href="{href}"/>')
        lines.append("  </url>")
    lines.append("</urlset>")
    return "\n".join(lines) + "\n"


def emit_sitemap_index_xml(sitemap_urls: list[str]) -> str:
    """Emit a <sitemapindex> XML string pointing to the sub-sitemaps."""
    lines = [
        '<?xml version="1.0" encoding="UTF-8"?>',
        f'<sitemapindex xmlns="{NS_SM}">',
    ]
    today = time.strftime("%Y-%m-%d")
    for url in sitemap_urls:
        lines.append("  <sitemap>")
        lines.append(f"    <loc>{_xml_escape(url)}</loc>")
        lines.append(f"    <lastmod>{today}</lastmod>")
        lines.append("  </sitemap>")
    lines.append("</sitemapindex>")
    return "\n".join(lines) + "\n"


def _xml_escape(s: str) -> str:
    return s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace('"', "&quot;")


def emit_split_sitemaps(pages: list[CrawledPage], output_dir: Path, base_url: str,
                        hreflang: bool = False) -> list[Path]:
    """Write one or more sitemap files (split at 50k URLs).

    Returns the list of written file paths. When the total exceeds 50k,
    also writes a sitemap-index.xml.
    """
    output_dir.mkdir(parents=True, exist_ok=True)
    written: list[Path] = []

    if len(pages) <= MAX_URLS_PER_SITEMAP:
        out = output_dir / "sitemap.xml"
        out.write_text(emit_sitemap_xml(pages, hreflang=hreflang), encoding="utf-8")
        written.append(out)
        return written

    # Split
    base = base_url.rstrip("/")
    chunks: list[list[CrawledPage]] = []
    for i in range(0, len(pages), MAX_URLS_PER_SITEMAP):
        chunks.append(pages[i:i + MAX_URLS_PER_SITEMAP])
    for i, chunk in enumerate(chunks, 1):
        out = output_dir / f"sitemap-pages-{i}.xml"
        out.write_text(emit_sitemap_xml(chunk, hreflang=hreflang), encoding="utf-8")
        written.append(out)

    index_urls = [f"{base}/{p.name}" for p in written]
    idx = output_dir / "sitemap-index.xml"
    idx.write_text(emit_sitemap_index_xml(index_urls), encoding="utf-8")
    written.append(idx)

    return written


# ---------------------------------------------------------------------------
# STRUCTURE.md writer
# ---------------------------------------------------------------------------

def render_structure_md(pages: list[CrawledPage], base_url: str,
                         filter_summary: dict) -> str:
    """Group URLs by path-pattern and render a structure report."""
    by_prefix: dict[str, list[CrawledPage]] = defaultdict(list)
    for p in pages:
        path = urlparse(p.url).path.strip("/")
        prefix = path.split("/", 1)[0] if "/" in path else (path or "(root)")
        by_prefix[prefix].append(p)

    sorted_prefixes = sorted(by_prefix.items(), key=lambda kv: (-len(kv[1]), kv[0]))

    lines = [
        f"# Sitemap Structure",
        "",
        f"**Source:** {base_url}",
        f"**Generated:** {time.strftime('%Y-%m-%d %H:%M:%S')}",
        "",
        "## Summary",
        "",
        f"- URLs discovered during crawl: {filter_summary.get('discovered', 0)}",
        f"- URLs kept for sitemap: {filter_summary.get('kept', 0)}",
        f"- Dropped (non-200 HTTP): {filter_summary.get('dropped_non_200', 0)}",
        f"- Dropped (noindex): {filter_summary.get('dropped_noindex', 0)}",
        f"- Dropped (non-canonical): {filter_summary.get('dropped_noncanonical', 0)}",
        "",
        "## URLs grouped by top-level path",
        "",
        "| Prefix | URL count |",
        "|---|---|",
    ]
    for prefix, items in sorted_prefixes:
        lines.append(f"| `/{prefix}` | {len(items)} |")
    lines.append("")

    for prefix, items in sorted_prefixes:
        lines.append(f"### /{prefix} ({len(items)} URL(s))")
        lines.append("")
        for p in items[:50]:
            lines.append(f"- {p.url}")
        if len(items) > 50:
            lines.append(f"- _… {len(items) - 50} more_")
        lines.append("")

    return "\n".join(lines).rstrip() + "\n"


# ---------------------------------------------------------------------------
# Validation report rendering (analyze mode)
# ---------------------------------------------------------------------------

def render_validation_report(result: AnalyzeResult, source: str) -> str:
    score = result.score()
    counts = Counter(f.severity for f in result.findings)

    lines = [
        f"# Sitemap Validation Report",
        "",
        f"**Source:** {source}",
        f"**Generated:** {time.strftime('%Y-%m-%d %H:%M:%S')}",
        "",
        f"## Score: {score}/100",
        "",
        f"- Critical issues: {counts.get('critical', 0)} (−{counts.get('critical', 0) * SEVERITY_WEIGHT['critical']} pts)",
        f"- Recommended fixes: {counts.get('recommended', 0)} (−{counts.get('recommended', 0) * SEVERITY_WEIGHT['recommended']} pts)",
        f"- Nice-to-have: {counts.get('nice-to-have', 0)} (−{counts.get('nice-to-have', 0) * SEVERITY_WEIGHT['nice-to-have']} pts)",
        f"- Informational: {counts.get('info', 0)}",
        "",
    ]

    if result.strengths:
        lines.append("## Strengths")
        lines.append("")
        for s in result.strengths:
            lines.append(f"- {s}")
        lines.append("")

    if result.findings:
        lines.append("## Findings")
        lines.append("")
        sev_order = {s: i for i, s in enumerate(SEVERITIES)}
        for f in sorted(result.findings, key=lambda x: sev_order[x.severity]):
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
            if isinstance(v, (list, dict)) and len(str(v)) > 200:
                lines.append(f"- `{k}`: ({len(v)} items)")
            else:
                lines.append(f"- `{k}`: {v}")
        lines.append("")

    return "\n".join(lines).rstrip() + "\n"


# ---------------------------------------------------------------------------
# CLI / main
# ---------------------------------------------------------------------------

def read_url_list(path: Path) -> list[str]:
    """Read URLs from a text file, one per line. '#' starts a comment."""
    urls = []
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.split("#", 1)[0].strip()
        if line:
            urls.append(line)
    return urls


async def amain(args) -> int:
    DEFAULT_OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    output = args.output

    if args.generate or args.from_list:
        # ---- Generation modes ----
        if args.from_list:
            urls = read_url_list(args.from_list)
            if not urls:
                print(f"ERROR: URL list at {args.from_list} is empty.", file=sys.stderr)
                return 1
            seed_url = urls[0]  # for output naming + base URL
            print(f"Building sitemap from {len(urls)} URL(s) in {args.from_list}…", file=sys.stderr)
            pages = [CrawledPage(url=u, status=200) for u in urls]
            base = f"{urlparse(seed_url).scheme}://{urlparse(seed_url).netloc}"
        else:
            seed_url = args.input
            data_attrs = tuple(a.strip() for a in args.data_attrs.split(",") if a.strip())
            print(f"Crawling {seed_url} (max {args.max_pages} pages, data-attrs: "
                  f"{','.join(data_attrs) or '(none)'})…", file=sys.stderr)
            pages = await crawl(seed_url, max_pages=args.max_pages,
                                 concurrency=args.concurrency, data_attrs=data_attrs)
            if not pages:
                print(f"ERROR: crawl from {seed_url} discovered no pages.", file=sys.stderr)
                return 1
            base = f"{urlparse(seed_url).scheme}://{urlparse(seed_url).netloc}"

        kept, filter_summary = filter_for_sitemap(pages, ignore_canonical=args.ignore_canonical)

        # Diagnostic: if 0 URLs kept, explain why prominently before bailing
        if not kept:
            print(f"\nNo URLs kept after filtering. Filter summary:", file=sys.stderr)
            print(f"  discovered:          {filter_summary['discovered']}", file=sys.stderr)
            print(f"  dropped non-200:     {filter_summary['dropped_non_200']}", file=sys.stderr)
            print(f"  dropped noindex:     {filter_summary['dropped_noindex']}", file=sys.stderr)
            print(f"  dropped non-canonical: {filter_summary['dropped_noncanonical']}", file=sys.stderr)
            samples = filter_summary.get("noncanonical_samples", [])
            if samples:
                print(f"\nNon-canonical samples (page → canonical):", file=sys.stderr)
                for page_url, canon in samples:
                    print(f"  {page_url}\n    → {canon}", file=sys.stderr)
                print(f"\nIf this is a local-dev mirror of a production site, "
                      f"rerun with --ignore-canonical.", file=sys.stderr)
            return 1

        # Quality gate: location-page count
        location_count = sum(1 for p in kept if _LOCATION_PATH_RE.search(urlparse(p.url).path))
        if location_count >= 50 and not args.force_locations:
            print(f"\nHARD STOP: {location_count} location-pattern URLs detected. "
                  f"At 50+ this risks a Google doorway-page penalty.\n"
                  f"Pass --force-locations to override after auditing each page for unique content.",
                  file=sys.stderr)
            return 2
        if location_count >= 30:
            print(f"\nWARNING: {location_count} location-pattern URLs detected. "
                  f"Verify each has ≥60% unique content before publishing.",
                  file=sys.stderr)

        # Emit sitemap files
        out_dir = output if output else DEFAULT_OUTPUT_DIR
        written = emit_split_sitemaps(kept, out_dir, base, hreflang=args.hreflang)

        # Companion STRUCTURE.md
        structure_md = render_structure_md(kept, seed_url, filter_summary)
        structure_path = out_dir / "STRUCTURE.md"
        structure_path.write_text(structure_md, encoding="utf-8")

        print(f"\nWrote {len(kept)} URL(s) across {len([w for w in written if 'sitemap-pages' in w.name or w.name == 'sitemap.xml'])} sitemap file(s):", file=sys.stderr)
        for w in written:
            print(f"  {w}", file=sys.stderr)
        print(f"  {structure_path}", file=sys.stderr)
        return 0

    # ---- Analyze mode ----
    target = args.input
    print(f"Analyzing sitemap for {target}…", file=sys.stderr)
    result = await analyze(target, verify_sample=args.verify_sample)

    report = render_validation_report(result, target)
    if args.stdout:
        sys.stdout.write(report)
    else:
        out_path = output if output else DEFAULT_OUTPUT_DIR / "VALIDATION-REPORT.md"
        if out_path.is_dir():
            out_path = out_path / "VALIDATION-REPORT.md"
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(report, encoding="utf-8")
        print(f"Wrote {out_path} (score: {result.score()}/100, {len(result.findings)} finding(s))", file=sys.stderr)
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Sitemap analysis and generation."
    )
    parser.add_argument("input", nargs="?",
        help="URL to analyze (e.g. https://example.com or https://example.com/sitemap.xml). "
             "Required unless --from-list is given. Accepts the URL positionally — no "
             "flag needed. The --url alias below is provided for LLM tools that prefer "
             "named arguments.")
    parser.add_argument("--url", dest="url_alias", default=None,
        help="Alias for the positional URL argument. `--url X` is equivalent to passing `X` "
             "directly. Useful when the caller (e.g. an LLM tool) cannot easily emit "
             "positional argv.")
    parser.add_argument("--generate", action="store_true",
        help="Generate a new sitemap. With a URL: crawls internal links. "
             "With --from-list: reads URLs from file. Output: sitemap.xml (split at 50k) "
             "+ STRUCTURE.md.")
    parser.add_argument("--from-list", type=Path, default=None,
        help="Generate from a list of URLs (one per line, '#' comments). "
             "Implies --generate.")
    parser.add_argument("--hreflang", action="store_true",
        help="Generate-mode: emit xhtml:link rel='alternate' hreflang variants per <url>.")
    parser.add_argument("--max-pages", type=int, default=500,
        help="Crawl-mode: cap at N pages (default: 500).")
    parser.add_argument("--concurrency", type=int, default=5,
        help="Crawl-mode: concurrent fetch workers (default: 5).")
    parser.add_argument("--force-locations", action="store_true",
        help="Generate-mode: emit sitemap even with 50+ location-pattern URLs "
             "(otherwise hard-stops). Document the unique-content justification.")
    parser.add_argument("--ignore-canonical", action="store_true",
        help="Generate-mode: don't filter pages whose canonical points to a different "
             "URL. Useful when crawling a local-dev mirror that declares production "
             "canonicals (e.g. http://localhost/cv with canonical https://example.com/).")
    parser.add_argument("--data-attrs", default=",".join(DEFAULT_DATA_ATTRS),
        help="Crawl-mode: comma-separated list of HTML attributes (in addition to "
             "<a href>) whose values are treated as link targets. Use this for SPAs "
             "or JS-driven sites that store navigation in data-* attributes. "
             f"Default: {','.join(DEFAULT_DATA_ATTRS)}. Pass an empty string to disable.")
    parser.add_argument("--verify-sample", type=int, default=50,
        help="Analyze-mode: number of URLs to HEAD-check for status (default: 50, 0 = skip).")
    parser.add_argument("-o", "--output", type=Path, default=None,
        help="Output path. Analyze: report .md file. Generate: directory for sitemap.xml + STRUCTURE.md.")
    parser.add_argument("--stdout", action="store_true",
        help="Analyze-mode: print report to stdout instead of writing a file.")
    parser.add_argument("--source", default=None,
        help="Source URL label when input is a local file (bridge pre-fetch case).")
    args = parser.parse_args(argv)

    # Accept --url as an alias for the positional argument.
    if not args.input and args.url_alias:
        args.input = args.url_alias

    # Treat --from-list as implying --generate
    if args.from_list:
        args.generate = True

    # When the bridge pre-fetched the URL, args.input is a local file path
    # and args.source carries the real URL. Use the URL for crawling/analysis.
    if args.source and re.match(r"^https?://", args.source, re.I):
        args.input = args.source

    if not args.input and not args.from_list:
        parser.print_help(sys.stderr)
        return 2

    return asyncio.run(amain(args))


if __name__ == "__main__":
    sys.exit(main())
