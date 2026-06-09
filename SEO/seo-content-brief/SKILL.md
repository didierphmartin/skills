---
name: seo-content-brief
description: "Generate a competitive content brief for a target keyword. Use for: 'content brief for X', 'write a brief', 'content outline', 'blog brief', 'service page brief', 'plan for X article', 'help me outline X', 'SEO outline for X', 'writing brief'. Pipeline: fetch Google top-5 for the keyword via SerpAPI, filter out non-competitors (Wikipedia/Reddit/YouTube/directories/social — see references/excluded-domains.md), fetch each remaining competitor, extract their H2/H3 structure + word counts, classify search intent, identify topic/depth/quality gaps, then emit a brief: title suggestion, meta description, H2/H3 outline with per-section word targets, keyword-placement rules, PAA questions, internal-link recommendations, total word-count target. Two modes: NEW (keyword only) and IMPROVE (keyword + existing URL — distinguishes 'keep/strengthen' vs 'add new' sections). Output: BRIEF.md. Cost: ~5-6 SerpAPI calls per brief. SerpAPI key in <skill>/.env. Pairs naturally with topic-cluster (cluster → briefs for each spoke)."
dependencies: [beautifulsoup4]
fetches_urls: false
---

# seo-content-brief

> Generate a competitive SEO content brief from a target keyword.

The brief tells a writer exactly what to write to outrank the current top results: title + meta + H2/H3 outline with word targets, PAA questions to answer, keyword-placement rules, internal-link suggestions.

> ## ✅ REQUIRED FIRST STEP — ALWAYS CALL THE TOOL
>
> When the user asks for a content brief / outline / writing plan with a keyword, your first action is `run_skill_script`.
>
> ## ⛔ NO FABRICATION
>
> Every competitor, every word-count target, every PAA question must come from the actual SerpAPI + fetch data in the report.

## Invocation — exact argv shapes

```json
// New page brief — just a keyword
{ "dir_name": "seo-content-brief", "script": "scripts/audit.py", "argv": ["best running shoes for marathon"] }

// Improve mode — keyword + existing page URL
{ "argv": ["best running shoes for marathon", "--existing-url", "https://example.com/best-running-shoes"] }

// Different locale
{ "argv": ["meilleures chaussures de course", "--gl", "fr", "--hl", "fr"] }

// Tighter competitor sample
{ "argv": ["best running shoes for marathon", "--top-n", "3"] }
```

**The keyword is positional, not a flag.** The `--keyword` alias exists as a backstop.

CLI for reference:

```bash
python scripts/audit.py "best running shoes for marathon"
python scripts/audit.py "best running shoes for marathon" --existing-url https://example.com/post
python scripts/audit.py "consultant IA Québec" --gl fr --hl fr
```

## What it produces

`~/Documents/synergyAI/outputs/BRIEF.md` (or `/outputs/...` in Pyodide), containing:

- **Target keyword** + classified intent (informational / commercial / transactional)
- **Competitor analysis** table: rank, domain, word count, H2 count, H3 count, schema types
- **Recommended SERP format** (long-form guide / listicle / comparison / how-to / FAQ-rich)
- **Title suggestion** (60-110 chars) + **meta description** (150-160 chars) + **URL slug**
- **H2/H3 outline** with per-section word-count targets
- **Topic gaps** — what competitors don't cover that you should
- **Depth gaps** — topics covered too shallowly
- **Keyword placement rules** — where the primary keyword MUST appear
- **PAA questions** to address (from SerpAPI)
- **Related searches** to include semantically
- **Internal-link suggestions** (when `--existing-url` provided)
- **Total word-count target** (median of competitors + adjustment for depth)

## Modes

### NEW mode (default — keyword only)

Pipeline:
1. Fetch SerpAPI top-5 for the keyword
2. Filter out non-competitors per `references/excluded-domains.md` (Wikipedia, Reddit, YouTube, Pinterest, Amazon, government, SEO tools, job boards, directories, social platforms, news aggregators)
3. Fetch each remaining competitor page
4. Extract H2/H3 structure, word count, schema types
5. Classify search intent (rule-based: who/what/why → informational; best/top/review → commercial; buy/price/order → transactional)
6. Identify topic-gap candidates (subtopics ≥3 competitors cover that aren't in the "obvious" topic list)
7. Emit the brief

### IMPROVE mode (`--existing-url URL`)

Pipeline:
1-5 as NEW mode
6. ALSO fetch the existing URL and extract its current structure
7. Diff: "keep/strengthen" (already in the page + appears in competitors) vs "add new" (in competitors, missing from page)
8. Emit the brief with both lists clearly labeled

## Cost awareness

This skill consumes SerpAPI credits — one call to get the SERP, then per-competitor fetches don't cost SerpAPI (they go through the regular proxy). So **one brief = ~1 SerpAPI call + 4-6 page fetches**.

| `--top-n` | SerpAPI calls | Page fetches | Use |
|---|---|---|---|
| 3 | 1 | up to 5 | Quick / cheap |
| 5 (default) | 1 | up to 7 | Real brief |
| 10 | 1 | up to 12 | Comprehensive |

## Configuration

SerpAPI key in `<skill>/.env`:

```
SERPAPI_API_KEY=your_key_here
```

Falls back to the `SERPAPI_API_KEY` environment variable. Pre-populated from `serp-shape`'s key.

## Pyodide notes

- `fetches_urls: false` — the script does many fetches via the proxy
- Output detects Pyodide and routes to `/outputs/`
- SerpAPI URLs are constructed at runtime (key never in argv)

## Reference files

| File | Purpose |
|---|---|
| `references/excluded-domains.md` | Domains to filter from competitor analysis |
| `references/page-type-templates.md` | Structural templates by intent (informational/commercial/transactional) |
| `references/keyword-density.md` | Keyword placement and density rules (0.5%-2%, where the primary keyword MUST appear) |

## Limitations

- **SerpAPI cost.** Minimum 1 call per brief (more for repeated runs).
- **Static-HTML competitor extraction.** Competitor pages that render content client-side will show artificially low word counts. SerpAPI returns the ranking URL; whether the page is JS-rendered is a separate audit.
- **Heuristic competitor filtering.** The excluded-domains list catches the obvious non-competitors; edge cases may slip through (e.g. a niche directory).
- **No volume / difficulty data in v1.** Briefs are competitor-relative, not absolute. To add: pair with Google Keyword Planner (Tier 2 API integration).

## Related skills

- **`topic-cluster`** — generates a cluster of seed keywords; this skill produces briefs for each post in the cluster
- **`serp-shape`** — compares ONE existing page to top-10 (this skill compares competitor STRUCTURE for brief generation)
- **`ai-search-audit`** — run after the article is written to check AI-citation readiness
