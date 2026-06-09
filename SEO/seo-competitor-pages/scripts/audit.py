#!/usr/bin/env python3
"""
audit.py — Comparison-page and alternatives-page generator.

Pipeline:
  1. Fetch your-product URL + competitor URL(s)
  2. Extract product names, meta, existing Product schema
  3. Emit a structured page template (Markdown) + JSON-LD schema (JSON)
  4. Include fairness checklist + [VERIFY:...] markers for writer fill-in

The script does NOT invent comparison claims. The comparison table is emitted
empty for the writer to fill from verified sources.

Usage:
    python audit.py --your-url URL --vs URL                     # VS mode
    python audit.py --your-url URL --alternatives FILE          # ALTERNATIVES mode
    python audit.py --alternatives FILE --mode roundup          # ROUNDUP (no your-url)
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
BROWSER_UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
)

YEAR = time.strftime("%Y")
TODAY = time.strftime("%Y-%m-%d")


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
# Per-product context extraction
# ---------------------------------------------------------------------------

@dataclass
class ProductContext:
    url: str
    final_url: str = ""
    name: str = ""             # best guess at product name
    description: str = ""
    domain: str = ""
    og_image: str = ""
    declared_schema_types: list[str] = field(default_factory=list)
    product_schema: Optional[dict] = None
    fetch_error: Optional[str] = None


async def extract_product(url: str) -> ProductContext:
    pc = ProductContext(url=url)
    res = await fetch(url)
    if not res:
        pc.fetch_error = "fetch failed"
        return pc
    pc.final_url = res.get("final_url", "") or url
    pc.domain = urlparse(pc.final_url).netloc
    body = res.get("body", "")
    if not body:
        pc.fetch_error = "empty body"
        return pc

    try:
        soup = BeautifulSoup(body, "html.parser")
    except Exception as e:
        pc.fetch_error = f"parse error: {e}"
        return pc

    if soup.title and soup.title.string:
        pc.name = soup.title.string.strip()
    md = soup.find("meta", attrs={"name": "description"})
    if md and md.get("content"):
        pc.description = md.get("content").strip()
    ogi = soup.find("meta", attrs={"property": "og:image"})
    if ogi and ogi.get("content"):
        pc.og_image = ogi.get("content").strip()

    # JSON-LD types + Product schema
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
                    pc.declared_schema_types.extend(t)
                    if "Product" in t and pc.product_schema is None:
                        pc.product_schema = it
                else:
                    pc.declared_schema_types.append(t)
                    if t == "Product" and pc.product_schema is None:
                        pc.product_schema = it
            graph = it.get("@graph")
            if isinstance(graph, list):
                stack.extend(g for g in graph if isinstance(g, dict))

    # If product_schema declares a name field, prefer that over <title>
    if pc.product_schema and pc.product_schema.get("name"):
        pc.name = pc.product_schema.get("name") or pc.name

    # Try to strip "| BrandName" / "- BrandName" trailing site-name suffix
    pc.name = re.sub(r"\s*[|\-–—]\s*[^|]+$", "", pc.name).strip()

    return pc


# ---------------------------------------------------------------------------
# URL list reader
# ---------------------------------------------------------------------------

def read_url_list(path: Path) -> list[str]:
    urls = []
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.split("#", 1)[0].strip()
        if line:
            urls.append(line)
    return urls


# ---------------------------------------------------------------------------
# Template emitters
# ---------------------------------------------------------------------------

FAIRNESS_CHECKLIST = [
    "Every feature claim in the comparison table cites the competitor's docs / pricing page",
    "Pricing for every product includes \"as of [date]\" and a link to the live pricing page",
    "Both sides have pros AND cons — no one-sided framing",
    "\"Best for\" framing acknowledges different user needs (no universal \"X wins\")",
    "Competitor product names are spelled correctly, including trademark symbols where required",
    "No claims about competitor's customer count, revenue, or internal practices without a public citation",
    "Pricing is currency-tagged (USD / EUR / etc.) and tier-named exactly as the competitor names them",
    "If a competitor feature is marked unavailable, verify against their CURRENT docs (not cached versions)",
    "Quotes from third-party reviews (G2, Capterra, etc.) include source + date",
    "Feature comparison rows use the same yardstick for both products (don't compare X's headline metric to Y's edge case)",
    "Disclosure: declare your relationship to the products (e.g. \"We make Product A. This comparison reflects our perspective.\")",
    "Update schedule documented — when will this page be re-verified? (quarterly minimum)",
    "Page has a `dateModified` ≤ 6 months in JSON-LD",
    "All product names are linked to the competitor's actual site on first mention",
    "Comparison table is balanced — your product doesn't trivially dominate every row",
]


def slugify(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", s.lower()).strip("-")


def emit_vs_template(yours: ProductContext, competitor: ProductContext) -> tuple[str, list[dict]]:
    """Generate the COMPARISON-PAGE.md for a head-to-head VS page.

    Returns (markdown, schemas) where schemas is a list of JSON-LD objects.
    """
    your_name = yours.name or "[Your Product]"
    comp_name = competitor.name or "[Competitor]"
    slug = f"{slugify(your_name)}-vs-{slugify(comp_name)}"

    lines = [
        f"# {your_name} vs {comp_name}: [Decisive Angle] ({YEAR})",
        "",
        f"*Generated: {TODAY}*",
        "",
        "## Suggested page metadata",
        "",
        f"- **Slug:** `/{slug}/`",
        f"- **Title tag (60-110 chars):** `{your_name} vs {comp_name}: Which [Category] is Right for You? ({YEAR})`",
        f"- **Meta description (150-160 chars):** `Compare {your_name} vs {comp_name}: features, pricing, pros & cons. [VERIFY: add the specific decisive angle from your verified comparison.]`",
        f"- **Target keywords:** `{your_name.lower()} vs {comp_name.lower()}`, `{comp_name.lower()} vs {your_name.lower()}`, `{your_name.lower()} alternative`",
        "",
        "## Detected product context",
        "",
        f"**Your product** ({yours.domain})",
        f"- Page title: `{yours.name or '[VERIFY: not detected]'}`",
        f"- Description: `{(yours.description or '[VERIFY: missing]')[:200]}`",
        f"- Existing schema: {', '.join(yours.declared_schema_types) or 'none detected'}",
        f"- Product schema present: {'yes' if yours.product_schema else 'no — schema-generate can produce one'}",
        "",
        f"**Competitor** ({competitor.domain})",
        f"- Page title: `{competitor.name or '[VERIFY: not detected]'}`",
        f"- Description: `{(competitor.description or '[VERIFY: missing]')[:200]}`",
        f"- Existing schema: {', '.join(competitor.declared_schema_types) or 'none detected'}",
        "",
        "## Recommended H1 + introduction",
        "",
        f"**H1:** `{your_name} vs {comp_name}: Which is Right for You?`",
        "",
        "**Intro (150-200 words) — must address:**",
        "- One-sentence answer to \"which should I pick\" (\"best for X\" + \"best for Y\")",
        f"- Why this comparison matters (audience, common decision context for {your_name} vs {comp_name})",
        f"- Date stamp: \"This comparison reflects data verified as of {TODAY}.\"",
        "- Your disclosure (e.g. \"We make [Your Product]. This comparison reflects our perspective; we've cited every competitor claim.\")",
        "",
        "## Page outline (H2/H3)",
        "",
        f"### TL;DR — When to pick {your_name} vs {comp_name}",
        f"### Quick comparison",
        "  → Insert the comparison table below",
        f"### {your_name}: Overview, pros, cons",
        "  - Pros (4-6 bullets, every claim verifiable)",
        "  - Cons (3-5 bullets — yes, your product has cons; pretending it doesn't is a trust killer)",
        f"### {comp_name}: Overview, pros, cons",
        f"  - Pros (4-6 bullets, every claim cited from {competitor.domain})",
        "  - Cons (3-5 bullets, every claim cited)",
        "### Pricing comparison",
        "  → Two-column table; include \"as of [date]\" + link to live pricing",
        f"### Feature deep-dive: [pick 3-5 features that genuinely differentiate]",
        f"### When {your_name} is the better fit",
        f"### When {comp_name} is the better fit",
        f"### FAQ",
        "  → Insert FAQ schema; 4-6 questions covering common decision points",
        "",
        "## Comparison table (writer fills the cells)",
        "",
        f"| Feature | {your_name} | {comp_name} | Source |",
        "|---|:---:|:---:|---|",
    ]
    rows = [
        "Core feature 1", "Core feature 2", "Core feature 3", "Integrations",
        "API access", "Free tier", "Starting price (USD)", "Top-tier price (USD)",
        "Custom domain", "SSO / SAML", "GDPR compliance", "SOC 2",
        "Support — live chat", "Support — email response time",
    ]
    for r in rows:
        lines.append(f"| {r} | [VERIFY] | [VERIFY] | [link] |")
    lines.append("")
    lines.append("**Legend:** ✅ = supported, ❌ = not supported, ⚠️ = partial, [$X/mo] for pricing.")
    lines.append("")

    lines.append("## Pricing table")
    lines.append("")
    lines.append("| Tier | " + your_name + " (USD/mo) | " + comp_name + " (USD/mo) |")
    lines.append("|---|---|---|")
    lines.append("| Free | [$X or N/A] | [$Y or N/A] |")
    lines.append("| Starter | [$X] | [$Y] |")
    lines.append("| Pro | [$X] | [$Y] |")
    lines.append("| Enterprise | [contact sales] | [contact sales] |")
    lines.append("")
    lines.append(f"*Pricing as of {TODAY}. [VERIFY: update before publishing; both products may have changed prices.]*")
    lines.append("")

    lines.append("## FAQ (also emit as JSON-LD)")
    lines.append("")
    lines.append("Suggested 4-5 questions (writer drafts the answers from the comparison):")
    lines.append("")
    lines.append(f"1. Is {your_name} cheaper than {comp_name}?")
    lines.append(f"2. Does {comp_name} have [feature unique to your product]?")
    lines.append(f"3. Can I migrate from {comp_name} to {your_name}?")
    lines.append(f"4. Which is better for [target persona]?")
    lines.append(f"5. [Add a question that surfaces a real differentiator]")
    lines.append("")

    lines.append("## CTA placement")
    lines.append("")
    lines.append("- **After TL;DR:** \"Start with [Your Product] free →\"")
    lines.append("- **After feature comparison:** \"See [Your Product] in action — live demo →\"")
    lines.append(f"- **After \"When {your_name} is the better fit\":** \"Migrate from {comp_name} →\"")
    lines.append("")

    # Fairness checklist
    lines.append("## Fairness checklist — tick before publishing")
    lines.append("")
    for item in FAIRNESS_CHECKLIST:
        lines.append(f"- [ ] {item}")
    lines.append("")

    # Schemas to emit
    schemas = [
        {
            "@context": "https://schema.org",
            "@type": "Product",
            "name": your_name,
            "description": "[VERIFY: 1-sentence product description]",
            "brand": {"@type": "Brand", "name": "[VERIFY: your brand name]"},
            "url": yours.final_url or yours.url,
            "image": yours.og_image or "[image URL]",
            "aggregateRating": {
                "@type": "AggregateRating",
                "ratingValue": "[VERIFY: your G2/Capterra average rating]",
                "reviewCount": "[VERIFY: review count]",
                "bestRating": "5",
            },
        },
        {
            "@context": "https://schema.org",
            "@type": "Product",
            "name": comp_name,
            "description": "[VERIFY: 1-sentence summary of competitor — keep neutral]",
            "brand": {"@type": "Brand", "name": "[VERIFY: competitor brand name]"},
            "url": competitor.final_url or competitor.url,
            "image": competitor.og_image or "[image URL]",
        },
        {
            "@context": "https://schema.org",
            "@type": "FAQPage",
            "mainEntity": [
                {
                    "@type": "Question",
                    "name": f"Is {your_name} cheaper than {comp_name}?",
                    "acceptedAnswer": {"@type": "Answer", "text": "[VERIFY: write a balanced answer with current pricing]"},
                },
                {
                    "@type": "Question",
                    "name": f"Does {comp_name} have [your unique feature]?",
                    "acceptedAnswer": {"@type": "Answer", "text": "[VERIFY: yes/no/partial with citation]"},
                },
            ],
        },
    ]

    lines.append("## JSON-LD schemas to add to the page <head>")
    lines.append("")
    lines.append("See `schema.json` in the same output folder. Three schemas: two Product (one per product) + one FAQPage.")
    lines.append("")
    lines.append("*Note: FAQPage rich results are restricted to gov/health sites since Aug 2023, but AI engines (ChatGPT, Perplexity) still extract these. Including the schema is still worthwhile for AI citation.*")
    lines.append("")

    return "\n".join(lines).rstrip() + "\n", schemas


def emit_alternatives_template(yours: Optional[ProductContext],
                                 alternatives: list[ProductContext],
                                 mode: str) -> tuple[str, list[dict]]:
    """Generate the COMPARISON-PAGE.md for an alternatives roundup.

    mode: 'alternatives' (anchored to yours) or 'roundup' (editorial, no yours).
    """
    n = len(alternatives)
    if mode == "alternatives" and yours:
        your_name = yours.name or "[Your Product]"
        title = f"{n} Best {your_name} Alternatives in {YEAR}"
        slug = f"{n}-best-{slugify(your_name)}-alternatives"
        h1 = f"{n} Best {your_name} Alternatives in {YEAR}"
    else:
        title = f"{n} Best [Category] Tools in {YEAR}"
        slug = f"{n}-best-category-tools-{YEAR}"
        h1 = f"{n} Best [Category] Tools in {YEAR}"

    lines = [
        f"# {title}",
        "",
        f"*Generated: {TODAY}*",
        f"*Mode: {mode}*",
        "",
        "## Suggested page metadata",
        "",
        f"- **Slug:** `/{slug}/`",
        f"- **Title tag:** `{title}`",
        f"- **Meta description:** `Compare the top {n} [category] tools. Detailed pros, cons, pricing for each. [VERIFY: tailor the hook.]`",
        "",
        "## Detected alternatives",
        "",
        "| # | Name | Domain | Existing schema |",
        "|---|---|---|---|",
    ]
    for i, alt in enumerate(alternatives, 1):
        name = alt.name or f"[VERIFY: detect failed — {alt.url}]"
        schemas_short = ", ".join(alt.declared_schema_types[:3]) or "—"
        lines.append(f"| {i} | {name} | {alt.domain} | {schemas_short} |")
    lines.append("")

    if mode == "alternatives" and yours:
        lines.append(f"**Your product:** `{yours.name}` ({yours.domain}). Will be referenced in the \"Why we built {yours.name}\" section.")
        lines.append("")

    lines.append("## Page outline")
    lines.append("")
    lines.append("### Intro (150-200 words)")
    lines.append("- One-sentence summary of what makes a good [category] tool")
    lines.append("- Methodology: how you evaluated these (\"pricing checked Q[N] [year]; features verified from each product's docs\")")
    lines.append(f"- Date stamp: \"Comparison reflects data verified as of {TODAY}.\"")
    if mode == "alternatives" and yours:
        lines.append(f"- Disclosure: \"{yours.name} appears in this list. We've cited every claim about each alternative; we encourage you to verify pricing and features directly.\"")
    lines.append("")

    lines.append("### Quick comparison table")
    lines.append("")
    lines.append("| Tool | Best for | Free tier | Starting price | Standout feature |")
    lines.append("|---|---|---|---|---|")
    if mode == "alternatives" and yours:
        lines.append(f"| **{yours.name}** | [VERIFY] | [VERIFY] | [VERIFY] | [VERIFY] |")
    for alt in alternatives:
        nm = alt.name or f"[VERIFY: {alt.url}]"
        lines.append(f"| {nm} | [VERIFY] | [VERIFY] | [VERIFY] | [VERIFY] |")
    lines.append("")

    lines.append("### Detailed reviews")
    lines.append("")
    review_list = []
    if mode == "alternatives" and yours:
        review_list.append(yours)
    review_list.extend(alternatives)
    for i, p in enumerate(review_list, 1):
        nm = p.name or f"[VERIFY: name for {p.url}]"
        lines.append(f"#### {i}. {nm}")
        lines.append("")
        lines.append(f"**Best for:** [VERIFY: which audience / use case]")
        lines.append("**Pricing:** [VERIFY: starting price + free tier — link to live pricing page]")
        lines.append("**Standout feature:** [VERIFY: what genuinely differentiates this product]")
        lines.append("")
        lines.append(f"**Pros** (cite each from {p.domain}):")
        lines.append("- [VERIFY: pro 1]")
        lines.append("- [VERIFY: pro 2]")
        lines.append("- [VERIFY: pro 3]")
        lines.append("")
        lines.append("**Cons** (cite each, including from third-party reviews if applicable):")
        lines.append("- [VERIFY: con 1]")
        lines.append("- [VERIFY: con 2]")
        lines.append("")
        lines.append(f"**Try {nm}:** [link to product home + free trial if any]")
        lines.append("")

    if mode == "alternatives" and yours:
        lines.append(f"### Why we built {yours.name}")
        lines.append("")
        lines.append(f"- The specific gap in the alternatives that {yours.name} addresses")
        lines.append(f"- What you've prioritized differently — be honest about tradeoffs ({yours.name} doesn't try to be everything for everyone)")
        lines.append("- CTA: \"Try [Your Product] free →\"")
        lines.append("")

    lines.append("### How to choose")
    lines.append("")
    lines.append("- Decision tree by persona: \"If you're a [solo founder] → pick [X]; if you're a [team of 50+] → pick [Y]\"")
    lines.append("- Honest acknowledgment: \"There's no universal best — match the tool to your specific use case\"")
    lines.append("")

    lines.append("## Fairness checklist")
    lines.append("")
    for item in FAIRNESS_CHECKLIST:
        lines.append(f"- [ ] {item}")
    lines.append("")

    # ItemList schema with the alternatives
    item_list = {
        "@context": "https://schema.org",
        "@type": "ItemList",
        "name": title,
        "numberOfItems": n + (1 if mode == "alternatives" and yours else 0),
        "itemListElement": [],
    }
    position = 1
    if mode == "alternatives" and yours:
        item_list["itemListElement"].append({
            "@type": "ListItem",
            "position": position,
            "item": {
                "@type": "Product",
                "name": yours.name,
                "url": yours.final_url or yours.url,
                "image": yours.og_image or "[image URL]",
            },
        })
        position += 1
    for alt in alternatives:
        item_list["itemListElement"].append({
            "@type": "ListItem",
            "position": position,
            "item": {
                "@type": "Product",
                "name": alt.name or "[VERIFY: name]",
                "url": alt.final_url or alt.url,
                "image": alt.og_image or "[image URL]",
            },
        })
        position += 1

    schemas = [item_list]

    lines.append("## JSON-LD schema to add to the page <head>")
    lines.append("")
    lines.append("See `schema.json`. An ItemList referencing each product as a ListItem (Product). Add individual Product schemas if you want richer per-product markup.")
    lines.append("")

    return "\n".join(lines).rstrip() + "\n", schemas


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

async def amain(args) -> int:
    DEFAULT_OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    # Determine mode
    if args.vs and args.your_url:
        mode = "vs"
    elif args.alternatives:
        if args.mode == "roundup":
            mode = "roundup"
        elif args.your_url:
            mode = "alternatives"
        else:
            mode = "roundup"
    else:
        print("ERROR: provide either (--your-url + --vs URL) or (--your-url + --alternatives FILE) "
              "or (--alternatives FILE --mode roundup)", file=sys.stderr)
        return 2

    yours: Optional[ProductContext] = None
    if args.your_url:
        print(f"Fetching your product: {args.your_url}…", file=sys.stderr)
        yours = await extract_product(args.your_url)
        if yours.fetch_error:
            print(f"WARNING: could not fetch your-url: {yours.fetch_error}", file=sys.stderr)

    # VS mode
    if mode == "vs":
        print(f"Fetching competitor: {args.vs}…", file=sys.stderr)
        comp = await extract_product(args.vs)
        if comp.fetch_error:
            print(f"WARNING: could not fetch competitor: {comp.fetch_error}", file=sys.stderr)
        md, schemas = emit_vs_template(yours, comp)
        slug = f"{slugify(yours.name or 'your-product')}-vs-{slugify(comp.name or 'competitor')}"
    else:
        # ALTERNATIVES or ROUNDUP
        if not args.alternatives:
            print("ERROR: --alternatives FILE required.", file=sys.stderr)
            return 2
        if not args.alternatives.exists():
            print(f"ERROR: --alternatives file not found: {args.alternatives}", file=sys.stderr)
            return 1
        urls = read_url_list(args.alternatives)
        if not urls:
            print(f"ERROR: --alternatives file is empty.", file=sys.stderr)
            return 1
        print(f"Fetching {len(urls)} alternative(s)…", file=sys.stderr)
        alts = await asyncio.gather(*(extract_product(u) for u in urls))
        md, schemas = emit_alternatives_template(yours if mode == "alternatives" else None,
                                                    list(alts), mode)
        if mode == "alternatives" and yours:
            slug = f"{len(urls)}-best-{slugify(yours.name or 'product')}-alternatives"
        else:
            slug = f"{len(urls)}-best-category-tools-{YEAR}"

    # Write outputs
    out_dir = args.output if args.output else DEFAULT_OUTPUT_DIR
    out_dir.mkdir(parents=True, exist_ok=True)
    md_path = out_dir / f"COMPARISON-PAGE-{slug}.md"
    md_path.write_text(md, encoding="utf-8")
    schema_path = out_dir / f"COMPARISON-SCHEMA-{slug}.json"
    schema_path.write_text(
        json.dumps(schemas[0] if len(schemas) == 1 else schemas, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )

    if args.stdout:
        sys.stdout.write(md)
    else:
        print(f"\nWrote:", file=sys.stderr)
        print(f"  {md_path}", file=sys.stderr)
        print(f"  {schema_path}", file=sys.stderr)
    print(f"\nMode: {mode}; schemas emitted: {len(schemas)}", file=sys.stderr)
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Comparison-page and alternatives-page generator.")
    parser.add_argument("--your-url", default=None,
        help="URL of your own product (the page being promoted). Required for VS and ALTERNATIVES modes.")
    parser.add_argument("--vs", default=None,
        help="URL of the competitor (VS mode — head-to-head).")
    parser.add_argument("--alternatives", type=Path, default=None,
        help="Path to a URL list file (alternatives or roundup mode).")
    parser.add_argument("--mode", choices=("vs", "alternatives", "roundup"), default=None,
        help="Force mode. Otherwise auto-derived from --vs / --alternatives / --your-url.")
    parser.add_argument("-o", "--output", type=Path, default=None)
    parser.add_argument("--stdout", action="store_true")
    parser.add_argument("--source", default=None, help="Source label for bridge pre-fetch.")
    args = parser.parse_args(argv)

    return asyncio.run(amain(args))


if __name__ == "__main__":
    sys.exit(main())
