# topic-cluster

> SERP-overlap-driven topic clustering. Take a seed keyword, build a hub-and-spoke content architecture.

## What this skill does

Content-strategy tool, distinct from the audit skills:

1. **Seed expansion** — turns one keyword into 20-30 variants (modifiers, question forms, intent variants)
2. **SERP fetch** — calls SerpAPI for Google's top-10 organic results per keyword (one API call per keyword)
3. **Pairwise overlap clustering** — counts shared URLs in top-10 across every keyword pair; groups by threshold rules
4. **Intent classification** — rule-based label per keyword (informational / commercial / transactional / navigational)
5. **Hub-and-spoke design** — picks the pillar, organizes spokes into 2-5 clusters of 2-4 posts each
6. **Internal link matrix** — bidirectional adjacency: spoke↔pillar (mandatory), spoke↔spoke within cluster (sibling)
7. **Outputs three artifacts**: machine-readable JSON, human-readable Markdown, self-contained HTML visualization

## Output

Three files in `~/Documents/synergyAI/outputs/` (or `/outputs/` in Pyodide):

| File | Format | Content |
|---|---|---|
| `cluster-plan.json` | JSON | Complete plan: keywords, intents, cluster_ids, top10 URLs, posts, links, meta |
| `cluster-plan.md` | Markdown | Pillar + clusters + per-post template/word-count/PAA + cross-cluster interlinks table |
| `cluster-map.html` | HTML | Standalone visualization. No JS dependencies. Color-coded by intent. ~3 KB. Opens in any browser. |

## Cost awareness

This skill consumes SerpAPI credits — one call per keyword.

| `--max-keywords` | SerpAPI calls | Typical use |
|---|---|---|
| 10 | 10 | Quick check / cheap test |
| 20 (default) | 20 | Real cluster plan |
| 30 | 30 | Wide net, more clusters likely |
| 50 (cap) | 50 | Comprehensive |

The hard cap is 50 to prevent accidental cost spikes.

## Configuration

SerpAPI key is read from `<skill>/.env`:

```
SERPAPI_API_KEY=your_key_here
```

Falls back to the `SERPAPI_API_KEY` environment variable. The skill folder is pre-populated with the same key as `serp-shape`.

## CLI reference

```bash
python scripts/audit.py [SEED] [OPTIONS]
```

| Flag | Default | Purpose |
|---|---|---|
| `SEED` (positional) | — | Seed keyword. Required unless `--seed`. |
| `--seed VALUE` | — | Alias for the positional argument |
| `--max-keywords N` | 20 | Number of variants to expand + fetch. Capped at 50. |
| `--gl CODE` | `us` | SerpAPI `gl` (country code) |
| `--hl CODE` | `en` | SerpAPI `hl` (language code) |
| `--location STR` | unset | SerpAPI `location` (e.g. `"New York, NY"`). Omit for broadest SERP. |
| `--concurrency N` | 4 | Concurrent SerpAPI calls |
| `-o`, `--output PATH` | `~/Documents/synergyAI/outputs/` | Output directory |

## Algorithm details

### Seed expansion (no API cost)

Variants are added in this order until `--max-keywords` is reached:

1. Bare seed
2. **Question forms**: `what is X`, `how to use X`, `why X matters`, `when to use X`, `where to learn X`, `who needs X`, `X vs`, `X alternatives`, `X pricing`, `X review`
3. **Prefix modifiers**: `best X`, `top X`, `how to X`, `why X`, `what is X`
4. **Suffix modifiers**: `X guide`, `X tutorial`, `X template`, `X checklist`, `X examples`, `X vs`, `X alternatives`, `X for beginners`, `X for small business`, `X free`, `X tools`
5. **Intent variants**: `best X`, `X comparison`, `X pricing`, `X review`

Deduplication is case-insensitive on the normalized variant string.

### Pairwise overlap clustering

For each pair of keywords (i, j), `pairwise_overlaps()` counts `|top10_i ∩ top10_j|`. Threshold rules:

| Shared URLs | Action |
|---|---|
| 7-10 | Merge into the same post (highest signal) |
| 4-6 | Same cluster |
| 2-3 | Interlink across clusters (kept for the cross-cluster recommendation table) |
| 0-1 | Separate |

Implementation: **union-find** sorted by overlap (high→low). Pairs with overlap ≥ 4 trigger `union(i, j)`. Result: connected components are the clusters.

### Pillar selection

Score per keyword = sum of pairwise overlap with all others + **+5 informational-intent boost** (pillar pages are typically broad informational). Ties broken by shorter keyword (broader-sounding). The single highest scorer becomes the pillar.

### Intent classification

Rule-based, priority order: `transactional > commercial > navigational > informational`.

| Intent | Trigger tokens |
|---|---|
| **transactional** | buy, price, pricing, cost, discount, coupon, order, subscribe, sign, demo, free, trial, download |
| **commercial** | best, top, review, comparison, compare, vs, versus, alternative, alternatives, rated, ranking |
| **informational** | how, what, why, when, where, who, guide, tutorial, learn, examples, explain, definition, meaning |
| **navigational** | login, signin, sign in, account, dashboard |

Navigational keywords are excluded from clusters (brand-specific, not content-strategy material).

### Template assignment per intent

| Intent | Template |
|---|---|
| informational | ultimate-guide |
| commercial | comparison |
| transactional | landing-page |

Word-count targets:
- Pillar: **3000 words**
- Spokes: **1500 words**

### Internal link matrix

| Link type | When generated | Direction |
|---|---|---|
| `mandatory` | every spoke ↔ pillar | bidirectional |
| `sibling` | every spoke ↔ next-2-spokes within same cluster | bidirectional |

## Python API

```python
import audit, asyncio

# Seed expansion (no API cost)
variants = audit.expand_seed("AI workflow architect", target=20)

# Intent classification (no API cost)
audit.classify_intent("best CRM software")   # → "commercial"

# Full pipeline
import argparse
args = argparse.Namespace(
    seed="AI workflow architect", max_keywords=10,
    gl="us", hl="en", location=None, concurrency=4,
    output=None, source=None, seed_alias=None,
)
asyncio.run(audit.amain(args))
```

## Pyodide notes

- `fetches_urls: false` — script does many SerpAPI calls itself
- Output detects Pyodide and routes to `/outputs/`
- SerpAPI URLs are constructed at runtime (the API key never appears in argv)

## Limitations

- **SerpAPI cost.** 20 calls minimum per run. Use `--max-keywords 10` for testing.
- **No volume/difficulty data in v1.** Cluster sizing is overlap-only, not weighted by search volume.
- **Greedy clustering, not optimal.** First-pass union-find — for edge cases with highly interconnected keywords, manual review may be needed.
- **Intent classifier is rule-based.** Ambiguous keywords (e.g. "best CRM software" is both commercial and informational) may be misclassified.
- **No content execution.** This skill produces the plan. Pairs with a separate content-writing skill in future phases.

## Reference files

| File | Purpose |
|---|---|
| `references/serp-overlap-methodology.md` | Detailed clustering algorithm description |
| `references/hub-spoke-architecture.md` | Pillar/spoke design rules |
| `references/execution-workflow.md` | Post-plan content execution guidance (LLM-consulted when paired with a content-writing skill) |

## Related skills

- **`serp-shape`** — single-keyword competitive comparison (this skill is multi-keyword content strategy)
- **`sitemap-audit`** — site-architecture for an EXISTING set of pages (this skill plans NEW pages)
- **`ai-search-audit`** — page-level audit of CONTENT (run after the cluster is built and pages written)
