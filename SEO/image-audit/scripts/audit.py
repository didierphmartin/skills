#!/usr/bin/env python3
"""
audit.py — Image SEO and performance audit.

Extracts every <img> on a page, then runs eight check categories:
  1. Alt text (presence, length, quality, keyword stuffing)
  2. Format (WebP/AVIF vs JPEG/PNG, <picture> fallback chain)
  3. Responsive (srcset, sizes)
  4. Lazy loading — 5-stack detection (native, perfmatters, ewww, js-generic, none)
  5. LCP/CLS (fetchpriority, decoding, width/height attributes)
  6. Filename quality (IMG_1234, DSC_0001, hex strings flagged)
  7. CDN usage
  8. File size (optional, --skip-sizes — async HEAD probes)

Pyodide-safe: routes fetches through /gpt/backend/api/v1/fetch-url.

Usage:
    python audit.py URL                 # full audit (slowest)
    python audit.py URL --skip-sizes    # HTML-only (no per-image HEAD probes)
"""

from __future__ import annotations

import os
import argparse
import asyncio
import json
import re
import sys
import time
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional
from urllib.parse import urlparse, urljoin

from bs4 import BeautifulSoup


# Output directory — Pyodide vs CPython. See sitemap-audit / hreflang-audit
# for the rationale (fetches_urls=false skips the bridge's -o injection).
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
# Lazy-load detection — lifted verbatim from claude-seo/scripts/parse_html.py
# (lines 31-68). Detects 5 mechanisms: native + 3 WordPress optimizer stacks +
# generic JS lazy-load + none. Catches the case where a site IS lazy-loading
# heavily but the native attribute is intentionally stripped.
# ---------------------------------------------------------------------------

_PERFMATTERS_ATTRS = ("data-perfmatters-src", "data-perfmatters-srcset")
_EWWW_ATTRS = ("data-ewww-src", "data-eio")
_GENERIC_LAZY_ATTRS = ("data-src", "data-lazy-src", "data-original", "data-srcset")
_PERFMATTERS_CLASSES = {"perfmatters-lazy", "perfmatters-lazy-loaded"}
_EWWW_CLASSES = {"lazyload-eio", "lazyloaded-eio"}
_GENERIC_LAZY_CLASSES = {"lazyload", "lazyloaded", "lazy", "lazy-loaded"}


def detect_lazy_method(img) -> str:
    """Return one of: 'native', 'perfmatters', 'ewww', 'js-generic', 'none'.

    Order: specific WordPress stacks first (so reports attribute correctly),
    then generic data-* lazy patterns, then native HTML attribute.
    """
    if (img.get("loading") or "").lower() == "lazy":
        return "native"
    class_list = set(img.get("class", []) or [])
    if any(img.get(a) for a in _PERFMATTERS_ATTRS) or class_list & _PERFMATTERS_CLASSES:
        return "perfmatters"
    if any(img.get(a) for a in _EWWW_ATTRS) or class_list & _EWWW_CLASSES:
        return "ewww"
    if any(img.get(a) for a in _GENERIC_LAZY_ATTRS) or class_list & _GENERIC_LAZY_CLASSES:
        return "js-generic"
    return "none"


# ---------------------------------------------------------------------------
# Known image format → MIME mappings (for picture/source detection)
# ---------------------------------------------------------------------------

EXTENSION_FORMAT = {
    ".jpg": "JPEG", ".jpeg": "JPEG",
    ".png": "PNG",
    ".gif": "GIF",
    ".webp": "WebP",
    ".avif": "AVIF",
    ".jxl": "JPEG-XL",
    ".svg": "SVG",
    ".bmp": "BMP",
    ".ico": "ICO",
    ".tiff": "TIFF", ".tif": "TIFF",
}

MODERN_FORMATS = {"WebP", "AVIF", "SVG"}

# Common CDN hostname patterns. (hostname-contains string, label).
CDN_PATTERNS = [
    ("cloudfront.net", "AWS CloudFront"),
    ("cloudflare.com", "Cloudflare"),
    ("cdn.cloudflare.net", "Cloudflare"),
    ("imgix.net", "Imgix"),
    ("cloudinary.com", "Cloudinary"),
    ("fastly.net", "Fastly"),
    ("fastlylb.net", "Fastly"),
    ("akamaized.net", "Akamai"),
    ("akamaihd.net", "Akamai"),
    ("imagekit.io", "ImageKit"),
    ("bunnycdn.com", "BunnyCDN"),
    ("b-cdn.net", "BunnyCDN"),
    ("kxcdn.com", "KeyCDN"),
    ("jsdelivr.net", "jsDelivr"),
    ("googleusercontent.com", "Google CDN"),
    ("wp.com", "WordPress.com CDN (Jetpack/Photon)"),
    ("wordpress.com", "WordPress.com CDN"),
    ("shopify.com", "Shopify CDN"),
    ("squarespace-cdn.com", "Squarespace CDN"),
    ("vercel.app", "Vercel CDN"),
    ("netlify.app", "Netlify CDN"),
]

# Bad-filename regexes
_FILENAME_BAD = [
    (re.compile(r"^IMG[-_]?\d{3,}", re.I), "generic camera filename (IMG_NNNN)"),
    (re.compile(r"^DSC[-_]?\d{3,}", re.I), "generic camera filename (DSC_NNNN)"),
    (re.compile(r"^DSCN?\d{3,}", re.I), "generic camera filename (DSCN)"),
    (re.compile(r"^P\d{7,}", re.I), "Olympus-style camera filename"),
    (re.compile(r"^[0-9a-f]{16,}$", re.I), "hex string (likely auto-generated UUID/hash)"),
    (re.compile(r"^\d{10,}$"), "pure-numeric (likely timestamp or hash)"),
    (re.compile(r"^(image|picture|photo|untitled)\d*$", re.I), "generic placeholder name"),
]

# Filename quality: bigger size band for LCP detection
LCP_DIMENSION_THRESHOLD = 800  # px — images this wide or wider are LCP-candidates

# File-size tier thresholds (in bytes)
SIZE_TIERS = {
    "thumbnail": {"warn": 100_000, "critical": 200_000},
    "content":   {"warn": 200_000, "critical": 500_000},
    "hero":      {"warn": 300_000, "critical": 700_000},
}


# ---------------------------------------------------------------------------
# Findings
# ---------------------------------------------------------------------------

SEVERITIES = ("critical", "recommended", "nice-to-have", "info")
SEVERITY_WEIGHT = {"critical": 10, "recommended": 3, "nice-to-have": 1, "info": 0}


@dataclass
class Finding:
    severity: str
    category: str  # alt | format | responsive | lazy | lcp | filename | cdn | size
    title: str
    detail: str
    why: str
    fix: str


@dataclass
class ImageRecord:
    """Per-image extracted data."""
    src: str
    alt: Optional[str] = None
    width: Optional[int] = None
    height: Optional[int] = None
    loading: Optional[str] = None
    lazy_method: str = "none"
    fetchpriority: Optional[str] = None
    decoding: Optional[str] = None
    srcset: Optional[str] = None
    sizes_attr: Optional[str] = None
    inside_picture: bool = False
    picture_sources: list[str] = field(default_factory=list)  # MIME types of <source> siblings
    position: int = 0  # zero-based document order
    is_lcp_candidate: bool = False

    # File-size probe (filled in by audit_sizes)
    byte_size: Optional[int] = None
    content_type: Optional[str] = None
    size_probe_error: Optional[str] = None

    @property
    def format(self) -> Optional[str]:
        if self.content_type:
            ct = self.content_type.lower().split(";")[0].strip()
            if ct.startswith("image/"):
                fmt = ct[len("image/"):]
                return fmt.upper() if fmt != "jpeg" else "JPEG"
        ext = Path(urlparse(self.src).path).suffix.lower()
        return EXTENSION_FORMAT.get(ext)

    @property
    def filename(self) -> str:
        return Path(urlparse(self.src).path).name

    @property
    def category(self) -> str:
        """thumbnail / content / hero based on declared dimensions."""
        w = self.width or 0
        if w == 0:
            return "content"  # default when unknown
        if w < 200:
            return "thumbnail"
        if w >= LCP_DIMENSION_THRESHOLD:
            return "hero"
        return "content"


@dataclass
class AuditResult:
    findings: list[Finding] = field(default_factory=list)
    strengths: list[str] = field(default_factory=list)
    stats: dict = field(default_factory=dict)
    images: list[ImageRecord] = field(default_factory=list)

    def add(self, f: Finding) -> None:
        self.findings.append(f)

    def by_category(self, cat: str) -> list[Finding]:
        return [f for f in self.findings if f.category == cat]

    def score(self) -> int:
        deduction = sum(SEVERITY_WEIGHT[f.severity] for f in self.findings)
        return max(0, 100 - deduction)


# ---------------------------------------------------------------------------
# Fetch helpers (same pattern as other audits)
# ---------------------------------------------------------------------------

def _is_pyodide() -> bool:
    return sys.platform == "emscripten"


async def _fetch_pyodide(url: str, want_headers: bool = False) -> Optional[dict]:
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
            try: body_bytes = gzip.decompress(body_bytes)
            except OSError: pass
        elif encoding == "deflate":
            try: body_bytes = zlib.decompress(body_bytes)
            except zlib.error:
                try: body_bytes = zlib.decompress(body_bytes, -zlib.MAX_WBITS)
                except zlib.error: pass
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
# Image extraction
# ---------------------------------------------------------------------------

def _parse_int(v) -> Optional[int]:
    if v is None:
        return None
    try:
        return int(str(v).strip().rstrip("px").strip())
    except (ValueError, AttributeError):
        return None


def extract_images(html: str, base_url: str) -> list[ImageRecord]:
    """Walk the document and produce an ImageRecord per <img>.

    Captures <picture> context so we can check format-fallback chains. Order is
    document order — used downstream for LCP-candidate inference.
    """
    soup = BeautifulSoup(html, "html.parser")
    records: list[ImageRecord] = []
    position = 0

    for img in soup.find_all("img"):
        # Resolve src against base URL. For lazy-loaded images, the real URL
        # is often in a data-* attribute, not src.
        src = (img.get("src") or "").strip()
        if not src:
            # Try lazy-loader attributes
            for attr in ("data-src", "data-lazy-src", "data-original",
                          "data-perfmatters-src", "data-ewww-src"):
                v = (img.get(attr) or "").strip()
                if v:
                    src = v
                    break
        if not src:
            continue
        # Skip data: URIs — inline images aren't separately optimizable
        if src.startswith("data:"):
            continue
        absolute = urljoin(base_url, src)

        rec = ImageRecord(
            src=absolute,
            alt=img.get("alt"),  # may be None (no attr), "" (empty), or string
            width=_parse_int(img.get("width")),
            height=_parse_int(img.get("height")),
            loading=(img.get("loading") or "").lower() or None,
            lazy_method=detect_lazy_method(img),
            fetchpriority=(img.get("fetchpriority") or "").lower() or None,
            decoding=(img.get("decoding") or "").lower() or None,
            srcset=img.get("srcset"),
            sizes_attr=img.get("sizes"),
            position=position,
        )

        # Check if inside a <picture> with format-fallback sources
        parent = img.parent
        if parent is not None and parent.name == "picture":
            rec.inside_picture = True
            for source in parent.find_all("source"):
                t = source.get("type", "").strip()
                if t:
                    rec.picture_sources.append(t)

        records.append(rec)
        position += 1

    # Mark the LCP candidate: the FIRST image whose declared width is at or
    # above the LCP threshold. Falls back to the first image with the largest
    # declared width when no image clears the threshold.
    if records:
        wide = [r for r in records if (r.width or 0) >= LCP_DIMENSION_THRESHOLD]
        if wide:
            wide[0].is_lcp_candidate = True
        else:
            # Fallback: first image overall — without width data we can't pick better
            largest = max(records, key=lambda r: (r.width or 0))
            largest.is_lcp_candidate = True

    return records


# ---------------------------------------------------------------------------
# Audit passes
# ---------------------------------------------------------------------------

def _looks_like_filename_alt(alt: str, filename: str) -> bool:
    """True if alt is essentially the filename (with or without extension)."""
    alt_clean = alt.strip().lower().replace(" ", "").replace("_", "").replace("-", "")
    name = Path(filename).stem.lower().replace("_", "").replace("-", "")
    return alt_clean == name or alt_clean == filename.lower()


def audit_alt_text(images: list[ImageRecord], result: AuditResult) -> None:
    if not images:
        return

    missing = []           # alt attribute entirely absent
    empty_decorative = []  # alt="" — fine for decorative, but flag if seems content-bearing
    too_long = []          # alt > 125 chars
    filename_alt = []      # alt is essentially the filename
    keyword_stuffing = []  # same word 3+ times
    generic = []           # alt = "image", "picture", "photo"

    GENERIC_ALT = {"image", "picture", "photo", "img", "logo"}

    for r in images:
        if r.alt is None:
            missing.append(r)
            continue
        alt = r.alt.strip()
        if alt == "":
            # Empty alt is the correct form for decorative images — only flag
            # if the image is large (likely content) and inside a likely-content position.
            if (r.width or 0) >= 200:
                empty_decorative.append(r)
            continue
        if len(alt) > 125:
            too_long.append(r)
        if alt.lower() in GENERIC_ALT:
            generic.append(r)
            continue
        if _looks_like_filename_alt(alt, r.filename):
            filename_alt.append(r)
            continue
        words = [w.lower() for w in re.findall(r"\w+", alt) if len(w) > 3]
        if words:
            top = Counter(words).most_common(1)[0]
            if top[1] >= 3 and len(words) <= 10:
                keyword_stuffing.append(r)

    total = len(images)
    with_alt = sum(1 for r in images if r.alt is not None and r.alt.strip())
    coverage = with_alt / total
    result.stats["alt_coverage"] = round(coverage, 3)

    if missing:
        sample = ", ".join(r.filename[:40] for r in missing[:5])
        sev = "critical" if len(missing) >= max(1, total // 2) else "recommended"
        result.add(Finding(sev, "alt",
            f"Missing alt attribute on {len(missing)}/{total} image(s)",
            f"Images without an alt attribute (not even alt=''): {sample}.",
            "Search engines and AI engines parse alt text to understand image content. Missing alt means the image contributes nothing to topical signals — and the page also fails WCAG 1.1.1 (Non-text Content).",
            "Add descriptive alt text on every content image. For decorative images (icons, dividers, design flourishes), use alt='' explicitly to mark them skip-worthy to screen readers.",
        ))
    elif coverage >= 0.95:
        result.strengths.append(f"Strong alt-text coverage ({coverage:.0%} of {total} image(s))")

    if empty_decorative:
        sample = ", ".join(r.filename[:40] for r in empty_decorative[:3])
        result.add(Finding("nice-to-have", "alt",
            f"Empty alt on large image(s) ({len(empty_decorative)})",
            f"alt='' but image is ≥200px wide — usually content rather than decoration: {sample}.",
            "Empty alt is correct for decorative images, but a 200px+ image is almost always content. Empty alt on content images costs the same as missing alt.",
            "Verify each: if decorative, keep alt=''. If content, add descriptive alt text.",
        ))

    if filename_alt:
        sample = "; ".join(f"alt={r.alt!r} (file {r.filename[:30]})" for r in filename_alt[:3])
        result.add(Finding("recommended", "alt",
            f"Alt text equals filename on {len(filename_alt)} image(s)",
            f"Examples: {sample}.",
            "Filename-as-alt provides no descriptive value — search engines extract no extra signal beyond the filename itself, which they already have.",
            "Replace with descriptive alt text: 'blue running shoes' instead of 'blue-running-shoes.jpg'.",
        ))

    if generic:
        sample = "; ".join(f"alt={r.alt!r}" for r in generic[:3])
        result.add(Finding("recommended", "alt",
            f"Generic alt text on {len(generic)} image(s)",
            f"Alt is a single generic word (image/picture/photo/logo): {sample}.",
            "Generic words provide no topical or accessibility value. Screen reader users hear 'image' with no further context.",
            "Replace with a description of what the image actually shows or represents.",
        ))

    if too_long:
        result.add(Finding("nice-to-have", "alt",
            f"Alt text >125 chars on {len(too_long)} image(s)",
            f"Long alt slows screen-reader navigation; some tools truncate.",
            "Alt text is announced verbatim by screen readers. Long alt prolongs every navigation through the image.",
            "Trim to ≤125 characters. If you need more context, use the surrounding caption/figcaption or aria-describedby.",
        ))

    if keyword_stuffing:
        sample = "; ".join(f"alt={r.alt!r}" for r in keyword_stuffing[:3])
        result.add(Finding("recommended", "alt",
            f"Keyword stuffing in alt text on {len(keyword_stuffing)} image(s)",
            f"Same word repeated 3+ times in alt: {sample}.",
            "Search engines penalize repeated keywords in alt text — it's a classic over-optimization pattern.",
            "Write alt as a natural description, not a keyword list. Vary phrasing across multiple images on the same page.",
        ))


def audit_format(images: list[ImageRecord], result: AuditResult) -> None:
    if not images:
        return
    legacy = []      # JPEG/PNG/GIF without <picture> fallback
    modern_count = 0
    no_picture = []  # legacy-format images NOT inside <picture> with modern source

    for r in images:
        fmt = r.format
        if fmt in MODERN_FORMATS:
            modern_count += 1
            continue
        if fmt in ("JPEG", "PNG", "GIF"):
            legacy.append(r)
            has_modern_source = any(
                t in ("image/avif", "image/webp")
                for t in r.picture_sources
            )
            if not has_modern_source:
                no_picture.append(r)

    total = len(images)
    if modern_count:
        result.stats["modern_format_count"] = modern_count

    if no_picture and len(no_picture) >= max(2, total // 4):
        sample = ", ".join(r.filename[:30] for r in no_picture[:3])
        result.add(Finding("recommended", "format",
            f"{len(no_picture)} legacy-format image(s) without modern fallback",
            f"JPEG/PNG/GIF served directly without a <picture> wrapper offering AVIF or WebP. Examples: {sample}.",
            "Modern formats (AVIF ~50% smaller than JPEG, WebP ~30% smaller) directly improve LCP and bandwidth. Browser support is ~93% (AVIF) and ~97% (WebP); the <picture> element handles fallback automatically.",
            "Wrap legacy images in <picture> with <source type='image/avif'> and <source type='image/webp'> before the <img> fallback. Server-side conversion (cwebp, sharp, ImageMagick) handles the encoding.",
        ))
    elif modern_count >= total * 0.8:
        result.strengths.append(f"Modern image formats used ({modern_count}/{total} are WebP/AVIF/SVG)")


def audit_responsive(images: list[ImageRecord], result: AuditResult) -> None:
    """Check srcset and sizes presence — important for mobile bandwidth."""
    if not images:
        return
    no_srcset = [r for r in images if not r.srcset and (r.width or 0) >= 400]
    if no_srcset and len(no_srcset) >= max(2, len(images) // 3):
        sample = ", ".join(r.filename[:30] for r in no_srcset[:3])
        result.add(Finding("nice-to-have", "responsive",
            f"{len(no_srcset)} large image(s) without srcset",
            f"Images ≥400px wide served without a srcset providing smaller variants. Examples: {sample}.",
            "Mobile devices on slow networks download the full-size image when no srcset is provided. The browser can't choose a smaller variant.",
            "Add srcset with 2-3 widths and a sizes attribute matching your layout. Example: srcset='img-400w.webp 400w, img-800w.webp 800w, img-1200w.webp 1200w' sizes='(max-width: 600px) 400px, 800px'.",
        ))


def audit_lazy(images: list[ImageRecord], result: AuditResult) -> None:
    """Lazy-loading check — focuses on below-fold images NOT being lazy and
    the LCP candidate NOT being lazy."""
    if not images:
        return

    lazy_counts = Counter(r.lazy_method for r in images)
    result.stats["lazy_method_breakdown"] = dict(lazy_counts)
    total = len(images)
    lazy_total = total - lazy_counts.get("none", 0)

    # LCP image should NOT be lazy-loaded
    lcp_lazy = [r for r in images if r.is_lcp_candidate and r.lazy_method != "none"]
    if lcp_lazy:
        for r in lcp_lazy:
            result.add(Finding("critical", "lcp",
                f"LCP candidate is lazy-loaded ({r.lazy_method})",
                f"Image at position {r.position} (width={r.width or '?'}px) is the LCP candidate but is lazy-loaded via {r.lazy_method}.",
                "Lazy-loading the LCP image directly harms LCP scores. The browser delays the fetch until the image is in viewport, which by definition is when it would have rendered anyway.",
                f"Remove loading='lazy' (and any data-* lazy attribute) from this image. Add fetchpriority='high' instead to signal it as the priority resource.",
            ))

    # Below-fold images that should be lazy
    below_fold_eager = [r for r in images
                         if not r.is_lcp_candidate and r.position >= 3 and r.lazy_method == "none"]
    if below_fold_eager and len(below_fold_eager) >= 3:
        result.add(Finding("recommended", "lazy",
            f"{len(below_fold_eager)} below-fold image(s) not lazy-loaded",
            f"Images at position 3+ (likely below the fold) without loading='lazy' or any JS lazy-loader.",
            "Below-fold images blocking the network during initial load delays everything competing for bandwidth, including the LCP. Eager-loaded images that aren't yet visible give no UX benefit but cost LCP.",
            "Add loading='lazy' on every below-fold image. The LCP candidate must stay eager.",
        ))

    if lazy_total >= total * 0.7 and not lcp_lazy:
        result.strengths.append(
            f"{lazy_total}/{total} images lazy-loaded via {sorted(k for k in lazy_counts if k != 'none')}"
        )


def audit_lcp_cls(images: list[ImageRecord], result: AuditResult) -> None:
    """fetchpriority, decoding, width/height attributes."""
    if not images:
        return

    # LCP candidate without fetchpriority="high"
    lcp = [r for r in images if r.is_lcp_candidate]
    for r in lcp:
        if (r.fetchpriority or "").lower() != "high":
            result.add(Finding("recommended", "lcp",
                "LCP candidate missing fetchpriority='high'",
                f"Image at position {r.position} (width={r.width or '?'}px, file={r.filename[:40]}) "
                "is the LCP candidate but has no fetchpriority attribute.",
                "fetchpriority='high' is the modern signal that tells browsers to prioritize a resource in the network queue. For LCP images, this directly improves first-paint timing.",
                f"Add fetchpriority='high' to this <img>. Pair with no lazy-loading (already required for LCP) and width/height attributes for CLS prevention.",
            ))

    # Non-LCP images without decoding=async
    non_lcp_sync = [r for r in images
                     if not r.is_lcp_candidate and (r.decoding or "") not in ("async", "lazy")]
    if non_lcp_sync and len(non_lcp_sync) >= 3:
        result.add(Finding("nice-to-have", "lcp",
            f"{len(non_lcp_sync)} non-LCP image(s) without decoding='async'",
            f"Images that aren't the LCP candidate and have no decoding='async' attribute.",
            "Synchronous image decoding can block the main thread during render. decoding='async' tells browsers to decode off the main thread.",
            "Add decoding='async' on all non-LCP images. Safe default — no downside for content images.",
        ))

    # Missing width/height attributes (CLS)
    no_dims = [r for r in images if not r.width or not r.height]
    if no_dims and len(no_dims) >= max(2, len(images) // 4):
        sample = ", ".join(r.filename[:30] for r in no_dims[:3])
        result.add(Finding("recommended", "lcp",
            f"{len(no_dims)} image(s) missing width/height attributes",
            f"Images without explicit width and height (and no aspect-ratio CSS detected): {sample}.",
            "Missing dimensions force the browser to lay out the page twice — once before the image loads (zero space), once after (with space). The shift is counted in Cumulative Layout Shift (CLS), a Core Web Vital.",
            "Add width and height attributes to every <img>. Browsers use the ratio to reserve space even when CSS resizes the image. CSS aspect-ratio is an acceptable alternative.",
        ))


def audit_filename(images: list[ImageRecord], result: AuditResult) -> None:
    bad = []
    for r in images:
        stem = Path(r.filename).stem
        for pattern, reason in _FILENAME_BAD:
            if pattern.match(stem):
                bad.append((r, reason))
                break
    if bad and len(bad) >= max(2, len(images) // 4):
        sample = "; ".join(f"{r.filename[:30]} ({reason})" for r, reason in bad[:3])
        result.add(Finding("nice-to-have", "filename",
            f"{len(bad)} image(s) with non-descriptive filenames",
            f"Examples: {sample}.",
            "Filenames are a weak ranking signal but a free one — descriptive filenames provide keyword context to image-search crawlers and aid file management.",
            "Rename to descriptive, hyphenated names: 'blue-running-shoes.webp' not 'IMG_1234.jpg'. Lowercase, no special characters.",
        ))


def audit_cdn(images: list[ImageRecord], page_host: str, result: AuditResult) -> None:
    if not images:
        return
    cdn_hits: dict[str, int] = defaultdict(int)
    same_host = 0
    other_external = 0
    for r in images:
        host = urlparse(r.src).netloc.lower()
        if not host or host == page_host:
            same_host += 1
            continue
        matched = False
        for pattern, label in CDN_PATTERNS:
            if pattern in host:
                cdn_hits[label] += 1
                matched = True
                break
        if not matched:
            other_external += 1

    result.stats["images_same_host"] = same_host
    result.stats["images_other_external"] = other_external
    if cdn_hits:
        result.stats["cdn_usage"] = dict(cdn_hits)
        labels = ", ".join(f"{k}×{v}" for k, v in cdn_hits.items())
        result.strengths.append(f"CDN in use for images: {labels}")
    elif same_host >= len(images) * 0.8 and len(images) >= 5:
        result.add(Finding("nice-to-have", "cdn",
            "Images served from origin host (no CDN detected)",
            f"{same_host}/{len(images)} images come from the same host as the page; no known CDN hostnames matched.",
            "Serving images from the origin uses application bandwidth and gives no edge-cache benefit. CDNs deliver images from the closest geographic edge, dramatically improving LCP for distant users.",
            "Consider Cloudflare, Cloudinary, ImageKit, Bunny CDN, or your hosting provider's image CDN. For WordPress sites, Jetpack Photon or a static-export CDN can do this without code changes.",
        ))


# ---------------------------------------------------------------------------
# File-size HEAD probe (optional)
# ---------------------------------------------------------------------------

async def audit_sizes(images: list[ImageRecord], result: AuditResult,
                       concurrency: int = 8) -> None:
    """Async HEAD-probe every image URL for Content-Length and Content-Type.
    Updates each ImageRecord and emits findings for over-tier images."""
    if not images:
        return
    sem = asyncio.Semaphore(concurrency)

    async def probe(r: ImageRecord) -> None:
        async with sem:
            head = await fetch(r.src, want_headers=True)
        if not head:
            r.size_probe_error = "fetch returned no response"
            return
        status = head.get("status", 0)
        if status >= 400:
            r.size_probe_error = f"HTTP {status}"
            return
        headers = head.get("headers", {}) or {}
        # Case-insensitive lookup
        cl = None
        ct = None
        for k, v in headers.items():
            kl = k.lower()
            if kl == "content-length" and cl is None:
                try:
                    cl = int(v)
                except (ValueError, TypeError):
                    pass
            elif kl == "content-type" and ct is None:
                ct = v
        r.byte_size = cl
        r.content_type = ct

    print(f"  sizes: HEAD-probing {len(images)} image(s) (concurrency={concurrency})…",
          file=sys.stderr)
    await asyncio.gather(*(probe(r) for r in images))

    # Emit findings: over-tier sizes
    over_critical = []
    over_warn = []
    probe_failed = 0
    for r in images:
        if r.size_probe_error:
            probe_failed += 1
            continue
        if r.byte_size is None:
            continue
        thresholds = SIZE_TIERS[r.category]
        if r.byte_size > thresholds["critical"]:
            over_critical.append(r)
        elif r.byte_size > thresholds["warn"]:
            over_warn.append(r)

    result.stats["size_probe_completed"] = sum(1 for r in images if r.byte_size is not None)
    result.stats["size_probe_failed"] = probe_failed

    if over_critical:
        sample = "; ".join(
            f"{r.filename[:30]} ({r.byte_size // 1024}KB, {r.category} tier)"
            for r in over_critical[:5]
        )
        result.add(Finding("recommended", "size",
            f"{len(over_critical)} image(s) over critical size threshold",
            f"Examples: {sample}.",
            "Oversized images directly hurt LCP, mobile data costs, and CWV-driven ranking. Hero images >700KB, content images >500KB, and thumbnails >200KB are well past the point of diminishing returns.",
            "Compress to the tier-appropriate target (hero <200KB, content <100KB, thumbnail <50KB). Switching to WebP/AVIF typically halves file size at the same visual quality.",
        ))
    if over_warn:
        sample = "; ".join(
            f"{r.filename[:30]} ({r.byte_size // 1024}KB)"
            for r in over_warn[:3]
        )
        result.add(Finding("nice-to-have", "size",
            f"{len(over_warn)} image(s) over warning size threshold",
            f"Examples: {sample}.",
            "Above warning but below critical — still worth optimizing. Compression here is the easiest LCP win.",
            "Re-encode at lower quality or switch format. Aim for the tier target (hero <200KB, content <100KB).",
        ))


# ---------------------------------------------------------------------------
# Report rendering
# ---------------------------------------------------------------------------

def render_report(result: AuditResult, source: str) -> str:
    score = result.score()
    counts = Counter(f.severity for f in result.findings)

    lines = [
        "# Image Audit Report",
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

    if result.images:
        with_alt = sum(1 for r in result.images if r.alt is not None and r.alt.strip())
        with_dims = sum(1 for r in result.images if r.width and r.height)
        lazy_total = sum(1 for r in result.images if r.lazy_method != "none")
        modern_fmt = sum(1 for r in result.images if r.format in MODERN_FORMATS)
        sized = sum(1 for r in result.images if r.byte_size is not None)
        lines.append("## Summary")
        lines.append("")
        lines.append("| Metric | Count |")
        lines.append("|---|---|")
        lines.append(f"| Total images | {len(result.images)} |")
        lines.append(f"| With alt text | {with_alt} |")
        lines.append(f"| With width+height attrs | {with_dims} |")
        lines.append(f"| Lazy-loaded (any method) | {lazy_total} |")
        lines.append(f"| Modern format (WebP/AVIF/SVG) | {modern_fmt} |")
        lines.append(f"| Size probed | {sized} |")
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
        for f in sorted(result.findings, key=lambda x: (sev_order[x.severity], x.category)):
            lines.append(f"### [{f.severity.upper()}] {f.title}  *({f.category})*")
            lines.append("")
            lines.append(f"**What:** {f.detail}")
            lines.append("")
            lines.append(f"**Why it matters:** {f.why}")
            lines.append("")
            lines.append(f"**Fix:** {f.fix}")
            lines.append("")

    if result.images:
        lines.append("## Per-image detail")
        lines.append("")
        lines.append("| # | filename | format | dims | lazy | size | alt? | LCP |")
        lines.append("|---|---|---|---|---|---|---|---|")
        for r in result.images[:200]:  # cap to keep report readable
            fmt = r.format or "?"
            dims = f"{r.width}×{r.height}" if r.width and r.height else "—"
            size = f"{r.byte_size // 1024}KB" if r.byte_size else "—"
            alt = "✓" if (r.alt is not None and r.alt.strip()) else ("∅" if r.alt == "" else "✗")
            lcp = "★" if r.is_lcp_candidate else ""
            lines.append(f"| {r.position} | {r.filename[:35]} | {fmt} | {dims} | {r.lazy_method} | {size} | {alt} | {lcp} |")
        if len(result.images) > 200:
            lines.append(f"| _… {len(result.images) - 200} more_ |||||||||")
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

async def amain(args) -> int:
    DEFAULT_OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    # Fetch the input page
    result_dict = await fetch(args.input)
    if not result_dict:
        print(f"ERROR: could not fetch {args.input}", file=sys.stderr)
        return 1
    body = result_dict.get("body", "")
    final_url = result_dict.get("final_url") or args.input
    if not body:
        print(f"ERROR: empty body from {args.input}", file=sys.stderr)
        return 1

    images = extract_images(body, final_url)
    page_host = urlparse(final_url).netloc.lower()

    result = AuditResult()
    result.images = images
    result.stats["total_images"] = len(images)
    result.stats["page_url"] = final_url

    if not images:
        result.add(Finding("info", "alt",
            "No <img> elements found on the page",
            "The HTML contains no <img> tags. The page may render images via CSS background-image (not auditable) or be image-free.",
            "Not necessarily a problem — text-only pages don't need images.",
            "If you expected images, check whether they're rendered via CSS or loaded by JavaScript after page load.",
        ))
    else:
        # Run all the in-HTML checks
        audit_alt_text(images, result)
        audit_format(images, result)
        audit_responsive(images, result)
        audit_lazy(images, result)
        audit_lcp_cls(images, result)
        audit_filename(images, result)
        audit_cdn(images, page_host, result)

        # File-size probe (async, optional)
        if not args.skip_sizes:
            await audit_sizes(images, result, concurrency=args.concurrency)
            # Re-run format check now that content-type may have updated r.format
            # (caught e.g. .jpg URLs serving WebP via Content-Type)

    report = render_report(result, args.input)
    if args.stdout:
        sys.stdout.write(report)
    else:
        out_path = args.output if args.output else (DEFAULT_OUTPUT_DIR / "IMAGE-AUDIT.md")
        if out_path.is_dir():
            out_path = out_path / "IMAGE-AUDIT.md"
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(report, encoding="utf-8")
        print(f"Wrote {out_path} (score: {result.score()}/100, {len(images)} image(s), {len(result.findings)} finding(s))",
              file=sys.stderr)
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Image SEO and performance audit.")
    parser.add_argument("input", nargs="?",
        help="URL to audit. Required unless --url is passed. Accepts the URL positionally — no flag needed.")
    parser.add_argument("--url", dest="url_alias", default=None,
        help="Alias for the positional URL. Use when the caller cannot emit positional argv.")
    parser.add_argument("--skip-sizes", action="store_true",
        help="Skip the file-size HEAD probes (faster — HTML-only audit).")
    parser.add_argument("--concurrency", type=int, default=8,
        help="Size-probe concurrency (default: 8).")
    parser.add_argument("-o", "--output", type=Path, default=None,
        help="Output path (file or directory). Default: ~/Documents/synergyAI/outputs/ "
             "(or /outputs/ in Pyodide).")
    parser.add_argument("--stdout", action="store_true",
        help="Print to stdout instead of writing a file.")
    parser.add_argument("--source", default=None,
        help="Source URL label when input is a local file (bridge pre-fetch case).")
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
