#!/usr/bin/env python3
"""
schema_audit.py — Schema.org structured-data audit for the geo-schema skill.

Detects JSON-LD / Microdata / RDFa, validates each entity against type-specific
required + recommended properties, classifies sameAs URLs across 14 entity
platforms, flags deprecated @types, and reports speakable + knowsAbout signals.

Companion to `schema-generate` (which produces fresh JSON-LD). This script
ONLY audits; it does not generate.

Pyodide-safe: routes the fetch through /gpt/backend/api/v1/fetch-url under
Pyodide; uses urllib directly under native CPython.

Usage:
    python schema_audit.py https://example.com/blog/my-post
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

# Type-specific required + recommended properties (matches SKILL.md table).
TYPE_PROPS: dict[str, dict[str, list[str]]] = {
    "Organization": {
        "required": ["name", "url"],
        "recommended": ["logo", "sameAs", "description", "foundingDate",
                         "founder", "address", "contactPoint", "knowsAbout"],
    },
    "Corporation": {
        "required": ["name", "url"],
        "recommended": ["logo", "sameAs", "description", "foundingDate",
                         "address", "knowsAbout"],
    },
    "LocalBusiness": {
        "required": ["name", "url", "address", "telephone"],
        "recommended": ["openingHoursSpecification", "geo", "priceRange",
                         "aggregateRating", "sameAs", "logo"],
    },
    "Article": {
        "required": ["headline", "datePublished", "author"],
        "recommended": ["dateModified", "image", "publisher", "speakable"],
    },
    "NewsArticle": {
        "required": ["headline", "datePublished", "author"],
        "recommended": ["dateModified", "image", "publisher", "speakable"],
    },
    "BlogPosting": {
        "required": ["headline", "datePublished", "author"],
        "recommended": ["dateModified", "image", "publisher", "speakable"],
    },
    "TechArticle": {
        "required": ["headline", "datePublished", "author"],
        "recommended": ["dateModified", "image", "publisher", "speakable"],
    },
    "Person": {
        "required": ["name"],
        "recommended": ["url", "sameAs", "jobTitle", "worksFor", "knowsAbout",
                         "alumniOf", "award", "description", "image"],
    },
    "Product": {
        "required": ["name", "image", "offers"],
        "recommended": ["description", "brand", "sku", "aggregateRating",
                         "review", "category"],
    },
    "WebSite": {
        "required": ["name", "url"],
        "recommended": ["potentialAction"],
    },
    "FAQPage": {
        "required": ["mainEntity"],
        "recommended": [],
    },
    "QAPage": {
        "required": ["mainEntity"],
        "recommended": [],
    },
    "SoftwareApplication": {
        "required": ["name", "applicationCategory"],
        "recommended": ["operatingSystem", "offers", "aggregateRating",
                         "featureList", "screenshot", "softwareVersion"],
    },
    "BreadcrumbList": {
        "required": ["itemListElement"],
        "recommended": [],
    },
    "WebPage": {
        "required": ["name"],
        "recommended": ["description", "url", "speakable"],
    },
    "Event": {
        "required": ["name", "startDate", "location"],
        "recommended": ["description", "endDate", "organizer", "performer",
                         "offers"],
    },
    "VideoObject": {
        "required": ["name", "description", "thumbnailUrl", "uploadDate"],
        "recommended": ["contentUrl", "embedUrl", "duration"],
    },
}

# Deprecated / restricted types (matches schema-generate's audit.py).
DEPRECATED_TYPES = {
    "HowTo": "rich results removed September 2023",
    "SpecialAnnouncement": "deprecated July 2025",
    "CourseInfo": "retired June 2025",
    "EstimatedSalary": "retired June 2025",
    "LearningVideo": "retired June 2025",
    "ClaimReview": "retired from rich results June 2025",
    "VehicleListing": "retired from rich results June 2025",
    "PracticeProblem": "retired late 2025",
}
RESTRICTED_TYPES = {
    "FAQPage": "Google rich-result eligibility restricted to government / "
                "healthcare since August 2023. Still useful for AI extraction.",
    "QAPage": "Community-Q&A (Stack-Overflow-style) only for Google rich "
                "results. Still useful for AI extraction.",
}

# 14 sameAs platforms tracked.
SAMEAS_PLATFORMS = {
    "Wikipedia": ("wikipedia.org",),
    "Wikidata": ("wikidata.org",),
    "LinkedIn": ("linkedin.com",),
    "YouTube": ("youtube.com", "youtu.be"),
    "Twitter/X": ("twitter.com", "x.com"),
    "Facebook": ("facebook.com", "fb.com"),
    "Instagram": ("instagram.com",),
    "GitHub": ("github.com",),
    "Crunchbase": ("crunchbase.com",),
    "Google Scholar": ("scholar.google.com",),
    "ORCID": ("orcid.org",),
    "Apple App Store": ("apps.apple.com",),
    "Google Play": ("play.google.com",),
    "BBB": ("bbb.org",),
}


# ---------------------------------------------------------------------------
# Fetch helpers (dual Pyodide/CPython)
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
# Schema extraction
# ---------------------------------------------------------------------------

def _entity_type(entity: dict) -> str | None:
    """Return the first @type value as a string, or None."""
    t = entity.get("@type")
    if isinstance(t, list):
        return str(t[0]) if t else None
    if isinstance(t, str):
        return t
    return None


def _flatten_jsonld(data, into: list[dict]) -> None:
    """Walk a JSON-LD payload, collecting every dict-shaped entity (including
    those inside @graph or nested under properties like author/publisher).
    """
    if isinstance(data, list):
        for item in data:
            _flatten_jsonld(item, into)
        return
    if not isinstance(data, dict):
        return
    # Add this entity if it carries an @type.
    if data.get("@type") is not None:
        into.append(data)
    # Walk @graph specifically.
    graph = data.get("@graph")
    if isinstance(graph, list):
        for g in graph:
            _flatten_jsonld(g, into)
    # Walk inner objects on properties commonly holding nested entities.
    for key in ("author", "publisher", "creator", "founder", "isPartOf",
                "mainEntity", "mainEntityOfPage", "about", "subjectOf",
                "image", "logo", "review", "offers", "brand", "itemListElement",
                "potentialAction", "speakable"):
        val = data.get(key)
        if isinstance(val, dict) and val.get("@type"):
            _flatten_jsonld(val, into)
        elif isinstance(val, list):
            for v in val:
                if isinstance(v, dict) and v.get("@type"):
                    _flatten_jsonld(v, into)


def extract_jsonld_blocks(soup: BeautifulSoup) -> tuple[list[dict], list[str]]:
    """Return (entities, parse_warnings). `entities` flattens @graph and nested
    entities so every typed object is reachable for validation.
    """
    entities: list[dict] = []
    warnings: list[str] = []
    scripts = soup.find_all("script", type="application/ld+json")
    for idx, script in enumerate(scripts, 1):
        if not script.string:
            warnings.append(f"JSON-LD block #{idx}: empty <script>")
            continue
        text = script.string
        try:
            data = json.loads(text)
        except json.JSONDecodeError as e:
            warnings.append(f"JSON-LD block #{idx}: invalid JSON — {e.msg} "
                             f"(line {e.lineno}, col {e.colno})")
            continue
        _flatten_jsonld(data, entities)
    return entities, warnings


def collect_sameAs(entities: list[dict]) -> list[str]:
    """Collect every sameAs URL across entities, dedup preserving order."""
    seen: set[str] = set()
    out: list[str] = []
    for e in entities:
        sa = e.get("sameAs")
        items = []
        if isinstance(sa, list):
            items = [str(x) for x in sa if isinstance(x, str)]
        elif isinstance(sa, str):
            items = [sa]
        for u in items:
            if u not in seen:
                seen.add(u)
                out.append(u)
    return out


def classify_sameAs(urls: list[str]) -> dict[str, list[str]]:
    """Group sameAs URLs by platform; `unknown` collects unmatched URLs."""
    out: dict[str, list[str]] = {p: [] for p in SAMEAS_PLATFORMS}
    out["Other"] = []
    for u in urls:
        host = urlparse(u).netloc.lower()
        matched = False
        for platform, domains in SAMEAS_PLATFORMS.items():
            for d in domains:
                if host == d or host.endswith("." + d):
                    out[platform].append(u)
                    matched = True
                    break
            if matched:
                break
        if not matched:
            out["Other"].append(u)
    return out


def validate_entity(entity: dict) -> dict:
    """Required + recommended property check for a known @type."""
    t = _entity_type(entity)
    props = TYPE_PROPS.get(t) if t else None
    out = {
        "type": t,
        "known": props is not None,
        "missing_required": [],
        "missing_recommended": [],
        "present_required": [],
        "present_recommended": [],
        "deprecated": t in DEPRECATED_TYPES,
        "deprecated_reason": DEPRECATED_TYPES.get(t),
        "restricted": t in RESTRICTED_TYPES,
        "restricted_reason": RESTRICTED_TYPES.get(t),
    }
    if not props:
        return out
    for k in props["required"]:
        if k in entity and entity[k] not in (None, "", [], {}):
            out["present_required"].append(k)
        else:
            out["missing_required"].append(k)
    for k in props["recommended"]:
        if k in entity and entity[k] not in (None, "", [], {}):
            out["present_recommended"].append(k)
        else:
            out["missing_recommended"].append(k)
    return out


def detect_microdata_rdfa(soup: BeautifulSoup) -> dict:
    microdata = len(soup.find_all(attrs={"itemscope": True}))
    rdfa = len(soup.find_all(attrs={"typeof": True}))
    return {"microdata_count": microdata, "rdfa_count": rdfa}


def has_speakable(entities: list[dict]) -> tuple[bool, list[str]]:
    """Returns (any present, list of @types that carry speakable)."""
    types_with: list[str] = []
    for e in entities:
        if "speakable" in e and e["speakable"]:
            t = _entity_type(e)
            if t and t not in types_with:
                types_with.append(t)
    return (len(types_with) > 0, types_with)


def knowsAbout_summary(entities: list[dict]) -> dict:
    out = {"present_on_types": [], "total_topics": 0, "samples": []}
    for e in entities:
        ka = e.get("knowsAbout")
        if not ka:
            continue
        t = _entity_type(e)
        if t and t not in out["present_on_types"]:
            out["present_on_types"].append(t)
        if isinstance(ka, list):
            out["total_topics"] += len(ka)
            for x in ka[:5]:
                if isinstance(x, str) and x not in out["samples"]:
                    out["samples"].append(x)
                elif isinstance(x, dict) and x.get("name"):
                    if x["name"] not in out["samples"]:
                        out["samples"].append(x["name"])
        elif isinstance(ka, str):
            out["total_topics"] += 1
            if ka not in out["samples"]:
                out["samples"].append(ka)
    return out


# ---------------------------------------------------------------------------
# Aggregation + report
# ---------------------------------------------------------------------------

def analyze(html: str, url: str) -> dict:
    soup = BeautifulSoup(html, "html.parser")
    entities, parse_warnings = extract_jsonld_blocks(soup)
    microdata_rdfa = detect_microdata_rdfa(soup)
    same_as_urls = collect_sameAs(entities)
    same_as_by_platform = classify_sameAs(same_as_urls)
    speakable_present, speakable_types = has_speakable(entities)
    ka = knowsAbout_summary(entities)

    validations = [validate_entity(e) for e in entities]
    types_present = []
    for e in entities:
        t = _entity_type(e)
        if t and t not in types_present:
            types_present.append(t)

    return {
        "url": url,
        "format": {
            "jsonld_blocks": len(soup.find_all("script", type="application/ld+json")),
            "jsonld_entities_parsed": len(entities),
            "microdata_elements": microdata_rdfa["microdata_count"],
            "rdfa_elements": microdata_rdfa["rdfa_count"],
            "jsonld_present": len(entities) > 0,
        },
        "types_present": types_present,
        "validations": validations,
        "parse_warnings": parse_warnings,
        "sameAs": {
            "total_unique": len(same_as_urls),
            "urls": same_as_urls,
            "by_platform": same_as_by_platform,
            "platforms_present": [p for p, urls in same_as_by_platform.items()
                                    if urls and p != "Other"],
            "platforms_missing": [p for p in SAMEAS_PLATFORMS
                                    if not same_as_by_platform[p]],
        },
        "speakable": {
            "present": speakable_present,
            "on_types": speakable_types,
        },
        "knowsAbout": ka,
        "deprecated_types_found": [v["type"] for v in validations if v["deprecated"]],
        "restricted_types_found": [v["type"] for v in validations if v["restricted"]],
    }


def render(result: dict) -> str:
    L: list[str] = [
        f"# Schema.org audit: {result['url']}",
        "",
        f"**Fetched:** {result['fetched_at']}",
        "",
        "## Format detection",
        "",
        f"- JSON-LD `<script>` blocks: **{result['format']['jsonld_blocks']}**",
        f"- JSON-LD entities parsed (flattened from @graph + nested): "
        f"**{result['format']['jsonld_entities_parsed']}**",
        f"- Microdata (`itemscope`) elements: {result['format']['microdata_elements']}",
        f"- RDFa (`typeof`) elements: {result['format']['rdfa_elements']}",
        "",
    ]

    if not result["format"]["jsonld_present"]:
        if result["format"]["microdata_elements"] or result["format"]["rdfa_elements"]:
            L += ["⚠️ Only Microdata / RDFa detected — no JSON-LD. AI platforms "
                  "process JSON-LD most reliably. Recommend migrating to JSON-LD; "
                  "call **schema-generate** to produce a starting set.", ""]
        else:
            L += ["⚠️ **No structured data found at all** (no JSON-LD / "
                  "Microdata / RDFa). The page is invisible to AI entity "
                  "extraction. Recommend calling **schema-generate** to "
                  "produce baseline JSON-LD (Organization + WebSite at minimum).",
                  ""]

    if result["parse_warnings"]:
        L += ["## Parse warnings", ""]
        for w in result["parse_warnings"]:
            L.append(f"- ⚠️ {w}")
        L.append("")

    L += [
        "## @types present",
        "",
        f"- {result['types_present'] if result['types_present'] else 'none'}",
        "",
    ]

    if result["deprecated_types_found"]:
        L += [
            "## Deprecated @types — REMOVE OR REPLACE",
            "",
        ]
        for v in result["validations"]:
            if v["deprecated"]:
                L.append(f"- ❌ `{v['type']}` — {v['deprecated_reason']}")
        L.append("")
    if result["restricted_types_found"]:
        L += [
            "## Restricted @types (still useful for AI; no Google rich results)",
            "",
        ]
        for v in result["validations"]:
            if v["restricted"]:
                L.append(f"- ⚠️ `{v['type']}` — {v['restricted_reason']}")
        L.append("")

    # Per-entity validation
    L += ["## Per-entity validation", ""]
    if not result["validations"]:
        L += ["(no entities to validate)", ""]
    for i, v in enumerate(result["validations"], 1):
        L.append(f"### Entity {i}: `{v['type'] or 'UNKNOWN'}`"
                 + (" — type not in known set; skipping required-property check"
                    if not v["known"] else ""))
        if v["known"]:
            if v["missing_required"]:
                L.append(f"- ❌ Missing required: "
                         f"{', '.join(v['missing_required'])}")
            else:
                L.append(f"- ✅ Required present: "
                         f"{', '.join(v['present_required']) or '(none defined)'}")
            if v["missing_recommended"]:
                L.append(f"- ⚠️ Missing recommended: "
                         f"{', '.join(v['missing_recommended'])}")
            if v["present_recommended"]:
                L.append(f"- ✅ Recommended present: "
                         f"{', '.join(v['present_recommended'])}")
        L.append("")

    # sameAs
    sa = result["sameAs"]
    L += [
        "## `sameAs` analysis",
        "",
        f"- Total unique sameAs URLs: **{sa['total_unique']}**",
        f"- Platforms present ({len(sa['platforms_present'])}): "
        f"{', '.join(sa['platforms_present']) or 'none'}",
        f"- Priority platforms MISSING: {', '.join(sa['platforms_missing'])}",
        "",
    ]
    if sa["total_unique"] > 0:
        L.append("URLs by platform:")
        for platform, urls in sa["by_platform"].items():
            if urls:
                L.append(f"- **{platform}** ({len(urls)}):")
                for u in urls[:4]:
                    L.append(f"  - {u}")
                if len(urls) > 4:
                    L.append(f"  - … {len(urls) - 4} more")
        L.append("")

    # speakable + knowsAbout
    sp = result["speakable"]
    ka = result["knowsAbout"]
    L += [
        "## AI-specific signals",
        "",
        f"- `speakable` present: "
        + ("yes — on " + ", ".join(sp['on_types']) if sp['present'] else "NO"),
        f"- `knowsAbout` present: "
        + (f"yes — {ka['total_topics']} topic(s) on "
            f"{', '.join(ka['present_on_types'])}; samples: {ka['samples']}"
           if ka['present_on_types'] else "NO"),
        "",
    ]

    return "\n".join(L).rstrip() + "\n"


# ---------------------------------------------------------------------------
# Output paths
# ---------------------------------------------------------------------------

def resolve_output_paths(output: Path | None) -> tuple[Path, Path]:
    if output is None:
        base = DEFAULT_OUTPUT_DIR
        return base / "GEO-SCHEMA-EXTRACT.md", base / "GEO-SCHEMA-EXTRACT.json"
    if output.suffix == "" or output.is_dir():
        return output / "GEO-SCHEMA-EXTRACT.md", output / "GEO-SCHEMA-EXTRACT.json"
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

    print(f"Auditing schema on {url}…", file=sys.stderr)
    resp = await fetch(url)
    if not resp or not resp.get("body"):
        print(f"ERROR: page fetch failed for {url}", file=sys.stderr)
        return 1

    result = analyze(resp["body"], resp.get("final_url") or url)
    result["fetched_at"] = time.strftime("%Y-%m-%d %H:%M:%S")
    result["http_status"] = resp.get("status", 0)

    report = render(result)

    md_path, json_path = resolve_output_paths(args.output)
    md_path.parent.mkdir(parents=True, exist_ok=True)
    md_path.write_text(report, encoding="utf-8")
    json_path.write_text(json.dumps(result, indent=2, ensure_ascii=False) + "\n",
                         encoding="utf-8")

    # Full audit to stdout — what run_skill_script returns to the model.
    print(report)

    print(f"[geo-schema] jsonld_blocks={result['format']['jsonld_blocks']} "
          f"entities={result['format']['jsonld_entities_parsed']} "
          f"sameAs={result['sameAs']['total_unique']} "
          f"deprecated={len(result['deprecated_types_found'])} "
          f"— wrote {md_path}", file=sys.stderr)
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Audit Schema.org structured data on a page.")
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
