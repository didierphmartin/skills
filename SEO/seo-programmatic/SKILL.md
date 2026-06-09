---
name: seo-programmatic
description: "Audit programmatic SEO pages — detect at-scale doorway-page patterns, thin-content risk, index bloat from auto-generated pages. Use for: 'programmatic SEO audit', 'check my city pages', 'detect doorway pages', 'are my generated pages too similar', 'audit location pages', 'thin content at scale', 'index bloat check'. Pipeline: crawl seed URL (or accept URL list), group URLs into pattern clusters (e.g. /locations/<city>, /tools/<name>, /integrations/<platform>), sample 3-5 pages per cluster, compute pairwise content similarity (Jaccard on n-gram shingles), flag clusters where similarity ≥60% as doorway-page risk. Quality gates: WARNING at 100+ programmatic pages, HARD STOP at 500+ without --force, flag clusters with <40% unique content. Output: PROGRAMMATIC-REPORT.md with cluster table (pattern, count, uniqueness score, sample URLs) + recommendations. Useful BEFORE bulk-generating pages (template review) AND AFTER (audit existing patterns)."
dependencies: [beautifulsoup4]
fetches_urls: false
---

# seo-programmatic

Audit auto-generated / templated pages for thin-content and doorway-page risk.

> ## ✅ REQUIRED FIRST STEP — ALWAYS CALL THE TOOL
>
> When the user asks anything programmatic-SEO related and gives a seed URL, your first action is `run_skill_script`. Localhost URLs supported.
>
> ## ⛔ NO FABRICATION
>
> Every cluster, every uniqueness score, every flagged URL must come from the actual report. Never produce a programmatic audit from inference.

## Invocation — exact argv shapes

```json
// Crawl + audit (most common)
{ "dir_name": "seo-programmatic", "script": "scripts/audit.py", "argv": ["https://example.com"] }

// Cap pages crawled
{ "argv": ["https://example.com", "--max-pages", "200"] }

// Override the 500+ pages hard stop (after manual content review)
{ "argv": ["https://example.com", "--force"] }

// Audit a URL list instead of crawling
{ "argv": ["--from-list", "/path/to/urls.txt"] }
```

CLI for reference:

```bash
# Crawl + cluster + sample-and-compare
python scripts/audit.py https://example.com --max-pages 200

# Audit an explicit URL list
python scripts/audit.py --from-list urls.txt

# Tweak similarity threshold (default 60%)
python scripts/audit.py https://example.com --similarity-threshold 0.5
```

## What it checks

1. **URL pattern clustering** — group URLs by inferred template. Examples:
   - `/locations/[city]/` → all city pages cluster together
   - `/tools/[name]/` → all tool pages cluster together
   - `/integrations/[platform]/` → all integration pages cluster together
   - `/glossary/[term]/` → all glossary entries cluster together

2. **Quality gates per cluster** — count + sample-based uniqueness check:
   - **100+ pages** → warning
   - **500+ pages** → hard stop unless `--force` is passed
   - **Content similarity ≥60%** (sample of 3-5 pages, Jaccard on 5-gram shingles) → doorway-page flag
   - **<40% unique content per page** → thin-content flag

3. **Recommendations**:
   - Identify the most-templated clusters
   - Suggest what to add for genuine differentiation per page-type
   - Flag URL-only-differs patterns (e.g. only the city name changes between location pages)

## Output

`~/Documents/synergyAI/outputs/PROGRAMMATIC-REPORT.md` (or `/outputs/...` in Pyodide), containing:
- **Score** (0-100)
- **Cluster summary table** — pattern, count, sample-pages-checked, mean Jaccard similarity, severity
- **Per-cluster detail** with sample URLs and flagged duplicates
- **Recommendations** — which clusters need rework, which are fine

## Why this matters

Google penalizes "doorway pages" — many near-duplicate pages targeting slight keyword variations (e.g. `Plumbers in Seattle`, `Plumbers in Portland`, `Plumbers in Tacoma` where only the city name changes). This skill catches the pattern BEFORE it triggers a manual action.

Mid-sized programmatic SEO done well (Zapier integrations, G2 product pages, dictionary sites) ranks superbly. Done poorly, it tanks the entire domain. The line is content uniqueness per page.

## Pyodide notes

- `fetches_urls: false` — skill does its own crawl + per-page fetches
- Output detects Pyodide and routes to `/outputs/`
- Crawler reuses the same async pattern as sitemap-audit

## Limitations

- **Sample-based similarity.** Default samples 3-5 pages per cluster; bigger samples cost more time but catch more variance. Tune via `--sample-size`.
- **N-gram Jaccard is a proxy.** A site with identical boilerplate but distinct critical content (price tables, specs, reviews) may score similar on Jaccard but be SEO-fine. The report includes raw similarity AND flags content-block ratios so humans can disambiguate.
- **No DOM-based "main content" extraction.** Strips nav/header/footer with heuristics; SPAs without semantic HTML may give noisy results.
- **Not a substitute for manual content review.** The skill identifies which clusters to look at; humans decide whether each page is actually distinct enough.

## Related skills

- **`sitemap-audit`** — has location-page quality gates too (30+/50+) but for sitemap generation, not standalone audit
- **`ai-search-audit`** — per-page content quality (Flesch, E-E-A-T) on a single page
- **`topic-cluster`** — content STRATEGY (planning a cluster), not an audit of an existing one
