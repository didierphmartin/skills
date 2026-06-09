#!/usr/bin/env python3
"""
audit.py — Hreflang validation and generation.

Two purposes:
  1. VALIDATE the hreflang setup of a multi-language/multi-region page. Checks
     self-referencing tag, ISO 639-1 + ISO 3166-1 codes, x-default presence,
     return-tag mesh (A→B requires B→A), canonical alignment, HTTP/HTTPS
     consistency, trailing-slash consistency.

  2. GENERATE a corrected hreflang set in one of three formats:
       --format html      HTML <link rel="alternate"> tags
       --format http      HTTP Link: response-header value
       --format sitemap   XML sitemap with xhtml:link namespace

Pyodide-safe: async fetches route through /gpt/backend/api/v1/fetch-url
(same pattern as serp-shape, sitemap-audit, ai-search-audit's external pass).

Usage:
    python audit.py URL                       # validate (full + mesh)
    python audit.py URL --skip-mesh           # validate (local only, no fetches of alternates)
    python audit.py URL --generate --format html|http|sitemap
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
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional
from urllib.parse import urlparse, urljoin

from bs4 import BeautifulSoup


# Pyodide mounts the host outputs/ at /outputs (see pyodide-runner.js).
# fetches_urls=false in SKILL.md → no -o injection by the bridge → we route
# the default ourselves so the report lands on the host filesystem.
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


# ---------------------------------------------------------------------------
# ISO code tables
# ---------------------------------------------------------------------------

# ISO 639-1 two-letter language codes. ~184 codes total; embedded as a frozenset
# for O(1) validity lookup. Source: ISO 639-1:2002.
ISO_639_1: frozenset[str] = frozenset({
    "aa","ab","ae","af","ak","am","an","ar","as","av","ay","az","ba","be","bg",
    "bh","bi","bm","bn","bo","br","bs","ca","ce","ch","co","cr","cs","cu","cv",
    "cy","da","de","dv","dz","ee","el","en","eo","es","et","eu","fa","ff","fi",
    "fj","fo","fr","fy","ga","gd","gl","gn","gu","gv","ha","he","hi","ho","hr",
    "ht","hu","hy","hz","ia","id","ie","ig","ii","ik","io","is","it","iu","ja",
    "jv","ka","kg","ki","kj","kk","kl","km","kn","ko","kr","ks","ku","kv","kw",
    "ky","la","lb","lg","li","ln","lo","lt","lu","lv","mg","mh","mi","mk","ml",
    "mn","mr","ms","mt","my","na","nb","nd","ne","ng","nl","nn","no","nr","nv",
    "ny","oc","oj","om","or","os","pa","pi","pl","ps","pt","qu","rm","rn","ro",
    "ru","rw","sa","sc","sd","se","sg","si","sk","sl","sm","sn","so","sq","sr",
    "ss","st","su","sv","sw","ta","te","tg","th","ti","tk","tl","tn","to","tr",
    "ts","tt","tw","ty","ug","uk","ur","uz","ve","vi","vo","wa","wo","xh","yi",
    "yo","za","zh","zu",
})

# Common ISO 639-1 mistakes → suggestions (3-letter codes from 639-2 or wrong codes).
ISO_639_1_COMMON_MISTAKES: dict[str, str] = {
    "eng": "en", "fre": "fr", "fra": "fr", "ger": "de", "deu": "de",
    "spa": "es", "ita": "it", "por": "pt", "dut": "nl", "nld": "nl",
    "rus": "ru", "chi": "zh", "zho": "zh", "jpn": "ja", "kor": "ko",
    "ara": "ar", "heb": "he", "hin": "hi", "ben": "bn", "urd": "ur",
    "jp": "ja",  # Common typo for Japanese
    "cn": "zh",  # cn is a region code, not a language code
    "chinese": "zh", "japanese": "ja", "english": "en", "french": "fr",
    "german": "de", "spanish": "es",
}

# ISO 3166-1 alpha-2 country codes. ~249 codes total. Source: ISO 3166-1:2020.
ISO_3166_1: frozenset[str] = frozenset({
    "AD","AE","AF","AG","AI","AL","AM","AO","AQ","AR","AS","AT","AU","AW","AX",
    "AZ","BA","BB","BD","BE","BF","BG","BH","BI","BJ","BL","BM","BN","BO","BQ",
    "BR","BS","BT","BV","BW","BY","BZ","CA","CC","CD","CF","CG","CH","CI","CK",
    "CL","CM","CN","CO","CR","CU","CV","CW","CX","CY","CZ","DE","DJ","DK","DM",
    "DO","DZ","EC","EE","EG","EH","ER","ES","ET","FI","FJ","FK","FM","FO","FR",
    "GA","GB","GD","GE","GF","GG","GH","GI","GL","GM","GN","GP","GQ","GR","GS",
    "GT","GU","GW","GY","HK","HM","HN","HR","HT","HU","ID","IE","IL","IM","IN",
    "IO","IQ","IR","IS","IT","JE","JM","JO","JP","KE","KG","KH","KI","KM","KN",
    "KP","KR","KW","KY","KZ","LA","LB","LC","LI","LK","LR","LS","LT","LU","LV",
    "LY","MA","MC","MD","ME","MF","MG","MH","MK","ML","MM","MN","MO","MP","MQ",
    "MR","MS","MT","MU","MV","MW","MX","MY","MZ","NA","NC","NE","NF","NG","NI",
    "NL","NO","NP","NR","NU","NZ","OM","PA","PE","PF","PG","PH","PK","PL","PM",
    "PN","PR","PS","PT","PW","PY","QA","RE","RO","RS","RU","RW","SA","SB","SC",
    "SD","SE","SG","SH","SI","SJ","SK","SL","SM","SN","SO","SR","SS","ST","SV",
    "SX","SY","SZ","TC","TD","TF","TG","TH","TJ","TK","TL","TM","TN","TO","TR",
    "TT","TV","TW","TZ","UA","UG","UM","US","UY","UZ","VA","VC","VE","VG","VI",
    "VN","VU","WF","WS","YE","YT","ZA","ZM","ZW",
})

# Common ISO 3166-1 mistakes → suggestions.
ISO_3166_1_COMMON_MISTAKES: dict[str, str] = {
    "UK": "GB",   # United Kingdom uses GB in ISO 3166-1
    "EU": "",     # European Union — not a country, use individual states
    "LA": "",     # Latin America — not a country
    "EN": "",     # English is a language, not a country
    "EL": "GR",   # Greece (sometimes mistakenly uses EL from .gr languages list)
    "UN": "",     # United Nations — not a country
    "WW": "",     # Worldwide — use x-default instead
}

# Special hreflang value: x-default. Indicates the page for unmatched languages.
X_DEFAULT = "x-default"

# Sitemap XML namespaces
NS_SM = "http://www.sitemaps.org/schemas/sitemap/0.9"
NS_XHTML = "http://www.w3.org/1999/xhtml"


# ---------------------------------------------------------------------------
# Findings
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
class HreflangDeclaration:
    """One entry from a hreflang set: lang code + target URL."""
    lang: str
    url: str
    source: str = "html"   # 'html', 'http-header', or 'sitemap'


@dataclass
class PageHreflangSet:
    """All hreflang declarations a single page makes (incl. itself if self-ref)."""
    page_url: str
    declarations: list[HreflangDeclaration] = field(default_factory=list)
    canonical: Optional[str] = None
    fetch_status: int = 0
    fetch_error: Optional[str] = None
    final_url: str = ""


@dataclass
class ValidateResult:
    findings: list[Finding] = field(default_factory=list)
    strengths: list[str] = field(default_factory=list)
    stats: dict = field(default_factory=dict)
    input_page: Optional[PageHreflangSet] = None
    alternates: dict[str, PageHreflangSet] = field(default_factory=dict)  # url → set

    def add(self, f: Finding) -> None:
        self.findings.append(f)

    def score(self) -> int:
        deduction = sum(SEVERITY_WEIGHT[f.severity] for f in self.findings)
        return max(0, 100 - deduction)


# ---------------------------------------------------------------------------
# Fetch helpers
# ---------------------------------------------------------------------------

def _is_pyodide() -> bool:
    return sys.platform == "emscripten"


async def _fetch_pyodide(url: str, want_headers: bool = False) -> Optional[dict]:
    """Pyodide: POST to /gpt/backend/api/v1/fetch-url proxy."""
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
            PROXY_ENDPOINT, method="POST", headers=headers,
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


def _fetch_urllib(url: str, want_headers: bool = False) -> Optional[dict]:
    """CPython: direct urllib request."""
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
    if _is_pyodide():
        return await _fetch_pyodide(url, want_headers=want_headers)
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(None, _fetch_urllib, url, want_headers)


# ---------------------------------------------------------------------------
# Hreflang extraction (three sources)
# ---------------------------------------------------------------------------

def extract_from_html(html: str, base_url: str) -> list[HreflangDeclaration]:
    """Extract <link rel="alternate" hreflang="..." href="..."> from HTML head."""
    decls: list[HreflangDeclaration] = []
    try:
        soup = BeautifulSoup(html, "html.parser")
    except Exception:
        return decls
    for link in soup.find_all("link", rel="alternate"):
        lang = link.get("hreflang")
        href = link.get("href")
        if lang and href:
            decls.append(HreflangDeclaration(
                lang=lang.strip(),
                url=urljoin(base_url, href.strip()),
                source="html",
            ))
    return decls


def extract_from_http_header(headers: dict, base_url: str) -> list[HreflangDeclaration]:
    """Parse RFC 8288 Link: header for rel=alternate hreflang=... entries.

    Header form (multi-value):
      Link: <https://example.com/page>; rel="alternate"; hreflang="en-US",
            <https://example.com/fr/page>; rel="alternate"; hreflang="fr"
    """
    decls: list[HreflangDeclaration] = []
    # Case-insensitive lookup
    link_header = None
    for k, v in headers.items():
        if k.lower() == "link":
            link_header = v
            break
    if not link_header:
        return decls
    # Split entries: each starts with `<...>;` and may span commas inside params.
    # We use a tolerant regex.
    for match in re.finditer(r"<([^>]+)>\s*((?:;\s*[^,;]+)*)", link_header):
        target = match.group(1).strip()
        params_blob = match.group(2)
        params = dict(re.findall(r';\s*([^=;\s]+)\s*=\s*"?([^";]+)"?', params_blob))
        if params.get("rel") == "alternate" and "hreflang" in params:
            decls.append(HreflangDeclaration(
                lang=params["hreflang"].strip(),
                url=urljoin(base_url, target),
                source="http-header",
            ))
    return decls


def extract_from_sitemap_xml(xml_text: str, base_url: str) -> dict[str, list[HreflangDeclaration]]:
    """Parse <url>...<xhtml:link rel="alternate" hreflang="..." href="..."/></url>.

    Returns mapping page_url → list of declarations on that page entry.
    """
    result: dict[str, list[HreflangDeclaration]] = {}
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError:
        return result
    root_tag = root.tag.split("}", 1)[1] if "}" in root.tag else root.tag
    if root_tag.lower() != "urlset":
        return result
    for url_el in root:
        loc = None
        decls: list[HreflangDeclaration] = []
        for child in url_el:
            tag = child.tag.split("}", 1)[1] if "}" in child.tag else child.tag
            if tag.lower() == "loc":
                loc = (child.text or "").strip()
            elif tag.lower() == "link":
                rel = child.get("rel", "")
                hreflang = child.get("hreflang", "")
                href = child.get("href", "")
                if rel == "alternate" and hreflang and href:
                    decls.append(HreflangDeclaration(
                        lang=hreflang.strip(),
                        url=urljoin(base_url, href.strip()),
                        source="sitemap",
                    ))
        if loc and decls:
            result[loc] = decls
    return result


async def fetch_page_set(url: str) -> PageHreflangSet:
    """Fetch a page and extract its hreflang set from HTML + HTTP Link header."""
    ps = PageHreflangSet(page_url=url)
    result = await fetch(url)
    if not result:
        ps.fetch_error = "Fetch returned no response (proxy error or network failure)"
        return ps
    ps.fetch_status = result.get("status", 0) or 0
    ps.final_url = result.get("final_url", "") or url
    body = result.get("body", "")
    headers = result.get("headers", {}) or {}

    # Detect: is this a sitemap? Then handle differently.
    if body.lstrip().startswith("<?xml") or body.lstrip().startswith("<urlset"):
        sitemap_map = extract_from_sitemap_xml(body, ps.final_url)
        # Use the entry matching the input URL, if any
        for loc, decls in sitemap_map.items():
            if _normalize_url(loc) == _normalize_url(url):
                ps.declarations = decls
                return ps
        # Otherwise just merge all
        for decls in sitemap_map.values():
            ps.declarations.extend(decls)
        return ps

    # HTML page: extract from <link> tags + Link: header + canonical
    ps.declarations.extend(extract_from_html(body, ps.final_url))
    ps.declarations.extend(extract_from_http_header(headers, ps.final_url))
    # Canonical from HTML
    try:
        soup = BeautifulSoup(body, "html.parser")
        canon = soup.find("link", rel="canonical")
        if canon and canon.get("href"):
            ps.canonical = urljoin(ps.final_url, canon.get("href"))
    except Exception:
        pass

    return ps


def _normalize_url(url: str) -> str:
    """Strip trailing slash and fragment for URL comparison."""
    from urllib.parse import urldefrag
    url, _ = urldefrag(url)
    p = urlparse(url)
    path = p.path
    if len(path) > 1 and path.endswith("/"):
        path = path[:-1]
    return f"{p.scheme}://{p.netloc}{path}{('?' + p.query) if p.query else ''}"


# ---------------------------------------------------------------------------
# Validation pass
# ---------------------------------------------------------------------------

_HREFLANG_VALUE_RE = re.compile(r"^([a-zA-Z]{2,3})(?:-([a-zA-Z]{2,4}))?$")


def parse_hreflang_value(value: str) -> tuple[Optional[str], Optional[str]]:
    """Split a hreflang value into (language, region|None).

    Returns (None, None) if the format is malformed.
    'en-US' → ('en', 'US')
    'en'    → ('en', None)
    'eng'   → ('eng', None)  — caller catches invalid codes
    'x-default' → handled separately; this function returns (None, None) for it.
    """
    if value == X_DEFAULT:
        return (None, None)
    m = _HREFLANG_VALUE_RE.match(value)
    if not m:
        return (None, None)
    return (m.group(1).lower(), m.group(2).upper() if m.group(2) else None)


def validate_codes(decls: list[HreflangDeclaration], result: ValidateResult) -> None:
    """Validate each declaration's language and region codes."""
    invalid_lang: list[tuple[str, str]] = []  # (declared, suggestion)
    invalid_region: list[tuple[str, str, str]] = []  # (lang, declared, suggestion)
    malformed: list[str] = []

    for d in decls:
        if d.lang == X_DEFAULT:
            continue
        lang, region = parse_hreflang_value(d.lang)
        if lang is None:
            malformed.append(d.lang)
            continue
        if lang not in ISO_639_1:
            suggestion = ISO_639_1_COMMON_MISTAKES.get(lang, "")
            invalid_lang.append((d.lang, suggestion))
        if region and region not in ISO_3166_1:
            suggestion = ISO_3166_1_COMMON_MISTAKES.get(region, "")
            invalid_region.append((lang or "?", d.lang, suggestion))

    if malformed:
        sample = ", ".join(repr(v) for v in malformed[:5])
        result.add(Finding("critical",
            f"Malformed hreflang value(s): {len(malformed)}",
            f"Values that don't match the `language` or `language-REGION` pattern: {sample}.",
            "Malformed hreflang values are silently dropped by search engines. The relationship they encode is lost.",
            "Use the form `language` (e.g. `fr`) or `language-REGION` (e.g. `en-GB`). The special value `x-default` is also valid. No other shapes are accepted.",
        ))

    if invalid_lang:
        details = "; ".join(
            f"{declared!r}{' (use ' + suggestion + ')' if suggestion else ''}"
            for declared, suggestion in invalid_lang[:5]
        )
        result.add(Finding("critical",
            f"Invalid ISO 639-1 language code(s): {len(invalid_lang)}",
            f"Codes not in ISO 639-1: {details}.",
            "Search engines parse hreflang against ISO 639-1 strictly. Three-letter codes (`eng`, `fra`), region codes used as language codes (`cn`, `jp`), and full language names (`english`) are all silently dropped.",
            "Use the two-letter ISO 639-1 code. Common fixes: eng→en, fre/fra→fr, ger/deu→de, jp→ja, chinese/cn→zh.",
        ))

    if invalid_region:
        details = "; ".join(
            f"{full!r}{' (use ' + lang + '-' + suggestion + ')' if suggestion else ''}"
            for lang, full, suggestion in invalid_region[:5]
        )
        result.add(Finding("critical",
            f"Invalid ISO 3166-1 region code(s): {len(invalid_region)}",
            f"Region codes not in ISO 3166-1 alpha-2: {details}.",
            "Region codes must be ISO 3166-1 alpha-2 — uppercase, two letters. UK (use GB), EU (not a country), LA (Latin America is not a country) all fail validation and the entire hreflang entry is dropped.",
            "Use the country's ISO 3166-1 alpha-2 code. Common fix: `en-uk`→`en-GB`. For regional groupings (Latin America, EU), use individual country codes or fall back to language-only (`es` instead of `es-LA`).",
        ))


def validate_self_reference(input_url: str, decls: list[HreflangDeclaration],
                              result: ValidateResult) -> None:
    """The input page must include a hreflang tag pointing to itself."""
    normalized_input = _normalize_url(input_url)
    self_refs = [d for d in decls if _normalize_url(d.url) == normalized_input]
    if not self_refs:
        result.add(Finding("critical",
            "No self-referencing hreflang tag",
            f"None of the {len(decls)} hreflang declarations point back to the input URL ({input_url}).",
            "Google requires a self-referencing hreflang tag. When missing, the entire hreflang set on the page is ignored — all language variants lose their relationship signal.",
            f"Add `<link rel='alternate' hreflang='<your-lang>' href='{input_url}' />` to the input page, where `<your-lang>` matches the page's actual language/region.",
        ))
    else:
        result.strengths.append(f"Self-reference present: hreflang='{self_refs[0].lang}' → {input_url}")


def validate_x_default(decls: list[HreflangDeclaration], result: ValidateResult) -> None:
    """Recommended: every hreflang set should include x-default for unmatched languages."""
    x_defaults = [d for d in decls if d.lang == X_DEFAULT]
    if not x_defaults:
        result.add(Finding("recommended",
            "No x-default hreflang",
            "No hreflang tag with value 'x-default' in the set.",
            "x-default tells engines which page to serve when no declared language matches the user's preference. Without it, mismatched users may see the wrong locale.",
            f"Add `<link rel='alternate' hreflang='x-default' href='<your-fallback-url>' />`, typically pointing to a language-selector page or the English/global homepage.",
        ))
    elif len(x_defaults) > 1:
        result.add(Finding("recommended",
            f"Multiple x-default tags ({len(x_defaults)})",
            f"Found {len(x_defaults)} 'x-default' hreflang tags pointing to: {', '.join(d.url for d in x_defaults[:3])}.",
            "Only one x-default per page is meaningful. Multiple values create ambiguity engines silently resolve in unpredictable ways.",
            "Keep one x-default. Remove the duplicates.",
        ))


def validate_protocol_consistency(decls: list[HreflangDeclaration], result: ValidateResult) -> None:
    """Flag mixed http:// vs https:// across the set."""
    schemes = {urlparse(d.url).scheme for d in decls if d.url}
    if len(schemes) > 1:
        sample = "; ".join(d.url[:60] for d in decls[:3])
        result.add(Finding("recommended",
            f"Mixed protocols in hreflang set: {', '.join(sorted(schemes))}",
            f"Hreflang URLs use both schemes. Examples: {sample}.",
            "Mixed HTTP/HTTPS hreflang invalidates the relationship as engines treat the two protocols as different URLs entirely.",
            "Standardize all hreflang URLs on HTTPS. If a site is still partially HTTP, that's a separate migration problem worth addressing first.",
        ))


def validate_canonical_alignment(input_page: PageHreflangSet, result: ValidateResult) -> None:
    """Hreflang only counts on canonical URLs."""
    if not input_page.canonical:
        return  # No canonical declared; can't check alignment
    if _normalize_url(input_page.canonical) != _normalize_url(input_page.page_url):
        result.add(Finding("critical",
            "Hreflang on a non-canonical page",
            f"This page (page_url={input_page.page_url}) declares canonical={input_page.canonical}.",
            "Google ignores hreflang tags on any page that points its canonical at a different URL. The entire hreflang set is effectively invisible.",
            f"Either: (a) make this page self-canonical by changing the canonical tag, OR (b) move the hreflang declarations to the canonical URL ({input_page.canonical}) and remove them from this page.",
        ))


async def validate_mesh(input_page: PageHreflangSet, result: ValidateResult,
                          concurrency: int = 5) -> None:
    """Mesh check: for each declared alternate, fetch it and verify it links back.

    Builds an adjacency check: declared A → B means B's hreflang set must
    contain a return tag pointing to A.
    """
    declared_targets = [d for d in input_page.declarations
                         if d.lang != X_DEFAULT and _normalize_url(d.url) != _normalize_url(input_page.page_url)]
    if not declared_targets:
        return

    print(f"  mesh: fetching {len(declared_targets)} alternate page(s) for return-tag verification…",
          file=sys.stderr)

    # Concurrent fetches (capped)
    sem = asyncio.Semaphore(concurrency)
    async def fetch_with_sem(url: str) -> PageHreflangSet:
        async with sem:
            return await fetch_page_set(url)

    page_sets = await asyncio.gather(*(fetch_with_sem(d.url) for d in declared_targets))

    input_norm = _normalize_url(input_page.page_url)
    missing_returns: list[tuple[str, str]] = []  # (alternate_url, missing_lang)
    unreachable: list[str] = []

    for decl, alt_set in zip(declared_targets, page_sets):
        result.alternates[decl.url] = alt_set
        if alt_set.fetch_error or alt_set.fetch_status >= 400:
            unreachable.append(decl.url)
            continue
        # Does the alternate's hreflang set contain a return tag pointing to our input?
        return_tags = [d for d in alt_set.declarations
                        if _normalize_url(d.url) == input_norm]
        if not return_tags:
            missing_returns.append((decl.url, decl.lang))

    result.stats["mesh_alternates_checked"] = len(declared_targets)
    result.stats["mesh_unreachable"] = len(unreachable)
    result.stats["mesh_missing_returns"] = len(missing_returns)

    if unreachable:
        sample = "; ".join(unreachable[:3])
        result.add(Finding("recommended",
            f"Alternate page(s) unreachable: {len(unreachable)}/{len(declared_targets)}",
            f"Could not fetch {len(unreachable)} alternate URL(s). Examples: {sample}.",
            "Unreachable alternates can't have their hreflang sets verified. Engines that can't reach them either drop the relationship or treat it as broken.",
            "Make the alternate URLs reachable from the public internet. If they're staged behind auth, run validation on the public versions instead.",
        ))

    if missing_returns:
        sample = "; ".join(f"{u} (missing return for hreflang={lang!r})" for u, lang in missing_returns[:3])
        result.add(Finding("critical",
            f"Missing return tags: {len(missing_returns)} alternate(s) don't link back",
            f"Declared one-way A→B without B→A return tag. Examples: {sample}.",
            "Hreflang must be bidirectional. A unilateral A→B declaration is silently dropped by engines as ambiguous (which page is the 'main' one for the language?).",
            f"Add `<link rel='alternate' hreflang='<lang-of-A>' href='{input_page.page_url}' />` to each of the listed alternate pages, where `<lang-of-A>` is the input page's own hreflang value.",
        ))

    if not unreachable and not missing_returns:
        result.strengths.append(f"All {len(declared_targets)} alternate(s) have correct return tags.")


# ---------------------------------------------------------------------------
# Generation
# ---------------------------------------------------------------------------

def _xml_escape(s: str) -> str:
    return s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace('"', "&quot;")


def generate_html(decls: list[HreflangDeclaration]) -> str:
    """Emit <link rel='alternate' hreflang='...' href='...' /> tags."""
    # Dedup by (lang, url)
    seen: set[tuple[str, str]] = set()
    lines = []
    for d in decls:
        key = (d.lang, _normalize_url(d.url))
        if key in seen:
            continue
        seen.add(key)
        lines.append(f'<link rel="alternate" hreflang="{_xml_escape(d.lang)}" href="{_xml_escape(d.url)}" />')
    return "\n".join(lines) + "\n"


def generate_http_header(decls: list[HreflangDeclaration]) -> str:
    """Emit an RFC 8288 Link: header value."""
    seen: set[tuple[str, str]] = set()
    parts = []
    for d in decls:
        key = (d.lang, _normalize_url(d.url))
        if key in seen:
            continue
        seen.add(key)
        parts.append(f'<{d.url}>; rel="alternate"; hreflang="{d.lang}"')
    return "Link: " + ", ".join(parts) + "\n"


def generate_sitemap(decls: list[HreflangDeclaration], page_url: str) -> str:
    """Emit a single <url> entry with xhtml:link variants."""
    lines = [
        '<?xml version="1.0" encoding="UTF-8"?>',
        f'<urlset xmlns="{NS_SM}"',
        f'        xmlns:xhtml="{NS_XHTML}">',
        "  <url>",
        f"    <loc>{_xml_escape(page_url)}</loc>",
    ]
    seen: set[tuple[str, str]] = set()
    for d in decls:
        key = (d.lang, _normalize_url(d.url))
        if key in seen:
            continue
        seen.add(key)
        lines.append(f'    <xhtml:link rel="alternate" hreflang="{_xml_escape(d.lang)}" href="{_xml_escape(d.url)}"/>')
    lines.append("  </url>")
    lines.append("</urlset>")
    return "\n".join(lines) + "\n"


def build_corrected_set(input_page: PageHreflangSet) -> list[HreflangDeclaration]:
    """Take the input page's declarations, normalize + sort, ensure self-ref and x-default.

    Does not invent new languages; only cleans up existing entries.
    """
    decls = list(input_page.declarations)

    # Ensure self-reference. If no entry currently points back to the page,
    # we can't safely add one (we don't know what language to assert). Leave
    # the gap for the validation report to surface.

    # Ensure x-default. If missing, default to self-URL.
    has_xdef = any(d.lang == X_DEFAULT for d in decls)
    if not has_xdef:
        decls.append(HreflangDeclaration(lang=X_DEFAULT, url=input_page.page_url, source="generated"))

    # Sort: x-default last, otherwise alpha by lang
    decls.sort(key=lambda d: (d.lang == X_DEFAULT, d.lang.lower()))
    return decls


# ---------------------------------------------------------------------------
# Validation report rendering
# ---------------------------------------------------------------------------

def render_report(result: ValidateResult, source: str) -> str:
    score = result.score()
    counts = Counter(f.severity for f in result.findings)

    lines = [
        "# Hreflang Validation Report",
        "",
        f"**Source:** {source}",
        f"**Generated:** {time.strftime('%Y-%m-%d %H:%M:%S')}",
        "",
        f"## Score: {score}/100",
        "",
        f"- Critical: {counts.get('critical', 0)} (−{counts.get('critical', 0) * SEVERITY_WEIGHT['critical']} pts)",
        f"- Recommended: {counts.get('recommended', 0)} (−{counts.get('recommended', 0) * SEVERITY_WEIGHT['recommended']} pts)",
        f"- Nice-to-have: {counts.get('nice-to-have', 0)} (−{counts.get('nice-to-have', 0) * SEVERITY_WEIGHT['nice-to-have']} pts)",
        "",
    ]

    if result.input_page and result.input_page.declarations:
        lines.append("## Declared hreflang set on input page")
        lines.append("")
        lines.append("| hreflang | URL | source |")
        lines.append("|---|---|---|")
        for d in result.input_page.declarations:
            lines.append(f"| `{d.lang}` | {d.url} | {d.source} |")
        lines.append("")

    if result.alternates:
        lines.append("## Mesh check: alternate pages")
        lines.append("")
        lines.append("| Alternate URL | fetched | declarations | links back? |")
        lines.append("|---|---|---|---|")
        input_norm = _normalize_url(result.input_page.page_url) if result.input_page else ""
        for url, alt in result.alternates.items():
            ok = "✗" if (alt.fetch_error or alt.fetch_status >= 400) else "✓"
            decl_count = len(alt.declarations)
            links_back = "✓" if input_norm and any(_normalize_url(d.url) == input_norm for d in alt.declarations) else "✗"
            lines.append(f"| {url} | {ok} | {decl_count} | {links_back} |")
        lines.append("")

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
            lines.append(f"- `{k}`: {v}")
        lines.append("")

    return "\n".join(lines).rstrip() + "\n"


# ---------------------------------------------------------------------------
# Main pipeline
# ---------------------------------------------------------------------------

async def run_validate(input_url: str, skip_mesh: bool = False) -> ValidateResult:
    result = ValidateResult()

    input_page = await fetch_page_set(input_url)
    result.input_page = input_page

    if input_page.fetch_error:
        result.add(Finding("critical",
            "Could not fetch input page",
            f"Fetch error: {input_page.fetch_error}",
            "Without the page's HTML or response headers, no hreflang declarations can be extracted or validated.",
            "Verify the URL is correct and reachable from the audit environment.",
        ))
        return result

    if input_page.fetch_status >= 400:
        result.add(Finding("critical",
            f"Input page returned HTTP {input_page.fetch_status}",
            f"The URL responded with a {input_page.fetch_status} status code.",
            "Pages returning error status cannot host hreflang relationships — engines treat them as broken.",
            "Fix the page's HTTP status before running this audit.",
        ))

    result.stats["input_page_status"] = input_page.fetch_status
    result.stats["input_page_final_url"] = input_page.final_url
    result.stats["declarations_found"] = len(input_page.declarations)

    if not input_page.declarations:
        result.add(Finding("critical",
            "No hreflang declarations found",
            "Page has no <link rel='alternate' hreflang='...'> tags in HTML, no Link: header alternates, and no xhtml:link in a sitemap entry.",
            "Without hreflang, search engines have no signal that language/region variants exist. Users see the wrong-language version in search results.",
            "Add hreflang declarations. For a single-language site, this skill doesn't apply. For multi-language, declare every variant including self-reference and x-default.",
        ))
        return result

    # Core validations (always run)
    validate_codes(input_page.declarations, result)
    validate_self_reference(input_url, input_page.declarations, result)
    validate_x_default(input_page.declarations, result)
    validate_protocol_consistency(input_page.declarations, result)
    validate_canonical_alignment(input_page, result)

    # Mesh check (skippable)
    if not skip_mesh:
        await validate_mesh(input_page, result)
    else:
        result.stats["mesh_skipped"] = True

    return result


async def amain(args) -> int:
    DEFAULT_OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    if args.generate:
        # Validate first (without mesh) to get the parsed declarations
        result = await run_validate(args.input, skip_mesh=True)
        if not result.input_page or not result.input_page.declarations:
            print("ERROR: cannot generate — no hreflang declarations found on input page.",
                  file=sys.stderr)
            return 1

        corrected = build_corrected_set(result.input_page)

        if args.format == "html":
            output = generate_html(corrected)
            ext = ".html"
        elif args.format == "http":
            output = generate_http_header(corrected)
            ext = ".http.txt"
        elif args.format == "sitemap":
            output = generate_sitemap(corrected, result.input_page.page_url)
            ext = ".xml"
        else:
            print(f"ERROR: unknown --format {args.format!r}", file=sys.stderr)
            return 2

        if args.stdout:
            sys.stdout.write(output)
        else:
            out_path = args.output if args.output else (DEFAULT_OUTPUT_DIR / f"hreflang-corrected{ext}")
            if out_path.is_dir():
                out_path = out_path / f"hreflang-corrected{ext}"
            out_path.parent.mkdir(parents=True, exist_ok=True)
            out_path.write_text(output, encoding="utf-8")
            print(f"Wrote {out_path} ({len(corrected)} declaration(s), format={args.format})",
                  file=sys.stderr)
        return 0

    # Validate mode (default)
    result = await run_validate(args.input, skip_mesh=args.skip_mesh)
    report = render_report(result, args.input)

    if args.stdout:
        sys.stdout.write(report)
    else:
        out_path = args.output if args.output else (DEFAULT_OUTPUT_DIR / "VALIDATION-REPORT.md")
        if out_path.is_dir():
            out_path = out_path / "VALIDATION-REPORT.md"
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(report, encoding="utf-8")
        print(f"Wrote {out_path} (score: {result.score()}/100, {len(result.findings)} finding(s))",
              file=sys.stderr)
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Hreflang validation and generation.")
    parser.add_argument("input", nargs="?",
        help="URL to audit (HTML page or sitemap.xml). Required unless --url is passed. "
             "Accepts the URL positionally — no flag needed.")
    parser.add_argument("--url", dest="url_alias", default=None,
        help="Alias for the positional URL argument. Use when the caller cannot emit "
             "positional argv.")
    parser.add_argument("--skip-mesh", action="store_true",
        help="Skip the return-tag mesh check (faster — no fetches of alternates).")
    parser.add_argument("--generate", action="store_true",
        help="Generate a corrected hreflang set from the input page's declarations.")
    parser.add_argument("--format", choices=("html", "http", "sitemap"), default="html",
        help="Generate-mode: output format. html = <link> tags; http = Link: header value; "
             "sitemap = XML <urlset> with xhtml:link namespace.")
    parser.add_argument("-o", "--output", type=Path, default=None,
        help="Output path (file or directory). Default: ~/Documents/synergyAI/outputs/ "
             "(or /outputs/ in Pyodide).")
    parser.add_argument("--stdout", action="store_true",
        help="Print to stdout instead of writing a file.")
    parser.add_argument("--source", default=None,
        help="Source URL label when input is a local file (bridge pre-fetch case).")
    args = parser.parse_args(argv)

    # Accept --url alias
    if not args.input and args.url_alias:
        args.input = args.url_alias

    # When the bridge pre-fetched the URL, args.input is a local file path
    # and args.source carries the real URL.
    if args.source and re.match(r"^https?://", args.source, re.I):
        args.input = args.source

    if not args.input:
        parser.print_help(sys.stderr)
        return 2

    return asyncio.run(amain(args))


if __name__ == "__main__":
    sys.exit(main())
