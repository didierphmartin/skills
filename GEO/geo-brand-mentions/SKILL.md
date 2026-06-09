---
name: geo-brand-mentions
description: "Score a brand's authority across the platforms AI systems rely on for entity recognition and citation — YouTube, Reddit, Wikipedia, LinkedIn, plus Quora/Stack Overflow/GitHub/News. Use for: 'brand mentions audit', 'brand authority score', 'is my brand recognized by AI', 'AI entity recognition check', 'where is my brand discussed', 'YouTube/Reddit/Wikipedia presence', 'why doesn't ChatGPT know about my brand'. Pipeline: deterministic Wikipedia + Wikidata API check via run_skill_script (search both, fetch the article extract, count Wikidata properties — these JSON APIs are reliable; web search alone misses Wikipedia presence regularly) -> the LLM uses SynergyAI's serpApiSearch or braveSearch tools for YouTube / Reddit / LinkedIn / Quora / Stack Overflow / GitHub / Hacker News / news mentions -> aggregate into a 0-100 Brand Authority Score weighted YouTube 25% + Reddit 25% + Wikipedia 20% + LinkedIn 15% + Other 15%, per the Ahrefs Dec 2025 correlation study of 75K brands."
dependencies: []
fetches_urls: false
---

# geo-brand-mentions

Score a brand's authority across the platforms AI systems weight most heavily
for entity recognition and citation. Per the Ahrefs Dec 2025 study of 75K
brands, **unlinked mentions on YouTube and Reddit correlate ~3× more strongly
with AI visibility than traditional backlinks** — this inverts traditional
SEO and is the foundation of the GEO entity strategy.

> ## ✅ REQUIRED FIRST STEP — RUN THE WIKIPEDIA / WIKIDATA SCRIPT, THEN SEARCH
>
> When the user asks for a brand mentions / authority / entity audit, follow
> this exact sequence:
>
> 1. **First**, `run_skill_script` with `dir_name: geo-brand-mentions` and
>    `scripts/wikipedia_lookup.py` and `argv: ["<brand name>"]` (add
>    `--founder "<founder name>"` if known). This hits the Wikipedia and
>    Wikidata APIs directly, which is reliable.
> 2. **Then**, use SynergyAI's `serpApiSearch` or `braveSearch` tool (the
>    built-in LLM tools, NOT `run_skill_script`) for each of the other
>    platforms, using the query patterns in the "Platform search queries"
>    section below.
> 3. **Finally**, aggregate the platform scores into the composite Brand
>    Authority Score using the weighting table.
>
> If a sub-tool is missing, run what you can and produce a partial report
> flagging the missing dimensions.
>
> ## ⛔ NO FABRICATION
>
> - **Wikipedia / Wikidata presence MUST come from the script.** Web search
>   alone misses Wikipedia presence regularly (the original Claude Code skill
>   explicitly called this out). If the script says a page exists with high
>   confidence, that's authoritative — do not override with a search result
>   that disagrees.
> - **Search-based platform scoring** must cite specific results from the
>   `serpApiSearch` / `braveSearch` output. If you search "brand site:youtube.com"
>   and get N results, report N — don't round to make the report look
>   tidier. If the search returned zero results, the score for that platform
>   is 0-9, not "estimated 30/100".
> - **Sentiment claims** must cite specific posts/comments returned by the
>   search. "Reddit sentiment is mostly positive" needs to be grounded in
>   at least one quoted Reddit search result.

## Invocation — script

```json
// Brand name only
{ "dir_name": "geo-brand-mentions", "script": "scripts/wikipedia_lookup.py", "argv": ["Anthropic"] }
```

```json
// Brand + founder (founder gets its own Wikipedia lookup)
{ "argv": ["Anthropic", "--founder", "Dario Amodei"] }
```

```json
// Founder only (no brand article expected — research the person directly)
{ "argv": ["Dario Amodei", "--founder-only"] }
```

The script outputs structured JSON + Markdown covering: brand Wikipedia
article (exists / URL / extract / match confidence), brand Wikidata entry
(QID / label / description / property count), and the same for the founder
if specified.

## Platform search queries (LLM uses serpApiSearch / braveSearch)

After the Wikipedia/Wikidata script returns, run these searches:

| Platform | Search query patterns |
|---|---|
| **YouTube** | `<brand> site:youtube.com` · `"<brand>" site:youtube.com` · check `youtube.com/@<brand-slug>` directly |
| **Reddit** | `<brand> site:reddit.com` · `"<brand>" site:reddit.com` · `reddit.com/r/<brand-slug>` · check sentiment from snippet text |
| **LinkedIn** | `<brand> site:linkedin.com` · check `linkedin.com/company/<brand-slug>` |
| **Quora** | `<brand> site:quora.com` |
| **Stack Overflow** | `<brand> site:stackoverflow.com` (only for technical brands) |
| **GitHub** | `<brand> site:github.com` (only for technical / dev-tool brands) |
| **Hacker News** | `<brand> site:news.ycombinator.com` |
| **News** | `"<brand>"` filtered to the last 6 months — look for major outlets |
| **Podcasts** | `<brand> podcast` (transcripts increasingly indexed by AI) |

For each platform, collect: result count (proxy for mention volume), top 3-5
URLs and snippets (for sentiment + context), and any official-account URLs
(channel / subreddit / company-page).

## Platform importance + per-platform rubrics

### YouTube (25% — strongest correlation, ~0.737)

| Score | Criteria |
|---|---|
| 90-100 | Active channel ≥10K subs, regular uploads, brand in 20+ third-party videos, top YouTube results for industry terms |
| 70-89 | Active channel ≥1K subs, 10-19 third-party video mentions |
| 50-69 | Channel exists with some content, 5-9 third-party mentions |
| 30-49 | Channel exists but inactive, 1-4 third-party mentions |
| 10-29 | No channel; brand in 1-2 videos |
| 0-9 | No YouTube presence |

### Reddit (25%)

| Score | Criteria |
|---|---|
| 90-100 | Frequently recommended in relevant subreddits, mostly positive, active official presence, own subreddit ≥5K members |
| 70-89 | Regular mentions across relevant subreddits, mostly positive, some official presence |
| 50-69 | Mentioned in several threads, mixed sentiment, recognized by community |
| 30-49 | Occasional mentions in 1-2 subreddits, no official presence |
| 10-29 | Rare mentions, largely unknown |
| 0-9 | No Reddit presence |

### Wikipedia / Wikidata (20%)

| Score | Criteria |
|---|---|
| 90-100 | Detailed Wikipedia article (B-class+), complete Wikidata, brand cited as reference in multiple articles, founder also has a page |
| 70-89 | Wikipedia article (start-class+), Wikidata entry, mentioned in 2+ other articles |
| 50-69 | Wikipedia article (stub/start), basic Wikidata, limited cross-article mentions |
| 30-49 | No Wikipedia article but mentioned in other articles OR Wikidata exists |
| 10-29 | One passing reference in another article |
| 0-9 | No Wikipedia or Wikidata presence |

Source: the `wikipedia_lookup.py` script's output. Article quality is the
LLM's judgment from the article extract length / completeness.

### LinkedIn (15%)

| Score | Criteria |
|---|---|
| 90-100 | Active company page ≥10K followers, leadership posts regularly, frequent third-party mentions, strong employee profiles |
| 70-89 | Active page ≥5K followers, some employee thought leadership |
| 50-69 | Page ≥1K followers, irregular posting |
| 30-49 | Sparse / inactive page, few followers |
| 10-29 | Minimal information |
| 0-9 | No LinkedIn page |

### Other platforms (15% combined)

Score each that's relevant to the brand type:

- **Quora** — moderate for B2C, lower for B2B
- **Stack Overflow** — high for technical brands; skip for non-technical
- **GitHub** — high for dev-tool / open-source; skip for non-technical
- **Hacker News** — moderate, valuable for tech startups
- **News mentions** — moderate; recency matters (last 6 months >> 3 years)
- **Podcasts** — growing source of AI training data

Average the relevant ones; if a platform doesn't apply to the brand type
(e.g. GitHub for a restaurant), exclude rather than penalize.

## Composite Brand Authority Score

```
Brand Authority = (YouTube × 0.25) + (Reddit × 0.25)
                + (Wikipedia × 0.20) + (LinkedIn × 0.15)
                + (Other × 0.15)
```

| Score | Rating | Interpretation |
|---|---|---|
| 85-100 | Dominant | Well-recognized entity across AI platforms; high citation likelihood |
| 70-84 | Strong | Solid cross-platform presence; AI likely cites for relevant queries |
| 50-69 | Moderate | Gaps exist; AI citation inconsistent |
| 30-49 | Weak | Limited platform presence; AI may not recognize as a distinct entity |
| 0-29 | Minimal | Negligible presence; AI unlikely to cite |

## Output format — produce `GEO-BRAND-MENTIONS.md`

```markdown
# Brand Authority Report: [Brand]

**Date:** [date] · **Brand:** [brand] · **Domain:** [URL]
**Industry:** [from user or inferred]

## Brand Authority Score: [X]/100 ([Rating])

### Platform breakdown
| Platform | Score | Weight | Weighted | Status |
|---|---|---|---|---|
| YouTube | X/100 | 25% | X | [Active channel / Mentioned / Absent] |
| Reddit | X/100 | 25% | X | [Active / Discussed / Absent] |
| Wikipedia / Wikidata | X/100 | 20% | X | [Article / Mentioned / Absent] |
| LinkedIn | X/100 | 15% | X | [Active / Basic / Absent] |
| Other | X/100 | 15% | X | [Summary] |
| **Total** | | | **X/100** | |

## Platform detail

### YouTube (X/100)
**Channel:** [URL or "Not found"] · **Subs:** [count] · **Videos:** [count]
**Third-party mentions (from search):** [N results]
Key findings: [...]

### Reddit (X/100)
**Official account:** [URL or "Not found"] · **Own subreddit:** [URL or "None"]
**Subreddits with discussion:** [...]
**Sentiment:** [Positive / Negative / Neutral / Mixed] — cite specific posts
Key findings: [...]

### Wikipedia / Wikidata (X/100) — from script
**Article:** [URL or "None"] (confidence: [exact / partial / none])
**Extract:** [first 200 words from the script]
**Wikidata QID:** [Q-number] · **Properties:** [count]
**Founder article:** [URL or "None"] (if checked)

### LinkedIn (X/100)
**Company page:** [URL or "Not found"] · **Followers:** [count]
**Post frequency:** [Weekly / Monthly / Rare]
Key findings: [...]

### Other platforms
| Platform | Presence | Notes |
|---|---|---|
| Quora | [Yes/No] | [N results from search] |
| Stack Overflow | [Yes/No/N/A for non-tech] | [N results] |
| GitHub | [Yes/No/N/A for non-tech] | [N results] |
| Hacker News | [Yes/No] | [N results] |
| News (last 6mo) | [Yes/No] | [Outlet samples] |

## Recommendations

### Immediate (Week 1-2)
[3 specific, actionable items per the weakest platforms]

### Short-term (Month 1-3)
[3-5 items]

### Long-term (Month 3-12)
[2-3 items — Wikipedia / Wikidata strategies, sustained content programs]

## Key takeaway
[1-2 sentences — the single most impactful action]
```

## Pyodide notes

The Wikipedia / Wikidata script uses only `urllib` (CPython) or
`pyodide.http` via the SynergyAI proxy (`/gpt/backend/api/v1/fetch-url`).
JSON responses from the Wikipedia API come back through the proxy's
content-type-agnostic body field. `dependencies: []` (stdlib only),
`fetches_urls: false` (script self-fetches).

The search tools (`serpApiSearch` / `braveSearch`) are SynergyAI's
built-in LLM tools, called directly by the model — no `run_skill_script`
wrapper needed.

## Limitations

- **Wikipedia article QUALITY classification is heuristic.** The script
  returns the article extract; "stub vs. start vs. B-class" is an LLM
  judgment from the extract length, structure, and references count.
- **Sentiment is search-snippet-based.** Without scraping each Reddit
  thread, sentiment is inferred from search snippet text — call it out as
  approximate when uncertain.
- **Subscriber / follower counts are best-effort.** SerpAPI snippets often
  include these; if absent, mark as "not visible in search snippets" rather
  than guessing.
- **No backlink data.** The script doesn't pull from Ahrefs / SEMrush APIs;
  if the user wants DR / backlink context, recommend they bring it.
