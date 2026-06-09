#!/usr/bin/env python3
"""
audit.py — JSON-LD schema generator.

Fetches a page, detects the best-fit Schema.org @type from URL pattern, existing
JSON-LD, OG type, and headings, then emits ready-to-paste JSON-LD with the page's
actual content auto-filled in. Unknown fields are marked [PLACEHOLDER].

Refuses to emit deprecated types (HowTo, SpecialAnnouncement, etc.).
Warns on restricted types (FAQPage outside government/healthcare).

Pyodide-safe: routes the page fetch through /gpt/backend/api/v1/fetch-url.

Usage:
    python audit.py URL                       # auto-detect type
    python audit.py URL --type Article        # force a type
    python audit.py URL --all                 # emit all applicable types
    python audit.py --list                    # show available templates
    python audit.py --type LocalBusiness --blank   # placeholder-only, no fetch
"""

from __future__ import annotations

import argparse
import asyncio
import json
import re
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional
from urllib.parse import urljoin, urlparse

from bs4 import BeautifulSoup


DEFAULT_OUTPUT_DIR = (
    Path("/outputs")
    if sys.platform == "emscripten"
    else Path.home() / "Documents" / "synergyAI" / "outputs"
)
PROXY_ENDPOINT = "/gpt/backend/api/v1/fetch-url"
SCRIPT_DIR = Path(__file__).resolve().parent
SKILL_DIR = SCRIPT_DIR.parent
TEMPLATES_DIR = SKILL_DIR / "templates"

BROWSER_UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
)

# Refused outright — deprecated rich-result types.
DEPRECATED_TYPES = {
    "HowTo":               "rich results removed September 2023",
    "SpecialAnnouncement": "deprecated July 2025",
    "CourseInfo":          "retired June 2025",
    "EstimatedSalary":     "retired June 2025",
    "LearningVideo":       "retired June 2025",
    "ClaimReview":         "retired from rich results June 2025",
    "VehicleListing":      "retired from rich results June 2025",
    "PracticeProblem":     "retired late 2025",
}

# Warned but still emitted — restricted to specific verticals, still useful for AI citation.
RESTRICTED_TYPES = {
    "FAQPage": "Google rich results restricted to government/healthcare since August 2023. AI engines (ChatGPT, Perplexity) still extract these for citation.",
    "QAPage":  "Community-Q&A only (Stack-Overflow-style sites). Generic commercial pages should not use this for Google rich results.",
}


# ---------------------------------------------------------------------------
# Template loading
# ---------------------------------------------------------------------------

def load_templates() -> dict[str, dict]:
    """Load both common and specialized template files into {type_name: entry}."""
    out: dict[str, dict] = {}
    for fname in ("templates_common.json", "templates_specialized.json"):
        path = TEMPLATES_DIR / fname
        if not path.exists():
            continue
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as e:
            print(f"  warning: could not parse {fname}: {e}", file=sys.stderr)
            continue
        for entry in data.get("templates", []):
            t = entry.get("type")
            if t:
                out[t] = entry
    return out


# ---------------------------------------------------------------------------
# Fetch helpers (same pattern as other audits)
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
    return {
        "body": data.get("html", "") or "",
        "status": data.get("status", 0) or 0,
        "final_url": data.get("final_url", "") or url,
    }


def _fetch_urllib(url: str) -> Optional[dict]:
    import ssl
    import urllib.request
    import gzip
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    req = urllib.request.Request(url, headers={
        "User-Agent": BROWSER_UA,
        "Accept": "text/html,application/xhtml+xml,*/*;q=0.8",
        "Accept-Encoding": "gzip",
    })
    try:
        with urllib.request.urlopen(req, timeout=15, context=ctx) as r:
            body_bytes = r.read()
            status = r.status
            final_url = r.url
            encoding = (r.headers.get("Content-Encoding") or "").lower()
    except Exception:
        return None
    if encoding == "gzip":
        try: body_bytes = gzip.decompress(body_bytes)
        except OSError: pass
    try:
        body_text = body_bytes.decode("utf-8", errors="replace")
    except Exception:
        body_text = ""
    return {"body": body_text, "status": status, "final_url": final_url}


async def fetch(url: str) -> Optional[dict]:
    if _is_pyodide():
        return await _fetch_pyodide(url)
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(None, _fetch_urllib, url)


# ---------------------------------------------------------------------------
# Page-content extraction
# ---------------------------------------------------------------------------

@dataclass
class PageContext:
    url: str
    final_url: str
    title: Optional[str] = None
    meta_description: Optional[str] = None
    canonical: Optional[str] = None
    lang: Optional[str] = None
    author: Optional[str] = None
    date_published: Optional[str] = None
    date_modified: Optional[str] = None
    og_type: Optional[str] = None
    og_image: Optional[str] = None
    first_image: Optional[str] = None
    h1: Optional[str] = None
    word_count: int = 0
    existing_jsonld_types: list[str] = field(default_factory=list)
    site_name: Optional[str] = None


def extract_context(html: str, url: str, final_url: Optional[str] = None) -> PageContext:
    """Walk the HTML and pull the fields used for JSON-LD auto-fill."""
    ctx = PageContext(url=url, final_url=final_url or url)
    soup = BeautifulSoup(html, "html.parser")

    if soup.title and soup.title.string:
        ctx.title = soup.title.string.strip()

    md = soup.find("meta", attrs={"name": "description"})
    if md and md.get("content"):
        ctx.meta_description = md.get("content").strip()

    canon = soup.find("link", rel="canonical")
    if canon and canon.get("href"):
        ctx.canonical = urljoin(ctx.final_url, canon.get("href").strip())

    html_tag = soup.find("html")
    if html_tag and html_tag.get("lang"):
        ctx.lang = html_tag.get("lang").strip().split("-")[0]

    author_meta = soup.find("meta", attrs={"name": re.compile(r"^author$", re.I)})
    if author_meta and author_meta.get("content"):
        ctx.author = author_meta.get("content").strip()

    # OG metadata
    for og_prop in ("og:type", "og:image", "og:site_name"):
        m = soup.find("meta", attrs={"property": og_prop})
        if m and m.get("content"):
            val = m.get("content").strip()
            if og_prop == "og:type": ctx.og_type = val.lower()
            elif og_prop == "og:image": ctx.og_image = urljoin(ctx.final_url, val)
            elif og_prop == "og:site_name": ctx.site_name = val

    # Article timestamps
    for prop, attr in (("article:published_time", "date_published"),
                       ("article:modified_time", "date_modified")):
        m = soup.find("meta", attrs={"property": prop})
        if m and m.get("content"):
            setattr(ctx, attr, m.get("content").strip())

    # First H1
    h1 = soup.find("h1")
    if h1:
        ctx.h1 = h1.get_text(" ", strip=True)[:200]

    # First image (fallback when no og:image)
    for img in soup.find_all("img"):
        src = (img.get("src") or img.get("data-src") or "").strip()
        if src and not src.startswith("data:"):
            ctx.first_image = urljoin(ctx.final_url, src)
            break

    # Existing JSON-LD @types
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
                    ctx.existing_jsonld_types.extend(t)
                else:
                    ctx.existing_jsonld_types.append(t)
            # Walk into @graph
            graph = it.get("@graph")
            if isinstance(graph, list):
                stack.extend(g for g in graph if isinstance(g, dict))

    # JSON-LD author/dates (priority over meta when both present)
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
            if not ctx.author:
                author = it.get("author")
                if isinstance(author, dict) and author.get("name"):
                    ctx.author = author.get("name")
                elif isinstance(author, str):
                    ctx.author = author
            if not ctx.date_published and isinstance(it.get("datePublished"), str):
                ctx.date_published = it.get("datePublished")
            if not ctx.date_modified and isinstance(it.get("dateModified"), str):
                ctx.date_modified = it.get("dateModified")
            graph = it.get("@graph")
            if isinstance(graph, list):
                stack.extend(g for g in graph if isinstance(g, dict))

    # Word count (strip nav/header/footer)
    for el in soup(["script", "style", "nav", "header", "footer"]):
        el.decompose()
    text = soup.get_text(" ", strip=True)
    ctx.word_count = len(re.findall(r"\b\w+\b", text))

    return ctx


# ---------------------------------------------------------------------------
# Type detection
# ---------------------------------------------------------------------------

@dataclass
class TypeCandidate:
    type_name: str
    score: int
    reasons: list[str]


def detect_types(ctx: PageContext, templates: dict[str, dict]) -> list[TypeCandidate]:
    """Score every template against page context. Return sorted candidates (high→low)."""
    candidates: list[TypeCandidate] = []
    path = urlparse(ctx.final_url).path.lower()

    for type_name, entry in templates.items():
        score = 0
        reasons: list[str] = []
        signals = entry.get("page_signals", {})

        # URL pattern matching
        for pattern in signals.get("url_patterns", []):
            if pattern in path or (pattern == "/" and path in ("", "/")):
                score += 30
                reasons.append(f"URL contains {pattern!r}")
                break

        # OG type
        if ctx.og_type and ctx.og_type in signals.get("og_types", []):
            score += 20
            reasons.append(f"og:type='{ctx.og_type}'")

        # Heading cues
        haystack = (ctx.h1 or "") + " " + (ctx.title or "")
        haystack = haystack.lower()
        for cue in signals.get("heading_cues", []):
            if cue.lower() in haystack:
                score += 10
                reasons.append(f"heading/title contains {cue!r}")

        # Word count threshold
        min_wc = signals.get("min_word_count")
        if isinstance(min_wc, int) and ctx.word_count >= min_wc:
            score += 5

        # Existing JSON-LD with this @type — strong signal it's the right one
        if type_name in ctx.existing_jsonld_types:
            score += 25
            reasons.append("already declared on page")

        # Always-recommended (BreadcrumbList): boost slightly so it surfaces
        if signals.get("always_recommended"):
            score += 5
            reasons.append("always recommended")

        if score > 0:
            candidates.append(TypeCandidate(
                type_name=type_name, score=score, reasons=reasons,
            ))

    # If nothing scored, fall back to WebPage
    if not candidates and "WebPage" in templates:
        candidates.append(TypeCandidate(
            type_name="WebPage", score=1, reasons=["fallback when no type detected"],
        ))

    candidates.sort(key=lambda c: -c.score)
    return candidates


# ---------------------------------------------------------------------------
# Auto-fill template with page context
# ---------------------------------------------------------------------------

def autofill(template: dict, ctx: PageContext) -> tuple[dict, list[str]]:
    """Return (filled_template, list_of_filled_fields).

    Walks the template recursively and replaces [PLACEHOLDER]-style values with
    page-derived content where available.
    """
    filled_fields: list[str] = []

    # Map of fields where we have data; the values get substituted when the
    # template's placeholder text contains these patterns.
    fills = {
        # generic substitutions
        "headline":     ctx.title,
        "name":         ctx.title or ctx.site_name,
        "description":  ctx.meta_description,
        "url":          ctx.canonical or ctx.final_url,
        "@id":          ctx.canonical or ctx.final_url,
        "image":        ctx.og_image or ctx.first_image,
        "thumbnailUrl": ctx.og_image or ctx.first_image,
        "datePublished": ctx.date_published,
        "dateModified": ctx.date_modified or ctx.date_published,
        "inLanguage":   ctx.lang,
    }

    def walk(node, path=""):
        if isinstance(node, dict):
            for k, v in list(node.items()):
                if k == "@context" or k == "@type":
                    continue
                if k in ("author", "publisher") and isinstance(v, dict):
                    # Recursively walk; also fill nested name where applicable
                    if k == "author" and ctx.author and isinstance(v.get("name"), str) and v["name"].startswith("["):
                        v["name"] = ctx.author
                        filled_fields.append(f"{path}{k}.name ← '{ctx.author}'")
                    if k == "publisher" and ctx.site_name and isinstance(v.get("name"), str) and v["name"].startswith("["):
                        v["name"] = ctx.site_name
                        filled_fields.append(f"{path}{k}.name ← '{ctx.site_name}'")
                    walk(v, path=f"{path}{k}.")
                    continue
                if isinstance(v, (dict, list)):
                    walk(v, path=f"{path}{k}.")
                    continue
                if k in fills and fills[k] and isinstance(v, str) and v.startswith("["):
                    node[k] = fills[k]
                    filled_fields.append(f"{path}{k} ← {fills[k]!r}")
        elif isinstance(node, list):
            for i, item in enumerate(node):
                walk(item, path=f"{path}[{i}].")

    import copy
    filled = copy.deepcopy(template)
    walk(filled)
    return filled, filled_fields


# ---------------------------------------------------------------------------
# Report rendering
# ---------------------------------------------------------------------------

def render_report(ctx: PageContext, picked: list[tuple[str, dict, list[str]]],
                   candidates: list[TypeCandidate], warnings: list[str]) -> str:
    lines = [
        "# Schema Generation Report",
        "",
        f"**Source:** {ctx.url}",
        f"**Final URL:** {ctx.final_url}",
        f"**Generated:** {time.strftime('%Y-%m-%d %H:%M:%S')}",
        "",
        "## Page context detected",
        "",
        f"- Title: {ctx.title!r}" if ctx.title else "- Title: ⚠️ not found",
        f"- Meta description: {(ctx.meta_description or '')[:100]!r}",
        f"- Canonical: {ctx.canonical or '⚠️ not declared'}",
        f"- Language: {ctx.lang or '⚠️ not declared'}",
        f"- Author: {ctx.author or '⚠️ not declared'}",
        f"- Published: {ctx.date_published or '⚠️ not declared'}",
        f"- Modified: {ctx.date_modified or '⚠️ not declared'}",
        f"- OG type: {ctx.og_type or '—'}",
        f"- OG image: {ctx.og_image or '—'}",
        f"- H1: {ctx.h1!r}" if ctx.h1 else "- H1: ⚠️ not found",
        f"- Word count: {ctx.word_count}",
        f"- Existing JSON-LD @types: {', '.join(ctx.existing_jsonld_types) or 'none'}",
        "",
    ]

    if candidates:
        lines.append("## Type candidates")
        lines.append("")
        lines.append("| Type | Score | Reasons |")
        lines.append("|---|---|---|")
        for c in candidates[:8]:
            lines.append(f"| {c.type_name} | {c.score} | {'; '.join(c.reasons)} |")
        lines.append("")

    if warnings:
        lines.append("## Warnings")
        lines.append("")
        for w in warnings:
            lines.append(f"- ⚠️ {w}")
        lines.append("")

    lines.append("## Generated JSON-LD")
    lines.append("")
    lines.append("Paste each block into the page's `<head>` (inside a `<script type='application/ld+json'>` tag).")
    lines.append("")
    for type_name, filled, fields in picked:
        lines.append(f"### {type_name}")
        lines.append("")
        if fields:
            lines.append("**Auto-filled fields:**")
            lines.append("")
            for f in fields:
                lines.append(f"- {f}")
            lines.append("")
        unfilled = _count_placeholders(filled)
        if unfilled:
            lines.append(f"**{unfilled} `[PLACEHOLDER]` field(s) remaining** — fill these manually before deploying.")
            lines.append("")
        lines.append("```html")
        lines.append('<script type="application/ld+json">')
        lines.append(json.dumps(filled, indent=2, ensure_ascii=False))
        lines.append('</script>')
        lines.append("```")
        lines.append("")

    return "\n".join(lines).rstrip() + "\n"


def _count_placeholders(node, count=0) -> int:
    if isinstance(node, dict):
        for v in node.values():
            count = _count_placeholders(v, count)
    elif isinstance(node, list):
        for v in node:
            count = _count_placeholders(v, count)
    elif isinstance(node, str) and node.startswith("[") and node.endswith("]"):
        count += 1
    return count


# ---------------------------------------------------------------------------
# Main pipeline
# ---------------------------------------------------------------------------

async def amain(args) -> int:
    DEFAULT_OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    templates = load_templates()
    if not templates:
        print("ERROR: no templates loaded. Check templates/ directory.", file=sys.stderr)
        return 1

    # --list mode
    if args.list:
        print("Available templates:\n", file=sys.stdout)
        for t in sorted(templates.keys()):
            desc = (templates[t].get("description") or "")[:80]
            print(f"  {t:<28} {desc}", file=sys.stdout)
        print(f"\nRefused (deprecated): {', '.join(sorted(DEPRECATED_TYPES.keys()))}", file=sys.stdout)
        print(f"Warned (restricted): {', '.join(sorted(RESTRICTED_TYPES.keys()))}", file=sys.stdout)
        return 0

    # Validate --type if given
    forced_type = args.type
    if forced_type:
        if forced_type in DEPRECATED_TYPES:
            print(f"ERROR: refusing to emit {forced_type} — {DEPRECATED_TYPES[forced_type]}",
                  file=sys.stderr)
            return 1
        if forced_type not in templates and forced_type not in RESTRICTED_TYPES:
            print(f"ERROR: unknown type {forced_type!r}. Run --list to see available types.",
                  file=sys.stderr)
            return 1

    # --blank mode (no fetch)
    if args.blank:
        if not forced_type:
            print("ERROR: --blank requires --type TYPE", file=sys.stderr)
            return 2
        if forced_type not in templates:
            print(f"ERROR: no template for {forced_type!r}", file=sys.stderr)
            return 1
        out_dir = args.output if args.output else DEFAULT_OUTPUT_DIR
        out_dir.mkdir(parents=True, exist_ok=True)
        blank = templates[forced_type]["template"]
        json_path = out_dir / "schema-recommended.json"
        json_path.write_text(json.dumps(blank, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        print(f"Wrote blank {forced_type} template to {json_path}", file=sys.stderr)
        return 0

    if not args.input:
        print("ERROR: URL required (or use --list / --blank).", file=sys.stderr)
        return 2

    # Fetch page
    print(f"Fetching {args.input}…", file=sys.stderr)
    fetched = await fetch(args.input)
    if not fetched:
        print(f"ERROR: could not fetch {args.input}", file=sys.stderr)
        return 1
    body = fetched.get("body", "")
    if not body:
        print(f"ERROR: empty body from {args.input}", file=sys.stderr)
        return 1
    ctx = extract_context(body, args.input, fetched.get("final_url"))

    candidates = detect_types(ctx, templates)
    print(f"Type candidates (top 5):", file=sys.stderr)
    for c in candidates[:5]:
        print(f"  {c.score:>3}  {c.type_name:<22} ({'; '.join(c.reasons)})", file=sys.stderr)

    # Pick the types to emit
    if forced_type:
        types_to_emit = [forced_type]
    elif args.all:
        # Emit top candidates that scored above threshold (10+)
        types_to_emit = [c.type_name for c in candidates if c.score >= 10][:5]
        if not types_to_emit:
            types_to_emit = [candidates[0].type_name] if candidates else ["WebPage"]
    else:
        types_to_emit = [candidates[0].type_name] if candidates else ["WebPage"]

    # Generate JSON-LD for each
    warnings: list[str] = []
    picked: list[tuple[str, dict, list[str]]] = []
    for tname in types_to_emit:
        if tname in DEPRECATED_TYPES:
            warnings.append(f"refusing to emit {tname} ({DEPRECATED_TYPES[tname]})")
            continue
        if tname in RESTRICTED_TYPES and tname not in templates:
            warnings.append(f"{tname} is restricted — {RESTRICTED_TYPES[tname]}")
            continue
        if tname in RESTRICTED_TYPES:
            warnings.append(f"{tname} restricted: {RESTRICTED_TYPES[tname]}")
        if tname not in templates:
            warnings.append(f"no template for {tname!r}; skipping")
            continue
        template = templates[tname]["template"]
        filled, fields = autofill(template, ctx)
        picked.append((tname, filled, fields))

    if not picked:
        if warnings:
            print("ERROR: no schema generated. Reason(s):", file=sys.stderr)
            for w in warnings:
                print(f"  - {w}", file=sys.stderr)
        else:
            print("ERROR: no types could be generated.", file=sys.stderr)
        return 1

    # Outputs
    out_dir = args.output if args.output else DEFAULT_OUTPUT_DIR
    out_dir.mkdir(parents=True, exist_ok=True)

    # Combined JSON file: list of filled schemas
    combined = [filled for _, filled, _ in picked]
    json_path = out_dir / "schema-recommended.json"
    if len(combined) == 1:
        json_path.write_text(json.dumps(combined[0], indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    else:
        json_path.write_text(json.dumps(combined, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")

    md_path = out_dir / "SCHEMA-REPORT.md"
    md_path.write_text(render_report(ctx, picked, candidates, warnings), encoding="utf-8")

    print(f"\nWrote schema recommendations:", file=sys.stderr)
    print(f"  {json_path}", file=sys.stderr)
    print(f"  {md_path}", file=sys.stderr)
    print(f"\nEmitted {len(picked)} schema block(s): {', '.join(t for t, _, _ in picked)}",
          file=sys.stderr)
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="JSON-LD schema generator with auto page-content fill-in."
    )
    parser.add_argument("input", nargs="?",
        help="URL to generate schema for. Accepts the URL positionally.")
    parser.add_argument("--url", dest="url_alias", default=None,
        help="Alias for the positional URL argument.")
    parser.add_argument("--type", default=None,
        help="Force a specific @type (e.g. Article, LocalBusiness). "
             "Run --list to see all available types.")
    parser.add_argument("--all", action="store_true",
        help="Emit all applicable types (above score 10).")
    parser.add_argument("--list", action="store_true",
        help="List available template types and exit (no fetch needed).")
    parser.add_argument("--blank", action="store_true",
        help="Output a blank template (placeholder-only, no page fetch). Requires --type.")
    parser.add_argument("-o", "--output", type=Path, default=None,
        help="Output directory. Default: ~/Documents/synergyAI/outputs/ (or /outputs/ in Pyodide).")
    parser.add_argument("--source", default=None,
        help="Source URL label when input is a local file (bridge pre-fetch case).")
    args = parser.parse_args(argv)

    if not args.input and args.url_alias:
        args.input = args.url_alias
    if args.source and re.match(r"^https?://", args.source, re.I):
        args.input = args.source

    return asyncio.run(amain(args))


if __name__ == "__main__":
    sys.exit(main())
