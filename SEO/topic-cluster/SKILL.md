---
name: topic-cluster
description: "Build a SERP-overlap-driven topic cluster (hub-and-spoke architecture) from a seed keyword. Triggers: 'topic cluster for X', 'content cluster', 'pillar page architecture', 'hub and spoke for X', 'keyword grouping by SERP overlap', 'plan content cluster', 'build content silo'. Pipeline: expand seed → 20-30 keyword variants (modifiers, questions, intent), fetch Google top-10 per keyword via SerpAPI, cluster by pairwise top-10 URL overlap (7-10 = merge; 4-6 = same cluster; 2-3 = interlink; 0-1 = separate), classify intent (informational/commercial/transactional/navigational — navigational excluded), pick pillar, design 2-5 clusters × 2-4 spokes, generate internal link matrix. Output: cluster-plan.json, cluster-plan.md, cluster-map.html. Cost: ~N SerpAPI calls per run where N = --max-keywords (default 20). SerpAPI key required in <skill>/.env."
dependencies: [beautifulsoup4]
# fetches_urls: false because the script makes many SerpAPI calls plus per-page
# fetches through the proxy. The bridge's single-URL prefetch is wasted.
fetches_urls: false
---

# topic-cluster

SERP-overlap-driven topic clustering. The structural counterpart to `ai-search-audit` (page-level) and `sitemap-audit` (site-level): `topic-cluster` operates at the **content-strategy** level. It takes a seed keyword you want to rank for and designs a content architecture — pillar page + 2-5 spoke-clusters of 2-4 posts each — by clustering keyword variants based on actual Google top-10 overlap (not text similarity).

> ## ✅ REQUIRED FIRST STEP — ALWAYS CALL THE TOOL
>
> When the user asks anything cluster-related and gives a seed keyword, your first action is `run_skill_script` with `dir_name: topic-cluster` and `scripts/audit.py`. Localhost URLs and arbitrary keywords are fully supported.
>
> ## ⛔ NO FABRICATION
>
> Every cluster, every overlap count, every assignment must come from the actual SerpAPI data. Never produce a cluster plan from inference. If the SerpAPI key is missing or the API fails, surface the error and stop.

## Invocation — exact argv shapes

The seed keyword is **positional**, not a flag.

```json
// Default (20 keywords, US English)
{ "dir_name": "topic-cluster", "script": "scripts/audit.py", "argv": ["AI workflow architect"] }

// Custom keyword count + locale
{ "argv": ["AI workflow architect", "--max-keywords", "30", "--gl", "us", "--hl", "en"] }

// Smallest possible run (10 keywords — lowest SerpAPI cost)
{ "argv": ["AI workflow architect", "--max-keywords", "10"] }
```

**Do NOT pass the seed as a flag.** The seed is positional. (The `--seed` alias exists as a backstop for tools that cannot emit positional argv.)

CLI for reference:

```bash
# Default cluster plan (20 keywords)
python scripts/audit.py "AI workflow architect"

# Smaller / cheaper run
python scripts/audit.py "AI workflow architect" --max-keywords 10

# Different locale
python scripts/audit.py "consultant IA" --gl fr --hl fr

# Custom output
python scripts/audit.py "AI workflow architect" -o /path/to/output/dir
```

## What it does

1. **Seed expansion** — append modifiers (best/how to/vs/guide/template/checklist/examples), question forms (who/what/when/where/why/how), intent modifiers (pricing/review/alternative/comparison/free). Target 20-30 unique variants by default; capped at `--max-keywords` (default 20).

2. **SERP fetch** — for each keyword, fetch Google's top-10 organic results via SerpAPI. One API call per keyword.

3. **Pairwise overlap clustering** — for each keyword pair, count shared URLs in the top-10. Threshold rules:

   | Shared URLs | Relationship | Action |
   |---|---|---|
   | 7-10 | Same post | Merge into one target page |
   | 4-6 | Same cluster | Group under the same spoke cluster |
   | 2-3 | Interlink | Place in adjacent clusters with cross-links |
   | 0-1 | Separate | Different clusters or excluded |

4. **Intent classification** — rule-based label per keyword: `informational`, `commercial`, `transactional`, `navigational`. Navigational keywords are excluded from clustering (brand-specific).

5. **Hub-and-spoke design** — pillar is the keyword with highest aggregate overlap and broadest informational intent. Spokes group into 2-5 clusters of 2-4 posts each. Template type per post inferred from intent (informational→ultimate-guide; commercial→comparison; etc.).

6. **Internal link matrix** — bidirectional adjacency list: spoke→pillar (mandatory), pillar→spoke (mandatory), spoke↔spoke within cluster (2-3 links), cross-cluster spoke→spoke (0-1 link).

## Output

Three files in `~/Documents/synergyAI/outputs/` (or `/outputs/` in Pyodide):

| File | Format | Purpose |
|---|---|---|
| `cluster-plan.json` | JSON | Machine-readable full plan: keywords, intents, clusters, link matrix |
| `cluster-plan.md` | Markdown | Human-readable summary: pillar + clusters + per-post details |
| `cluster-map.html` | HTML | Static visualization (no JS frameworks, opens in any browser) |

## Cost awareness

This skill consumes SerpAPI credits. Each run = one SerpAPI call per keyword. Default 20 keywords = 20 calls. Adjust `--max-keywords` to control cost:

| --max-keywords | SerpAPI calls | Typical use |
|---|---|---|
| 10 | 10 | Quick check / proof of concept |
| 20 (default) | 20 | Real cluster plan |
| 30 | 30 | Wide-net plan, more clusters |
| 50 (cap) | 50 | Comprehensive |

The SerpAPI key is read from `<skill>/.env` (same pattern as `serp-shape`). Required field: `SERPAPI_API_KEY=...`.

## When NOT to use this skill

- **You already have a content list and want a sitemap** → use `sitemap-audit --from-list` instead
- **You want to compare ONE page to top-10 results for ONE keyword** → use `serp-shape`
- **You want to audit existing content** → use `ai-search-audit` per-page or `sitemap-audit` for site-wide

## Pyodide notes

Routes SerpAPI calls through `/gpt/backend/api/v1/fetch-url` (same proxy pattern as `serp-shape`). Default output is `/outputs/` in Pyodide and `~/Documents/synergyAI/outputs/` in CPython.

## Reference files

- `references/serp-overlap-methodology.md` — full clustering algorithm
- `references/hub-spoke-architecture.md` — pillar/spoke design rules
- `references/execution-workflow.md` — post-plan content-execution guidance (when paired with a content-writing skill)

## Limitations

- **SerpAPI cost.** 20 calls minimum per run. Use `--max-keywords 10` for testing.
- **No volume/difficulty data** in v1. Cluster sizing is based on overlap only, not search volume. Volume data (Google Keyword Planner / DataForSEO) is a Phase-2 enhancement.
- **Greedy clustering.** The algorithm is a first-pass greedy grouping — not k-means or optimal partitioning. For most cases the result is good; edge cases (highly interconnected keywords) may need manual review.
- **Intent classifier is rule-based.** Catches obvious cases; ambiguous keywords (e.g. "best CRM software" is both commercial and informational) may be misclassified.
