#!/usr/bin/env python3
"""
audit.py — Strategic SEO planning scaffold.

Pipeline:
  1. Detect business type from URL signals (or use --type)
  2. Load the matching industry template from references/
  3. Scaffold a 4-phase implementation roadmap
  4. Emit PLAN.md with industry template + roadmap + cross-skill action items

The script provides STRUCTURE; the LLM fills in SPECIFICS during chat.

Usage:
    python audit.py URL                     # auto-detect business type
    python audit.py URL --type saas         # force type
    python audit.py --type saas --new-project
    python audit.py --list-types
"""

from __future__ import annotations

import os
import argparse
import asyncio
import json
import re
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional
from urllib.parse import urlparse

from bs4 import BeautifulSoup


DEFAULT_OUTPUT_DIR = (
    Path(os.environ.get("SYNERGYAI_OUTPUT_DIR", "/outputs"))
    if sys.platform == "emscripten"
    else Path.home() / "Documents" / "synergyAI" / "outputs"
)
PROXY_ENDPOINT = "/gpt/backend/api/v1/fetch-url"
SCRIPT_DIR = Path(__file__).resolve().parent
SKILL_DIR = SCRIPT_DIR.parent
REFERENCES_DIR = SKILL_DIR / "references"

BROWSER_UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
)

BUSINESS_TYPES = ("saas", "local-service", "ecommerce", "publisher", "agency", "generic")


# ---------------------------------------------------------------------------
# Industry detection — score each business type against page signals
# ---------------------------------------------------------------------------

@dataclass
class TypeScore:
    type_name: str
    score: int = 0
    reasons: list[str] = field(default_factory=list)


# Path-pattern signals + content-cue signals per type.
TYPE_SIGNALS = {
    "saas": {
        "url_paths": ["/pricing", "/features", "/integrations", "/docs", "/api", "/changelog", "/login", "/signup"],
        "content_cues": ["free trial", "start free", "sign up", "request demo", "book a demo", "pricing plans"],
        "schema_types": ["SoftwareApplication", "WebApplication"],
    },
    "local-service": {
        "url_paths": ["/contact", "/location", "/locations", "/service-area", "/visit", "/directions", "/book", "/appointment"],
        "content_cues": ["serving ", "service area", "call us", "book online", "free quote", "free estimate"],
        "schema_types": ["LocalBusiness", "ProfessionalService", "Plumber", "Electrician", "HomeAndConstructionBusiness", "AutoRepair"],
        "extra_signals": ["phone_number", "physical_address"],
    },
    "ecommerce": {
        "url_paths": ["/products", "/product", "/collections", "/collection", "/cart", "/checkout", "/shop", "/store"],
        "content_cues": ["add to cart", "shop now", "buy now", "in stock", "free shipping", "money back"],
        "schema_types": ["Product", "Offer", "AggregateOffer", "Store", "OnlineStore"],
    },
    "publisher": {
        "url_paths": ["/blog", "/article", "/articles", "/news", "/posts", "/topics", "/category", "/categories", "/author", "/authors"],
        "content_cues": ["by ", "published on", "minutes read", "min read", "subscribe to", "newsletter"],
        "schema_types": ["Article", "BlogPosting", "NewsArticle", "NewsMediaOrganization"],
    },
    "agency": {
        "url_paths": ["/case-studies", "/case-study", "/portfolio", "/work", "/clients", "/industries", "/services", "/team"],
        "content_cues": ["our work", "our clients", "case study", "trusted by", "we help", "our process"],
        "schema_types": ["ProfessionalService"],
    },
}

# Generic catch-all has no signals — it's the default when no other type wins.


# Phone-number regex (US-style + international with country code)
_PHONE_RE = re.compile(r"\b\+?\d{1,3}[-.\s]?\(?\d{2,4}\)?[-.\s]?\d{2,4}[-.\s]?\d{2,4}\b")
# Postal address heuristic — number + street type + city/state-ish
_ADDR_RE = re.compile(r"\b\d{1,5}\s+\w+\s+(?:street|st|avenue|ave|road|rd|boulevard|blvd|drive|dr|lane|ln|way|court|ct)\b", re.I)


def detect_business_type(html: str, url: str) -> list[TypeScore]:
    """Score each business type from URL + content signals."""
    scores: dict[str, TypeScore] = {t: TypeScore(t) for t in TYPE_SIGNALS}
    path = urlparse(url).path.lower()

    try:
        soup = BeautifulSoup(html, "html.parser")
    except Exception:
        soup = BeautifulSoup("", "html.parser")

    # Body text for content cues
    body_text = ""
    if soup.find("body"):
        for el in soup(["script", "style", "nav", "header", "footer"]):
            el.decompose()
        body_text = soup.get_text(" ", strip=True).lower()
    body_text_sample = body_text[:50000]

    # Existing JSON-LD types
    declared_schema_types: list[str] = []
    for script in soup.find_all("script", type="application/ld+json"):
        if not script.string:
            continue
        try:
            data = json.loads(script.string)
        except json.JSONDecodeError:
            continue
        items = data if isinstance(data, list) else [data]
        stack = list(items)
        while stack:
            it = stack.pop()
            if not isinstance(it, dict):
                continue
            t = it.get("@type")
            if t:
                if isinstance(t, list):
                    declared_schema_types.extend(t)
                else:
                    declared_schema_types.append(t)
            graph = it.get("@graph")
            if isinstance(graph, list):
                stack.extend(g for g in graph if isinstance(g, dict))

    # Internal links (for path-pattern signal — we don't have a crawl, so use
    # the in-page <a href> links as proxies for the site's URL structure)
    internal_paths: set[str] = set()
    site_host = urlparse(url).netloc.lower()
    for a in soup.find_all("a", href=True):
        href = (a.get("href") or "").strip().lower()
        if not href:
            continue
        # Same-host links (relative or absolute matching site_host)
        if href.startswith(("http://", "https://")):
            p = urlparse(href)
            if p.netloc.lower() == site_host:
                internal_paths.add(p.path)
        elif href.startswith("/"):
            internal_paths.add(href.split("?", 1)[0].split("#", 1)[0])

    # Phone + address signals (boost local-service)
    has_phone = bool(_PHONE_RE.search(body_text_sample))
    has_address = bool(_ADDR_RE.search(body_text_sample))

    for type_name, signals in TYPE_SIGNALS.items():
        ts = scores[type_name]

        # URL pattern match — check both the current URL path AND internal-link paths
        for pat in signals["url_paths"]:
            if pat in path:
                ts.score += 15
                ts.reasons.append(f"URL contains `{pat}`")
                break  # one match per type, not per path

        # Internal-link path match — separate bucket
        matched_internal: list[str] = []
        for pat in signals["url_paths"]:
            for ip in internal_paths:
                if pat in ip:
                    matched_internal.append(pat)
                    break
        if matched_internal:
            ts.score += min(20, 5 * len(matched_internal))
            ts.reasons.append(f"internal links to `{', '.join(matched_internal[:3])}`")

        # Content cues
        cue_hits = sum(1 for cue in signals["content_cues"] if cue in body_text_sample)
        if cue_hits:
            ts.score += min(15, 5 * cue_hits)
            ts.reasons.append(f"{cue_hits} content cue(s) matched")

        # Schema-type match
        matched_schemas = [t for t in signals["schema_types"] if t in declared_schema_types]
        if matched_schemas:
            ts.score += 20
            ts.reasons.append(f"JSON-LD declares {', '.join(matched_schemas)}")

        # Extra local-service signals
        if "extra_signals" in signals:
            if has_phone and "phone_number" in signals["extra_signals"]:
                ts.score += 5
                ts.reasons.append("phone number in body")
            if has_address and "physical_address" in signals["extra_signals"]:
                ts.score += 10
                ts.reasons.append("street address in body")

    # Sort high → low; tie-break by name for determinism
    ranked = sorted(scores.values(), key=lambda t: (-t.score, t.type_name))

    # Always append `generic` as a last-resort fallback
    ranked.append(TypeScore("generic", score=0, reasons=["fallback when no strong type signal"]))
    return ranked


# ---------------------------------------------------------------------------
# Fetch helpers
# ---------------------------------------------------------------------------

def _is_pyodide() -> bool:
    return sys.platform == "emscripten"


async def _fetch_pyodide(url: str) -> Optional[dict]:
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
        text = await resp.string()
        payload = json.loads(text)
    except Exception:
        return None
    if not payload.get("success"):
        return None
    data = payload.get("data", {}) or {}
    return {"body": data.get("html", "") or "", "final_url": data.get("final_url", "") or url}


def _fetch_urllib(url: str) -> Optional[dict]:
    import ssl
    import urllib.request
    import gzip
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    req = urllib.request.Request(url, headers={
        "User-Agent": BROWSER_UA, "Accept": "text/html,*/*", "Accept-Encoding": "gzip",
    })
    try:
        with urllib.request.urlopen(req, timeout=15, context=ctx) as r:
            body = r.read()
            final_url = r.url
            enc = (r.headers.get("Content-Encoding") or "").lower()
    except Exception:
        return None
    if enc == "gzip":
        try: body = gzip.decompress(body)
        except OSError: pass
    try:
        return {"body": body.decode("utf-8", errors="replace"), "final_url": final_url}
    except Exception:
        return None


async def fetch(url: str) -> Optional[dict]:
    if _is_pyodide():
        return await _fetch_pyodide(url)
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(None, _fetch_urllib, url)


# ---------------------------------------------------------------------------
# Plan rendering
# ---------------------------------------------------------------------------

# Quality gates per business type that the plan should call out.
QUALITY_GATES_BY_TYPE = {
    "local-service": [
        "**WARNING at 30+ location pages** — verify ≥60% unique content per page (avoid doorway-page penalty)",
        "**HARD STOP at 50+ location pages** — manual content audit required before scaling further",
        "Each location page must have unique NAP (name/address/phone), local testimonials, location-specific photos",
    ],
    "ecommerce": [
        "**Cannibalization risk** — overlapping product / collection / category pages targeting the same query",
        "**Faceted-navigation index bloat** — block crawl on filter combinations (use rel=canonical or robots.txt)",
        "**Out-of-stock product pages** — don't 404; return 200 with `availability=OutOfStock` schema",
    ],
    "publisher": [
        "**Tag-page bloat** — taxonomy pages can quickly proliferate; merge similar tags and noindex thin ones",
        "**Author E-E-A-T** — every author needs a real bio, credentials, and a Person schema",
        "**Dating signals** — visible publication + last-updated dates on every article",
    ],
    "saas": [
        "**Comparison-page proliferation** — \"X vs Y\" pages must be genuinely useful, not auto-generated lookalikes",
        "**Docs vs blog confusion** — clear separation; docs target navigational/informational, blog targets long-tail informational",
        "**Pricing-page change tracking** — modificationDate matters; pricing pages are revisited",
    ],
    "agency": [
        "**Case-study uniqueness** — each must have a real client outcome, not boilerplate",
        "**Service-page templating** — easy to over-template; each service deserves unique copy",
        "**Industries page bloat** — only create industry pages where you have real client work to point to",
    ],
    "generic": [
        "**Thin content** — pages under 300 words should justify their existence",
        "**Internal linking** — every page should be reachable in ≤3 clicks from homepage",
    ],
}


# Per-phase task scaffolding common across business types.
PHASE_SKELETON = {
    "Phase 1 — Foundation (weeks 1-4)": [
        "Set up Google Search Console + GA4",
        "Configure robots.txt + initial sitemap.xml",
        "Ship core pages: home, about, contact, primary service/product",
        "Implement essential schema (Organization, LocalBusiness, or appropriate type)",
        "Verify HTTPS + basic security headers (CSP, HSTS at minimum)",
        "Mobile viewport meta + width=device-width",
        "Run `ai-search-audit` on the homepage as a baseline",
    ],
    "Phase 2 — Expansion (weeks 5-12)": [
        "Build out the content priorities defined in the industry template (above)",
        "Launch the blog / resource section",
        "Implement internal linking strategy — every important page reachable in ≤3 clicks",
        "Add schema for each new page type (Article / Product / Service / etc.)",
        "Set up `sitemap-audit` cron — verify the sitemap grows correctly",
        "Generate content briefs (`seo-content-brief`) for the first 10 priority articles",
    ],
    "Phase 3 — Scale (weeks 13-24)": [
        "Topic-cluster planning (`topic-cluster`) for the main pillar topics",
        "Generate hub + spoke content per the cluster plans",
        "Begin link-building outreach (HARO, guest posts, partnership content)",
        "Run `image-audit` site-wide — fix LCP images, format conversion",
        "Implement hreflang if expanding to multiple languages (`hreflang-audit`)",
        "GEO optimization — verify AI crawlers (GPTBot, ClaudeBot, PerplexityBot) are allowed in robots.txt; add `/llms.txt`",
    ],
    "Phase 4 — Authority (months 7-12)": [
        "Thought-leadership content — original research, surveys, data-driven posts",
        "PR + media mentions — establish the brand as a citable source",
        "Advanced schema — VideoObject, Event, Course, Certification where applicable",
        "Run quarterly `ai-search-audit` site-wide; track score trends",
        "Run `seo-programmatic` if scaling to many auto-generated pages",
        "Iterate on the content cluster — expand spokes, retire underperformers",
    ],
}


def render_plan(business_type: str, ranked_types: list[TypeScore],
                  template_md: str, site_context: Optional[dict],
                  user_url: Optional[str], new_project: bool) -> str:
    lines = [
        f"# SEO Plan — {business_type}",
        "",
        f"**Generated:** {time.strftime('%Y-%m-%d %H:%M:%S')}",
        f"**Business type:** `{business_type}`",
    ]
    if new_project:
        lines.append("**Mode:** new project (no existing site)")
    elif user_url:
        lines.append(f"**Source URL:** {user_url}")
    lines.append("")

    # Detection table (only when auto-detected)
    if ranked_types and not new_project:
        lines.append("## Business-type detection")
        lines.append("")
        lines.append("| Type | Score | Reasons |")
        lines.append("|---|---|---|")
        for t in ranked_types[:5]:
            reasons = "; ".join(t.reasons) if t.reasons else "—"
            mark = " ← selected" if t.type_name == business_type else ""
            lines.append(f"| `{t.type_name}` | {t.score}{mark} | {reasons} |")
        lines.append("")

    if site_context:
        lines.append("## Site context")
        lines.append("")
        lines.append(f"- Title: {site_context.get('title', '—')!r}")
        lines.append(f"- Meta description: {(site_context.get('meta_description') or '—')[:120]!r}")
        if site_context.get("declared_schema_types"):
            lines.append(f"- Existing schema: {', '.join(site_context['declared_schema_types'])}")
        if site_context.get("og_type"):
            lines.append(f"- OG type: {site_context['og_type']}")
        lines.append("")

    # Industry template
    lines.append(f"## Industry template — {business_type}")
    lines.append("")
    lines.append("_Lifted from `references/" + business_type + ".md`. Tailor every item to the specific business._")
    lines.append("")
    lines.append(template_md)
    lines.append("")

    # 4-phase roadmap
    lines.append("## Implementation roadmap")
    lines.append("")
    for phase, tasks in PHASE_SKELETON.items():
        lines.append(f"### {phase}")
        lines.append("")
        for t in tasks:
            lines.append(f"- [ ] {t}")
        lines.append("")

    # Quality gates
    gates = QUALITY_GATES_BY_TYPE.get(business_type, QUALITY_GATES_BY_TYPE["generic"])
    lines.append("## Quality gates")
    lines.append("")
    for g in gates:
        lines.append(f"- {g}")
    lines.append("")

    # Cross-skill action items
    lines.append("## Recommended next steps (run these skills as you execute the plan)")
    lines.append("")
    lines.append("| Skill | When to run |")
    lines.append("|---|---|")
    lines.append("| `ai-search-audit` | Phase 1 — baseline on homepage; Phase 2 — each new page; Phase 4 — quarterly site-wide |")
    lines.append("| `sitemap-audit` | Phase 1 — validate initial sitemap; Phase 2+ — verify growth |")
    lines.append("| `seo-content-brief` | Phase 2+ — before writing each priority article |")
    lines.append("| `topic-cluster` | Phase 3 — for each pillar topic |")
    lines.append("| `image-audit` | Phase 3 — site-wide image SEO pass |")
    lines.append("| `hreflang-audit` | Phase 3 — only if going multi-language |")
    lines.append("| `schema-generate` | Phase 1 + Phase 4 — when adding new page types |")
    lines.append("| `seo-programmatic` | Phase 4 — if scaling to many auto-generated pages |")
    lines.append("| `serp-shape` | Phase 3+ — check positioning for top-priority keywords |")
    lines.append("")

    return "\n".join(lines).rstrip() + "\n"


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def load_template(business_type: str) -> Optional[str]:
    path = REFERENCES_DIR / f"{business_type}.md"
    if not path.exists():
        return None
    return path.read_text(encoding="utf-8")


def extract_site_context(html: str) -> dict:
    ctx: dict = {}
    try:
        soup = BeautifulSoup(html, "html.parser")
    except Exception:
        return ctx
    if soup.title and soup.title.string:
        ctx["title"] = soup.title.string.strip()
    md = soup.find("meta", attrs={"name": "description"})
    if md and md.get("content"):
        ctx["meta_description"] = md.get("content").strip()
    og = soup.find("meta", attrs={"property": "og:type"})
    if og and og.get("content"):
        ctx["og_type"] = og.get("content").strip()
    types: list[str] = []
    for script in soup.find_all("script", type="application/ld+json"):
        if not script.string:
            continue
        try:
            data = json.loads(script.string)
        except json.JSONDecodeError:
            continue
        items = data if isinstance(data, list) else [data]
        stack = list(items)
        while stack:
            it = stack.pop()
            if not isinstance(it, dict):
                continue
            t = it.get("@type")
            if t:
                if isinstance(t, list):
                    types.extend(t)
                else:
                    types.append(t)
            graph = it.get("@graph")
            if isinstance(graph, list):
                stack.extend(g for g in graph if isinstance(g, dict))
    ctx["declared_schema_types"] = list(dict.fromkeys(types))[:10]
    return ctx


async def amain(args) -> int:
    DEFAULT_OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    # --list-types
    if args.list_types:
        print("Available business types:", file=sys.stdout)
        for t in BUSINESS_TYPES:
            ref = REFERENCES_DIR / f"{t}.md"
            mark = "✓" if ref.exists() else "✗"
            print(f"  [{mark}] {t}", file=sys.stdout)
        return 0

    # Determine business type
    business_type: Optional[str] = args.type
    ranked: list[TypeScore] = []
    site_context: Optional[dict] = None

    if args.new_project:
        if not business_type:
            print("ERROR: --new-project requires --type TYPE", file=sys.stderr)
            return 2
    elif args.input:
        # Fetch the URL and detect industry
        print(f"Fetching {args.input} for industry detection…", file=sys.stderr)
        fetched = await fetch(args.input)
        if not fetched or not fetched.get("body"):
            print(f"ERROR: could not fetch {args.input}", file=sys.stderr)
            return 1
        body = fetched["body"]
        site_context = extract_site_context(body)
        ranked = detect_business_type(body, args.input)
        if not business_type:
            # Pick highest-scoring non-generic, or fall back to generic
            top = [t for t in ranked if t.type_name != "generic" and t.score > 0]
            business_type = top[0].type_name if top else "generic"
            print(f"Detected business type: {business_type}", file=sys.stderr)
            for t in ranked[:5]:
                print(f"  {t.type_name:<18} score={t.score}  ({'; '.join(t.reasons) or 'no signals'})",
                      file=sys.stderr)
    else:
        print("ERROR: provide a URL, --type, or --new-project + --type", file=sys.stderr)
        return 2

    if business_type not in BUSINESS_TYPES:
        print(f"ERROR: unknown business type {business_type!r}. Valid: {', '.join(BUSINESS_TYPES)}",
              file=sys.stderr)
        return 1

    template = load_template(business_type)
    if template is None:
        print(f"ERROR: no industry template found for {business_type!r}", file=sys.stderr)
        return 1

    plan = render_plan(business_type, ranked, template, site_context,
                        args.input, args.new_project)

    out_dir = args.output if args.output else DEFAULT_OUTPUT_DIR
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / f"PLAN-{business_type}.md"
    if args.stdout:
        sys.stdout.write(plan)
    else:
        out_path.write_text(plan, encoding="utf-8")
        print(f"\nWrote {out_path}", file=sys.stderr)
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Strategic SEO planning scaffold.")
    parser.add_argument("input", nargs="?", help="URL to detect business type from.")
    parser.add_argument("--url", dest="url_alias", default=None, help="Alias for positional URL.")
    parser.add_argument("--type", default=None, choices=BUSINESS_TYPES,
        help="Force a business type (skip detection).")
    parser.add_argument("--new-project", action="store_true",
        help="No existing site — skip URL fetch. Requires --type.")
    parser.add_argument("--list-types", action="store_true",
        help="List the 6 supported business types and exit.")
    parser.add_argument("-o", "--output", type=Path, default=None)
    parser.add_argument("--stdout", action="store_true")
    parser.add_argument("--source", default=None, help="Source label for bridge pre-fetch.")
    args = parser.parse_args(argv)

    if not args.input and args.url_alias:
        args.input = args.url_alias
    if args.source and re.match(r"^https?://", args.source, re.I):
        args.input = args.source

    return asyncio.run(amain(args))


if __name__ == "__main__":
    sys.exit(main())
