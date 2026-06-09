# seo-content-brief

> Generate a competitive SEO content brief from a target keyword.

## What this skill does

Produces a writer-ready brief that tells exactly what to write to outrank current top results. Two modes:

- **NEW mode** (keyword only) — fresh brief for a new page
- **IMPROVE mode** (`--existing-url URL`) — diffs an existing page against competitors; produces a "keep/strengthen" vs "add new" outline

Pipeline:

1. **SerpAPI top-N** for the keyword (default top-5)
2. **Filter** non-competitors — Wikipedia, Reddit, YouTube, Pinterest, Amazon, government, SEO tools, job boards, social platforms (see `references/excluded-domains.md`)
3. **Fetch** each remaining competitor; extract H2/H3 structure, word count, schema types
4. **Classify intent** (informational / commercial / transactional / navigational)
5. **Recommend SERP format** (long-form guide / listicle / comparison / how-to / landing page)
6. **Identify topic gaps** (subtopics ≥3 competitors cover — table-stakes) and **differentiation opportunities** (subtopics only 1 competitor covers)
7. **Emit brief** with title/meta/slug suggestion, H2/H3 outline with per-section word targets, keyword-placement rules, PAA + related searches, internal-link recommendations

## Output

`~/Documents/synergyAI/outputs/BRIEF-<slug>.md` (or `/outputs/...` in Pyodide). Contents:

- Target keyword, intent, recommended SERP format, word-count target
- Title + meta description + URL slug placeholders
- **Competitor analysis table** — rank, domain, word count, H2/H3 counts, schema types
- **Excluded results** listed (transparency about who was filtered)
- **Must-cover topics** with per-section word targets
- **Differentiation opportunities** — topics only one competitor covers
- **Improve-mode diff** (when `--existing-url` provided) — keep vs add lists
- **People Also Ask** (from SerpAPI)
- **Related searches** to include semantically
- **Keyword placement rules** (6-point checklist + density target)
- **Competitor heading detail** reference at the end

## CLI reference

```bash
python scripts/audit.py [KEYWORD] [OPTIONS]
```

| Flag | Default | Purpose |
|---|---|---|
| `KEYWORD` (positional) | — | Target keyword. Required unless `--keyword`. |
| `--keyword VALUE` | — | Alias for positional argument |
| `--existing-url URL` | unset | IMPROVE mode — diff against this existing page |
| `--top-n N` | 5 | Number of competitors to analyze (post-filtering) |
| `--gl CODE` | `us` | SerpAPI country code |
| `--hl CODE` | `en` | SerpAPI language code |
| `--location STR` | unset | SerpAPI location string (e.g. `"New York, NY"`) |
| `-o`, `--output PATH` | `~/Documents/synergyAI/outputs/` | Output dir |
| `--stdout` | off | Print to stdout |

## Cost awareness

| Setting | SerpAPI calls | Page fetches | Use |
|---|---|---|---|
| `--top-n 3` | 1 | up to 5 (3 competitors + 1-2 excluded) | Cheap / quick |
| `--top-n 5` (default) | 1 | up to 7 | Real brief |
| `--top-n 10` | 1 | up to 12 | Comprehensive |

One brief = **1 SerpAPI call** + N page fetches via the proxy.

## Configuration

SerpAPI key in `<skill>/.env`:

```
SERPAPI_API_KEY=your_key_here
```

Falls back to the `SERPAPI_API_KEY` environment variable. Pre-populated from `serp-shape`'s key.

## Algorithm details

### Intent classification

Rule-based, same shape as `topic-cluster`. Priority: `transactional > commercial > informational`.

| Intent | Trigger tokens |
|---|---|
| transactional | buy, price, pricing, cost, discount, coupon, order, subscribe, sign, demo, free, trial, download |
| commercial | best, top, review, comparison, compare, vs, versus, alternative, alternatives, rated, ranking |
| informational | how, what, why, when, where, who, guide, tutorial, learn, examples, explain, definition, meaning |
| navigational | (catch-all when keyword has none of the above) |

### Format recommendation

Drives the writer's structural choice:

| Trigger | Format |
|---|---|
| Keyword contains "vs" or "versus" | comparison |
| Keyword starts with "how to" | step-by-step how-to |
| Keyword starts with "best" or "top" | listicle (numbered ranking) |
| Otherwise + informational | long-form guide |
| Otherwise + commercial | listicle or comparison |
| Otherwise + transactional | landing page with offer + CTA |

### Topic gap analysis

For each competitor, extract H2 headings. Normalize (lowercase, first 4 words). Two outputs:

| Topic appears in… | Classification |
|---|---|
| ≥3 competitors | **Must-cover** — table-stakes for ranking |
| Exactly 1 competitor | **Differentiation opportunity** — potential gap to fill |

The "must-cover" list drives the outline skeleton; the "differentiation" list is the writer's choice based on what their business can credibly cover.

### Word-count target

Computed as `median(competitor_word_counts) × 1.10` — 10% above median to signal depth without bloating.

## Reference files

| File | Purpose |
|---|---|
| `references/excluded-domains.md` | Full non-competitor domain list (Wikipedia, Reddit, social, etc.) |
| `references/page-type-templates.md` | Structural templates per intent |
| `references/keyword-density.md` | Keyword placement and density rules (0.5%-2%, where the primary keyword MUST appear) |

## Pyodide notes

- `fetches_urls: false` — script does its own SerpAPI + competitor fetches
- Output detects Pyodide and routes to `/outputs/`

## Limitations

- **SerpAPI cost.** Minimum 1 call per brief.
- **Static-HTML competitor extraction.** Competitor pages that render content client-side will show artificially low word counts.
- **Heuristic competitor filtering.** Catches the obvious non-competitors; edge cases (niche directories) may slip through.
- **No volume / difficulty data in v1.** Briefs are competitor-relative, not absolute. Pair with Google Keyword Planner (Tier 2 API integration) when available.
- **Topic-gap analysis is loose.** Heading normalization takes the first 4 words; semantically identical headings phrased very differently may not group.

## Related skills

- **`topic-cluster`** — produces a cluster of seed keywords; this skill produces briefs for each post in the cluster
- **`serp-shape`** — compares an EXISTING page to top-10 (this skill compares competitor STRUCTURE for brief generation)
- **`ai-search-audit`** — run after the article is written to check AI-citation readiness
