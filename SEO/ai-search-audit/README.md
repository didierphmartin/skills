# ai-search-audit

> SEO + AI-search citation-readiness audit for an HTML page (file or URL).

## What this skill does

Scores how well an HTML page is set up to be cited by AI search engines and LLM overviews — Google AI Overviews, ChatGPT Search, Perplexity, Bing Copilot — and traditional search at the same time. The two optimization profiles overlap substantially; this skill covers what most users mean by "SEO audit" plus what's specific to AI citation.

Seven audit passes:

1. **Cognitive structure** — heading hierarchy, section length, lead-sentence thesis, list/table density, citation density, paragraph length, HTML5 landmarks
2. **Machine-readable metadata** — JSON-LD detection + deprecated/restricted-type flagging + required-property validation + Microdata/RDFa fallback detection + Open Graph + Twitter Card + canonical + robots + html lang
3. **Content quality (E-E-A-T)** — Flesch Reading Ease, author byline detection, freshness signals (datePublished/dateModified), internal/external link ratios
4. **AI-citation signals (GEO)** — passage-length citability scoring (134-167 word sweet spot), definition-pattern detection, answer-first heading check
5. **Technical signals** *(merged from claude-seo's `seo-technical`)* — URL structure flags (length >100, trailing-slash consistency vs canonical), mobile viewport meta tag, JS rendering detection (empty SPA shell heuristic), IndexNow protocol declaration
6. **Asset hygiene** — title tag length, image alt-text coverage, empty/fragment-only links
7. **External signals** *(only when source is a live URL)* — AI-crawler robots.txt audit, `/llms.txt` presence, security headers (CSP, HSTS, X-Frame-Options, X-Content-Type-Options, Referrer-Policy)

## Output

A scored Markdown report at `~/Documents/synergyAI/outputs/<stem>-audit.md` (or `/outputs/<stem>-audit.md` in Pyodide), containing:

- **Score** (0-100) and **grade** (A-F)
- **Strengths** list
- **Findings** grouped by category, each with severity, what, why it matters, and the fix
- **Stats** dict with measurements (Flesch score, alt coverage, word count, citation density, etc.)

In batch / crawl mode, additionally writes `batch-summary.md` with a comparison table and the most common issues across pages.

## Scoring

| Severity | Pts deducted | When emitted |
|---|---|---|
| **critical** | −10 | Load-bearing signal missing (no JSON-LD, no meta description, noindex, AI search crawlers blocked) |
| **recommended** | −3 | Sub-optimal structure that measurably reduces citation rate |
| **nice-to-have** | −1 | Minor polish |

Score = `max(0, 100 - sum_of_weights)`.

## CLI reference

```bash
python scripts/audit.py [INPUT] [OPTIONS]
```

### Modes

| Flag | Purpose |
|---|---|
| `INPUT` (positional) | URL, bare domain (auto-prepends `https://`), or local `.html` file path. Required unless `--stdin` or `--batch-file`. |
| `--stdin` | Read HTML from stdin. Pair with `--source URL` to label the report. Needed for Pyodide pre-fetched pages. |
| `--source URL` | When reading from stdin or a pre-fetched file, this is the original URL — used as the report label and to enable external-signal fetches (robots.txt, /llms.txt). |
| `--crawl` | Treat INPUT as a seed URL, BFS-walk same-host internal links, audit each page. |
| `--batch-file PATH` | Read URLs/file paths from a text file (`#` for comments). Mutually exclusive with `--crawl`. |

### Common flags

| Flag | Default | Purpose |
|---|---|---|
| `-o`, `--output PATH` | `~/Documents/synergyAI/outputs/<stem>-audit.md` | Single-mode output file |
| `--main-selector CSS` | auto-detect | CSS selector for the article container. Auto-detects `<main>`, `<article>`, `[role="main"]`, `#content`, etc. |
| `--stdout` | off | Print report to stdout instead of writing a file (single mode only) |

### Crawl/batch flags

| Flag | Default | Purpose |
|---|---|---|
| `--max-pages N` | 25 | Cap pages crawled |
| `--crawl-delay SEC` | 0.5 | Seconds between fetches during the discovery phase |
| `--summary-output PATH` | `~/Documents/synergyAI/outputs/batch-summary.md` | Where to write the batch summary |
| `--workers N` | 8 | Parallel audit workers |

## Python API

```python
import audit

# Sync — in-process passes only (no external fetches)
result = audit.audit_html(html, source="https://example.com/page")
md = audit.audit_html_to_markdown(html, source="https://example.com/page")

# Async — adds the external-signals pass (robots.txt, llms.txt, security headers)
import asyncio
result = asyncio.run(audit.audit_html_full(html, source="https://example.com/page"))
md = asyncio.run(audit.audit_html_to_markdown_full(html, source="https://example.com/page"))

# Inspect findings
for f in result.findings:
    print(f.severity, f.category, f.title)
print("Score:", result.score(), "Grade:", result.grade())
print("Stats:", result.stats)
```

## Key reference tables

### Deprecated schema types (flagged in metadata pass)

| Type | Retired |
|---|---|
| HowTo | Sept 2023 |
| SpecialAnnouncement | July 2025 |
| CourseInfo, EstimatedSalary, LearningVideo | June 2025 |
| ClaimReview, VehicleListing | June 2025 |
| PracticeProblem, Dataset (rich results) | late 2025 |

### Restricted schema types

| Type | Restriction |
|---|---|
| FAQPage | Google rich results gov/healthcare only since Aug 2023; still useful for AI citation |
| QAPage | Community-Q&A only |

### Required-property validation per `@type` (subset)

`Article`/`BlogPosting`/`NewsArticle` → headline, author, datePublished
`Organization` → name, url
`LocalBusiness` → name, address
`Product` → name
`BreadcrumbList` → itemListElement
`Event` → name, startDate, location
`VideoObject` → name, description, thumbnailUrl, uploadDate

### AI crawlers audited in robots.txt

GPTBot, OAI-SearchBot, ChatGPT-User, ClaudeBot, anthropic-ai, Claude-Web, PerplexityBot, Perplexity-User, Google-Extended, Bytespider, CCBot, cohere-ai.

The audit distinguishes:
- **Live AI search crawlers** (blocking these removes you from ChatGPT Search, Perplexity, etc.) → **critical** finding
- **Training-corpus crawlers** (blocking is a deliberate opt-out, doesn't affect search) → informational

### Security headers checked

`Content-Security-Policy`, `Strict-Transport-Security`, `X-Frame-Options`, `X-Content-Type-Options`, `Referrer-Policy`. Grouped by severity in the report.

## Pyodide notes

- The HTML must be pre-fetched server-side (browser CORS blocks cross-origin Python fetches). The bridge auto-injects this when `fetches_urls: true` is in the frontmatter — the URL is replaced with a local `/tmp/prefetched_*.html` file path and `--source <original-url>` is appended.
- The external-signals pass uses async `pyodide.http.pyfetch` against `/gpt/backend/api/v1/fetch-url` — same-origin so CORS does not apply.
- Output goes to `/outputs/` (host-mounted) when running under Pyodide; `~/Documents/synergyAI/outputs/` in CPython.

## Limitations

- **Heuristic, not predictive.** Identifies missing prerequisites for citation, not guaranteed outcomes.
- **Static analysis only.** Doesn't measure page speed, mobile rendering, or interactive behavior. Pair with Lighthouse.
- **No backlink / domain-authority data.** Looks at the page in isolation.
- **JavaScript-rendered content invisible.** Reads raw HTML; SPAs without SSR show nearly empty.

## Related skills

- **`schema-generate`** — produces JSON-LD this audit reports as missing
- **`sitemap-audit`** — site-architecture counterpart (this skill audits one page; sitemap-audit audits the site map)
- **`image-audit`** — image-specific counterpart (deeper image checks than this skill's asset-hygiene pass)
- **`serp-shape`** — competitive counterpart (this skill is absolute; serp-shape is relative-to-top-10)
