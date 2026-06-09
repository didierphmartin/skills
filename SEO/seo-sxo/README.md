# seo-sxo

> Search Experience Optimization — detects page-type mismatches, scores against personas, derives user stories.

## What this skill does

Bridges SEO (what Google rewards) and UX (what users need). Asks: "does this page deserve to rank for this keyword given what Google is actually rewarding in the SERP?"

Pipeline:

1. **Classify** the target page's type (blog-post / listicle / comparison / product / service / landing-page / tool / how-to / glossary / homepage / category-hub)
2. **Fetch SerpAPI top-10** for the keyword
3. **Classify each SERP result** with the same taxonomy
4. **Compute SERP consensus** — dominant page type + percentage
5. **Detect mismatch** — CRITICAL if ≥60% consensus and your type differs; HIGH if 40-60%; otherwise ALIGNED or fragmented
6. **Score** target page against 4 personas on 5 axes
7. **Derive 4-6 user stories** from intent + persona gaps
8. **Output** a recommendation report

## Output

`~/Documents/synergyAI/outputs/SXO-REPORT.md` (or `/outputs/...` in Pyodide):

- **Target page classification** — type, score, signal list, detected attributes
- **SERP analysis table** — top 10 with each result's classified page type
- **SERP consensus** + mismatch severity + concrete recommendation
- **Persona-scoring table** — 4 personas × 5 axes, weighted score per persona
- **Per-persona detail** — strengths and gaps
- **User stories** — 4-6 "As a [persona], I want to [action] so that [outcome]"

## CLI reference

```bash
python scripts/audit.py [URL] [OPTIONS]
```

| Flag | Default | Purpose |
|---|---|---|
| `URL` (positional) | — | Target page URL |
| `--url URL` | — | Alias for positional argument |
| `--keyword "..."` | auto-derived | Override keyword. Otherwise derived from page title + H1. |
| `--skip-serp` | off | Skip SerpAPI + per-result fetches (persona-only — no SerpAPI cost) |
| `--gl CODE` | `us` | SerpAPI country |
| `--hl CODE` | `en` | SerpAPI language |
| `-o`, `--output PATH` | `~/Documents/synergyAI/outputs/` | Output |
| `--stdout` | off | Print to stdout |

## Page-type taxonomy

Classified by scoring signals:

| Type | URL patterns | Content signals | Schema |
|---|---|---|---|
| `blog-post` | `/blog/`, `/post/`, `/article/` | Byline + publication date, prose | `Article`, `BlogPosting`, `NewsArticle` |
| `listicle` | `/best-`, `/top-` | "best X", "top N", numbered H2s | — |
| `comparison` | `-vs-`, `-versus-`, `/compare/` | "X vs Y" in title, comparison table | — |
| `how-to` | `/how-to-`, `/guide/` | "how to X" in title, Step-numbered H2s | `HowTo` |
| `product` | `/product/`, `/shop/` | Add-to-cart, pricing, stock status | `Product` |
| `service` | `/service/`, `/our-work/` | Book/contact CTAs | `Service`, `LocalBusiness` |
| `landing-page` | — | Single CTA, short, conversion-focused | — |
| `tool` | — | 3+ `<input>` elements, calculator | — |
| `glossary` | `/glossary/`, `/define/` | "What is X" + short word count + definition pattern | — |
| `homepage` | `/` | Multi-purpose nav hub | `WebSite` |
| `category-hub` | `/category/`, `/topics/` | ItemList linking to children | `ItemList` |

## Mismatch severity

| SERP consensus | Your page type matches? | Severity |
|---|---|---|
| ≥60% one type | yes | ✅ ALIGNED |
| ≥60% one type | no | 🛑 CRITICAL |
| 40-60% one type | yes | ✅ |
| 40-60% one type | no | ⚠️ HIGH |
| <40% (fragmented) | — | ⚠️ Differentiation opportunity |

## Persona scoring

4 personas × 5 axes. Each axis is 0-10. Final per-persona score is weighted (weights sum to 10):

| Persona | What they need | Top-weighted axes |
|---|---|---|
| `researcher` | Comprehensive, citable, balanced | depth (4), trust (3) |
| `decision-maker` | Quick scan, key facts | clarity (3), action (2), scannability (2) |
| `buyer` | Pricing, CTA, social proof | action (3), trust (3) |
| `new-visitor` | Orientation, "is this for me?" | clarity (3.5), action (2.5) |

The 5 axes:
1. **Clarity** — 5-second comprehension
2. **Depth** — answers follow-ups
3. **Trust** — byline, dates, schema, social proof
4. **Action** — obvious next step
5. **Scannability** — headings, lists, tables

Gaps are surfaced when an axis scores ≤4 AND that axis is weighted ≥2 for the persona.

## User stories

Format: `As a [persona], I want to [action] so that [outcome].`

Two derivation paths:

1. **From SERP mismatch** — when your page type ≠ SERP consensus, emit "As a searcher for X, I want [the SERP-consensus format] so that I can match Google's top results"
2. **From persona gaps** — for each persona with gaps on weighted axes, emit a story whose action+outcome maps to the gap (clarity → "understand in 5 seconds"; action → "see a clear next step"; etc.)

Always emits at least 3 stories; capped at 6.

## Cost awareness

| Mode | SerpAPI calls | Page fetches |
|---|---|---|
| Full (default) | 1 | 11 (target + 10 SERP results) |
| `--skip-serp` | 0 | 1 (target only — no mismatch detection) |

## Configuration

SerpAPI key in `<skill>/.env`:

```
SERPAPI_API_KEY=your_key_here
```

Falls back to the `SERPAPI_API_KEY` environment variable. Pre-populated from `serp-shape`.

## Reference files

| File | Purpose |
|---|---|
| `references/page-type-taxonomy.md` | Full classification rules (lifted from claude-seo) |
| `references/persona-scoring.md` | Scoring rubric and persona definitions |
| `references/user-story-framework.md` | User-story derivation guide |

## Pyodide notes

- `fetches_urls: false` — script does its own SerpAPI + per-page fetches
- Output detects Pyodide and routes to `/outputs/`
- 11 concurrent fetches via `asyncio.gather` with semaphore (default 5)

## Limitations

- **Page-type classification is heuristic.** Edge cases (product pages with long-form content; landing pages that look like blog posts) may misclassify. Reasons list shows why so the LLM can sanity-check.
- **Persona scoring is rule-based.** Catches obvious gaps (no CTA for a buyer; no author for a researcher) but won't catch subtle UX issues.
- **One-page-at-a-time.** No site-wide SXO audit in v1.
- **SerpAPI cost** — minimum 1 call per analysis.
- **No actual user testing.** Heuristic proxy for UX — pair with real user research for high-stakes pages.

## Related skills

- **`ai-search-audit`** — page QUALITY (this skill is page FIT with SERP)
- **`serp-shape`** — content-shape comparison to top-10 (this skill is page-TYPE classification mismatch)
- **`seo-content-brief`** — when SXO surfaces a mismatch, generate a brief for the right page type
