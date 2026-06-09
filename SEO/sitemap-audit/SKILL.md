---
name: sitemap-audit
description: "Validate an existing XML sitemap or generate a new one for a site. Use for any sitemap-related request: 'audit my sitemap', 'check sitemap.xml', 'is my sitemap valid', 'generate a sitemap for example.com', 'crawl my site and build a sitemap', 'split my sitemap' (>50k URLs), 'hreflang sitemap for multi-language site'. Modes: ANALYZE (fetches /sitemap.xml + /sitemap_index.xml + robots.txt 'Sitemap:' directives, validates 50k cap, flags deprecated <priority>/<changefreq>, samples URLs for HTTP status / noindex / canonical / redirect filtering); GENERATE-FROM-CRAWL (BFS internal links from a seed URL, filters noindexed/non-canonical/3xx, emits sitemap.xml with auto-split sitemap-index.xml at 50k); GENERATE-FROM-LIST (read URLs from a text file, same output); HREFLANG (multi-language sitemap with xhtml:link rel=alternate variants per <url>). Quality gates: warning at 30+ location pages, hard-stop at 50+ (doorway-page risk). Output: VALIDATION-REPORT.md for analyze, sitemap.xml + STRUCTURE.md for generate."
dependencies: [beautifulsoup4]
# NOTE: fetches_urls is intentionally false. This skill does its own fetches
# via /gpt/backend/api/v1/fetch-url (same pattern as serp-shape). The bridge's
# argv-prefetch would just download one HTML page that we don't actually need —
# analyze mode fetches sitemap.xml + robots.txt, generate mode crawls fresh.
fetches_urls: false
---

# sitemap-audit

Two purposes:
1. **Validate** an existing XML sitemap (or sitemap index) — find broken URLs, deprecated tags, oversized files, quality-gate violations.
2. **Generate** a new sitemap — crawl-based, URL-list-based, or hreflang-aware.

This skill is the structural counterpart to `ai-search-audit` (which checks one page in depth) and `serp-shape` (which checks one page against competitors). `sitemap-audit` operates at the **site-architecture** level.

> ## ✅ REQUIRED FIRST STEP — ALWAYS CALL THE TOOL
>
> When the user asks anything sitemap-related and gives you a URL or a sitemap path, your first action is `run_skill_script` with `dir_name: sitemap-audit` and `scripts/audit.py`. Don't refuse based on access concerns — localhost URLs are fully supported through the fetch bridge. Don't ask the user to "check that the sitemap exists" first; the audit itself reports presence/absence.
>
> ## ⛔ NO FABRICATION
>
> If the script errors or writes no file, surface the exact error. Never produce a "looks like your sitemap probably has X" report from inference. Sitemap validation is fact-by-fact: every claim ("3 URLs return 404", "47 URLs use the deprecated <priority> tag") must come from the actual report file at `~/Documents/synergyAI/outputs/`.

## When to use this skill

| User says | Mode |
|---|---|
| "audit my sitemap", "check sitemap.xml at example.com" | analyze |
| "is my sitemap valid?", "are there issues with sitemap.xml" | analyze |
| "generate a sitemap for my site", "crawl example.com and build a sitemap" | generate-from-crawl |
| "here's my URL list, build a sitemap", "make a sitemap from these URLs" | generate-from-list |
| "I have a multi-language site, build hreflang sitemap" | hreflang |
| "split my sitemap" (>50k URLs) | analyze + split-output |

If the user provides a sitemap URL or path → analyze.
If the user provides a site URL with no existing sitemap → generate-from-crawl.
If the user provides a list → generate-from-list.

## Invocation — exact argv shapes

The URL is **positional**, not a flag. When calling via `run_skill_script`, the `argv` array should be:

```json
// Analyze (URL passed positionally)
{ "dir_name": "sitemap-audit", "script": "scripts/audit.py", "argv": ["https://example.com"] }

// Generate from a crawl
{ "argv": ["https://example.com", "--generate", "--max-pages", "500"] }

// Generate from a URL list
{ "argv": ["--from-list", "/path/to/urls.txt", "--generate"] }

// Hreflang sitemap
{ "argv": ["https://example.com", "--generate", "--hreflang"] }

// Local-dev mirror (canonical points to production)
{ "argv": ["http://localhost/cv", "--generate", "--ignore-canonical"] }
```

**Do NOT pass the URL with a flag name** — there is no `--input`, `--site`, or `--target` flag. The URL goes first in `argv`, with no preceding flag. (The `--url` alias exists as a backstop for tools that cannot emit positional argv, but the positional form is preferred.)

CLI form for reference:

```bash
# Analyze an existing sitemap (or auto-discover one)
python scripts/audit.py https://example.com
python scripts/audit.py https://example.com/sitemap.xml

# Generate from a crawl (BFS internal links)
python scripts/audit.py https://example.com --generate --max-pages 500

# Generate from a URL list (one per line, '#' comments)
python scripts/audit.py --from-list urls.txt --generate

# Hreflang-aware generation
python scripts/audit.py https://example.com --generate --hreflang

# Local-dev where the page declares a production canonical
python scripts/audit.py http://localhost/cv --generate --ignore-canonical

# Custom output path
python scripts/audit.py https://example.com -o my-sitemap-report.md
```

All output lands in `~/Documents/synergyAI/outputs/`. Analyze mode writes `VALIDATION-REPORT.md`. Generate modes write `sitemap.xml` (or split `sitemap-index.xml` + `sitemap-pages-N.xml` at 50k URLs) plus a companion `STRUCTURE.md` grouping URLs by path pattern.

## What the analyze pass checks

| Check | Severity | Rationale |
|---|---|---|
| Sitemap reachable at `/sitemap.xml`, `/sitemap_index.xml`, or referenced in robots.txt | critical if none | Without one, search and AI crawlers fall back to link discovery, missing orphan pages |
| Valid XML format | critical | Malformed XML = silently dropped by Google |
| <50,000 URLs per file | critical | Protocol limit; over → file ignored. Auto-split with sitemap-index |
| URLs return HTTP 200 (sampled) | recommended | 404/500 URLs in sitemap waste crawl budget |
| No noindexed URLs included | recommended | Mixed signals confuse crawlers |
| No non-canonical URLs included | recommended | Canonical drift weakens authority signals |
| No 3xx-redirected URLs included | recommended | Send the final URL, not the redirect chain start |
| HTTPS-only (no HTTP URLs) | recommended | Mixed protocols flag as insecure |
| `<lastmod>` dates are realistic (not all identical) | nice-to-have | Identical lastmod across thousands of URLs reads as fake |
| `<priority>` / `<changefreq>` tags absent | info | Google has ignored both since 2017 — present = noise |

## Quality gates (generate modes)

| Pattern | Threshold | Action |
|---|---|---|
| Location pages (e.g. `/locations/`, `/cities/`, `/service-area/`, `/<city-name>/`) | 30+ | **Warning** — emit but require ≥60% unique content per page |
| Location pages | 50+ | **Hard stop** — refuse to emit without `--force-locations` and a documented justification |
| Identical `<lastmod>` across all entries | always | Skip the field rather than ship obvious fakes |
| `<priority>` / `<changefreq>` requested | always | Refuse to emit — Google ignores them since 2017 |

The 6 industry-pattern markdown files under `references/industry-patterns/` (saas, ecommerce, local-service, publisher, agency, generic) describe what URL structures each business type typically needs. Consult them when the user asks "what should my sitemap structure look like for a SaaS site?".

## Output formats

### Standard sitemap

```xml
<?xml version="1.0" encoding="UTF-8"?>
<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
  <url>
    <loc>https://example.com/page</loc>
    <lastmod>2026-05-18</lastmod>
  </url>
</urlset>
```

### Sitemap index (auto-emitted at >50k URLs)

```xml
<?xml version="1.0" encoding="UTF-8"?>
<sitemapindex xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
  <sitemap><loc>https://example.com/sitemap-pages-1.xml</loc></sitemap>
  <sitemap><loc>https://example.com/sitemap-pages-2.xml</loc></sitemap>
</sitemapindex>
```

### Hreflang sitemap (multi-language)

```xml
<?xml version="1.0" encoding="UTF-8"?>
<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9"
        xmlns:xhtml="http://www.w3.org/1999/xhtml">
  <url>
    <loc>https://example.com/page</loc>
    <xhtml:link rel="alternate" hreflang="en-US" href="https://example.com/page"/>
    <xhtml:link rel="alternate" hreflang="fr" href="https://example.com/fr/page"/>
    <xhtml:link rel="alternate" hreflang="x-default" href="https://example.com/page"/>
  </url>
</urlset>
```

## Pyodide / browser-Python notes

Pyodide cannot fetch arbitrary URLs (browser CORS). The script routes fetches through the host's same-origin proxy at `/gpt/backend/api/v1/fetch-url` — same pattern as `ai-search-audit` and `serp-shape`. The crawler uses `asyncio` + `pyodide.http.pyfetch` to fan out fetches concurrently within Pyodide's event loop.

When the bridge pre-fetches the input URL (`fetches_urls: true`), the script receives a local file path + `--source <original-url>`. The script reads the file for the initial parse, then uses the `--source` host to resolve sitemap locations, crawl, and verify URLs.

## Limitations to surface to the user

- **Crawl-based generation respects robots.txt.** If `Disallow: /` is set, the crawler will produce an empty sitemap — that's correct behavior, not a bug.
- **Sample-based URL verification.** Analyze mode samples URLs (default 50) rather than verifying every one in a 50k sitemap — full verification would take hours. Bump `--verify-sample N` to widen the check.
- **JavaScript-rendered links missed.** Same caveat as `ai-search-audit` — the crawler reads raw HTML. Sites whose internal links are rendered client-side will produce undercounted sitemaps.
- **Hreflang detection from one page is heuristic.** The script trusts the `<link rel="alternate" hreflang="…">` tags found during the crawl; if your site declares them inconsistently, the generated sitemap will reflect those inconsistencies. Pair with `hreflang-audit` (when it exists) for full validation.

## Reference files

- `references/industry-patterns/saas.md` — SaaS sitemap patterns (features, pricing, docs, blog, customers, integrations)
- `references/industry-patterns/ecommerce.md` — product, collections, category trees
- `references/industry-patterns/local-service.md` — location pages, service areas, NAP signals
- `references/industry-patterns/publisher.md` — article, author, topic, archive trees
- `references/industry-patterns/agency.md` — case studies, portfolio, services, industries
- `references/industry-patterns/generic.md` — fallback when business type unclear
