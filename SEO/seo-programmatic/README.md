# seo-programmatic

> Audit auto-generated / templated pages for doorway-page and thin-content risk.

## What this skill does

Detects programmatic-SEO patterns at scale and flags clusters where pages are too similar (doorway-page risk) or too numerous (index bloat risk).

Pipeline:

1. **Crawl** seed URL OR read explicit URL list
2. **Cluster** URLs by inferred template pattern (e.g. `/locations/[slug]/`, `/tools/[slug]/`, `/blog/[num]/[num]/[slug]`)
3. **Sample** 3-5 pages per cluster
4. **Compute pairwise Jaccard similarity** on 5-gram word shingles of the main content
5. **Flag** clusters that trip quality gates

Quality gates:

| Trigger | Severity | Pts | Action |
|---|---|---|---|
| Cluster has 500+ pages, no `--force` | **hard stop** | 30 | Exits before report — audit 50+ pages manually first |
| Mean pairwise Jaccard ≥ similarity_threshold | **doorway** | 15 | Pages too similar — high penalty risk |
| Cluster has 100+ pages | **warn** | 5 | Verify uniqueness before scaling further |

Score = `max(0, 100 - sum_of_weights)`.

## Output

`~/Documents/synergyAI/outputs/PROGRAMMATIC-REPORT.md` (or `/outputs/...` in Pyodide), containing:
- **Score** (0-100)
- **Cluster summary table** — pattern, URL count, sample size, mean+max Jaccard, mean word count, severity emoji
- **Per-flagged-cluster detail** — sample URLs + actionable recommendation per severity
- **All clusters by size** — top 20 patterns regardless of severity (situational awareness)

## CLI reference

```bash
python scripts/audit.py [INPUT] [OPTIONS]
```

| Flag | Default | Purpose |
|---|---|---|
| `INPUT` (positional) | — | Seed URL to crawl. Required unless `--from-list`. |
| `--url URL` | — | Alias for positional argument |
| `--from-list FILE` | — | Audit an explicit URL list (one per line, `#` comments). Skips the crawl. |
| `--max-pages N` | 200 | Crawl cap |
| `--concurrency N` | 5 | Concurrent fetch workers (crawl + sample) |
| `--sample-size N` | 4 | Pages sampled per cluster for similarity |
| `--similarity-threshold X` | 0.6 | Mean pairwise Jaccard ≥ X triggers doorway flag |
| `--force` | off | Override the 500+ pages hard-stop |
| `-o`, `--output PATH` | `~/Documents/synergyAI/outputs/` | Output |
| `--stdout` | off | Print to stdout |

## URL pattern inference

`url_to_pattern()` converts a URL path into a generic pattern:

| Path segment | Pattern token |
|---|---|
| Pure digits (`2024`, `123`) | `[num]` |
| Long hyphenated (`how-to-do-an-seo-audit`) | `[slug]` |
| Very long (>20 chars) | `[slug]` |
| Short single-word (`tools`, `blog`) | kept literal |

Examples:
- `/locations/seattle/` → `/locations/[slug]/`
- `/tools/keyword-research/` → `/tools/[slug]/`
- `/blog/2024/05/19/post-title` → `/blog/[num]/[num]/[num]/[slug]`
- `/about` → `/about`

The grouping is deliberately coarse — pages with the same template should land in the same pattern even if their slugs vary widely.

## Content similarity

Per cluster:

1. **Sample** N pages (default 4)
2. **Fetch** each via the proxy
3. **Strip** `<nav>`, `<header>`, `<footer>`, `<aside>`, `<script>`, `<style>`, `<noscript>` from each
4. **Compute** word-tokenized 5-gram shingle sets
5. **Pairwise Jaccard**: `|A ∩ B| / |A ∪ B|` for every pair
6. **Mean + max** across all pairs

Interpretation:

| Mean Jaccard | Read |
|---|---|
| 0.0 - 0.2 | Pages are essentially unique content — safe |
| 0.2 - 0.4 | Some shared phrasing (intro paragraphs, boilerplate) — fine |
| 0.4 - 0.6 | Substantial overlap — review for differentiation |
| 0.6 - 0.8 | Mostly templated — likely doorway-page pattern |
| 0.8 - 1.0 | Near-identical — almost certainly doorway pages |

The 0.6 default threshold is conservative; tighten to 0.5 for stricter screening.

## Pyodide notes

- `fetches_urls: false` — script does its own crawl + sample fetches
- Output detects Pyodide and routes to `/outputs/`
- Crawler reuses the same async pattern as sitemap-audit (duplicated per the Phase 1 architectural decision)

## Limitations

- **Sample-based, not exhaustive.** Default samples 4 pages per cluster. Large clusters with high variance can sneak past the sample — bump `--sample-size` for stricter checks.
- **Jaccard on shingles is a proxy.** A site with identical boilerplate (footer, header, sidebar) but distinct critical content can score high Jaccard but be SEO-fine. The script strips obvious chrome before computing, but custom boilerplate may slip through.
- **Doesn't crawl beyond the seed URL's same-host.** No cross-domain analysis.
- **No DOM-based "main content" extraction.** Strips nav/header/footer with heuristics; SPAs without semantic HTML may give noisy results.
- **Not a substitute for manual review.** This skill identifies which clusters DESERVE manual review — humans decide whether each page is actually distinct.

## When to use

- **Before** bulk-generating programmatic pages — to design templates that produce differentiated content
- **After** generating — to verify the output doesn't trip doorway-page rules
- **Periodically** — to catch index bloat from auto-generated pages that drift toward similarity over time
- **Audit** — when inheriting a site with many programmatic pages of unknown quality

## Related skills

- **`sitemap-audit`** — has location-page quality gates too (30+/50+) but during sitemap generation, not as a standalone audit
- **`ai-search-audit`** — per-page content quality (Flesch, E-E-A-T) on one page
- **`topic-cluster`** — content STRATEGY (planning a content cluster), not an audit
