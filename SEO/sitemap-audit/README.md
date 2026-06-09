# sitemap-audit

> Validate or generate XML sitemaps for a site.

## What this skill does

Two purposes:

1. **Validate** an existing sitemap — discover sitemaps at the canonical paths and via robots.txt, parse XML, run validation checks (50k cap, lastmod realism, deprecated tags, sample URL verification, location-page quality gates).
2. **Generate** a new sitemap — three sub-modes:
   - **From a crawl** — BFS internal links from a seed URL
   - **From a URL list** — read URLs from a text file
   - **Hreflang sitemap** — multi-language sitemap with `xhtml:link` variants

## Output

Mode-dependent:

| Mode | Files in `~/Documents/synergyAI/outputs/` (or `/outputs/` in Pyodide) |
|---|---|
| Analyze (default) | `VALIDATION-REPORT.md` |
| Generate (any) | `sitemap.xml` (or split `sitemap-index.xml` + `sitemap-pages-N.xml` at 50k URLs) + companion `STRUCTURE.md` |

`STRUCTURE.md` groups URLs by top-level path prefix, summarizes crawl filter drops (non-200 / noindex / non-canonical), and lists all URLs grouped by section.

## CLI reference

```bash
python scripts/audit.py [INPUT] [OPTIONS]
```

### Modes

| Flag | Purpose |
|---|---|
| `INPUT` (positional) | URL to analyze or seed-crawl from. Can be a site root (`https://example.com`) or a direct sitemap URL (`https://example.com/sitemap.xml`). Required unless `--from-list`. |
| `--url URL` | Alias for the positional argument. Backstop for tools that can't emit positional argv. |
| `--generate` | Build a new sitemap instead of analyzing |
| `--from-list FILE` | Read URLs from a text file (one per line, `#` comments). Implies `--generate`. |
| `--hreflang` | Generate-mode: emit `xhtml:link` rel="alternate" hreflang="..." variants per `<url>` |

### Crawl/generate flags

| Flag | Default | Purpose |
|---|---|---|
| `--max-pages N` | 500 | Cap pages crawled |
| `--concurrency N` | 5 | Concurrent fetch workers |
| `--ignore-canonical` | off | Don't filter pages whose canonical points elsewhere — needed for local-dev mirrors of production sites |
| `--data-attrs LIST` | `data-href,data-url,data-link,data-article,data-page,data-route,data-target` | Comma-separated attribute names whose values are treated as link sources (in addition to `<a href>`). Use for SPAs where navigation lives in `data-*` attributes. Pass `""` to disable. |
| `--force-locations` | off | Override the 50+ location-pages hard-stop |

### Analyze flags

| Flag | Default | Purpose |
|---|---|---|
| `--verify-sample N` | 50 | Number of URLs to HEAD-check for status (0 = skip verification) |

### Common flags

| Flag | Default | Purpose |
|---|---|---|
| `-o`, `--output PATH` | `~/Documents/synergyAI/outputs/` | File or directory output |
| `--stdout` | off | Analyze-mode: print report to stdout |

## Analyze pass checks

| Check | Severity | Why |
|---|---|---|
| Sitemap discoverable | critical if none | Without a sitemap, crawlers fall back to link discovery |
| Valid XML | critical | Malformed = silently dropped by Google |
| <50k URLs per file | critical | Protocol limit |
| URLs return HTTP 200 (sampled) | recommended | 404/5xx waste crawl budget |
| 3xx redirects in sitemap | recommended | Should list final URLs |
| Location-page count ≥30 / ≥50 | recommended / critical | Doorway-page risk |
| `<lastmod>` identical across all entries | nice-to-have | Reads as auto-generated |
| `<priority>` / `<changefreq>` present | info | Google has ignored both since 2017 |
| HTTP/HTTPS protocol mismatch | recommended | Confuses canonicalization |

## Generate-from-crawl behavior

The crawler:
1. Checks `robots.txt` for `User-agent: * Disallow: /` — bails with empty result if blocked
2. BFS from the seed, same-host only, drops fragments, skips non-HTML extensions
3. Resolves relative URLs against the **final URL** after redirects (critical for sites like `/cv` that 301-redirect to `/cv/`)
4. Extracts canonical, `<meta name="robots">`, hreflang, Last-Modified header
5. Scans both `<a href>` and any of the configured `data-*` attributes for link candidates

Then `filter_for_sitemap()` drops:
- HTTP status != 200 (drops 3xx redirects, 4xx, 5xx)
- noindex set in `<meta name="robots">`
- non-canonical (unless `--ignore-canonical`)

When 0 URLs survive filtering, the script prints a diagnostic listing the drop reasons and (if canonical mismatches are the cause) suggests `--ignore-canonical`.

## Quality gates (generate)

| Pattern | Threshold | Action |
|---|---|---|
| Location pages (`/locations/`, `/cities/`, `/service-area/`, `/<city-name>/`) | 30+ | Warning |
| Location pages | 50+ | Hard stop — refuse to emit without `--force-locations` |
| Identical `<lastmod>` across all entries | always | Skip the field rather than emit obvious fakes |

## Reference files

| File | Purpose |
|---|---|
| `references/industry-patterns/saas.md` | URL-pattern guidance for SaaS sites |
| `references/industry-patterns/ecommerce.md` | E-commerce |
| `references/industry-patterns/local-service.md` | Local services |
| `references/industry-patterns/publisher.md` | News/blog publishers |
| `references/industry-patterns/agency.md` | Agencies/consultancies |
| `references/industry-patterns/generic.md` | Fallback when business type is unclear |

These are LLM-consulted during interactive sitemap-structure planning — they don't drive any deterministic logic.

## Pyodide notes

- `fetches_urls: false` — the skill does its own fetches via the proxy
- Output path detects Pyodide and routes to `/outputs/` (host-mounted) instead of `~/Documents/synergyAI/outputs/` (MEMFS)
- Crawler uses `asyncio.gather` for concurrent fetches; the proxy enforces SSRF guards (loopback bypass when caller is also loopback, which is the case in local XAMPP dev)

## Limitations

- **Sample-based URL verification.** Analyze samples 50 URLs by default. Bump `--verify-sample` to widen.
- **JavaScript-rendered links missed.** Crawler reads raw HTML. The `--data-attrs` flag handles the most common SPA pattern (`data-href`/`data-article`) where URLs live in static attributes.
- **No JS-time URL construction.** If a site builds URLs at click time (`"/articles/" + slug + ".html"`), only the slug is visible — need headless browser.

## Related skills

- **`ai-search-audit`** — page-level audit (this skill is site-architecture)
- **`hreflang-audit`** — when generating hreflang sitemap, pair to validate the resulting tags
