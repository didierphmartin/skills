---
name: geo-platform-optimizer
description: "Score and optimize a page for each major AI search platform separately — Google AI Overviews, ChatGPT, Perplexity, Gemini, Bing Copilot. Extracts on-page features, applies per-platform scoring rubrics, marks off-page signals as requires-external-verification, and emits a prioritized action plan."
dependencies: [beautifulsoup4]
fetches_urls: false
---

# geo-platform-optimizer

Only **11% of domains** are cited by both ChatGPT and Google AI Overviews for
the same query. Each AI search platform uses different indexes, ranking logic,
and source preferences — so platform-specific scoring is the GEO foundation,
not a refinement.

## When to use this skill

Trigger phrases (LLM routing): *"optimize for AI Overviews"*, *"optimize for
ChatGPT"*, *"optimize for Perplexity"*, *"optimize for Gemini"*, *"optimize for
Bing Copilot"*, *"AI platform optimization"*, *"which AI engines can find me"*,
*"platform-specific AI SEO"*, *"why ChatGPT cites my competitor not me"*.

## Pipeline (what happens end-to-end)

1. **Fetch the page** (the script does this — never imagine HTML).
2. **Extract on-page features grouped by platform-relevant signals**:
   question-based headings, direct-answer candidates, FAQ section, tables /
   lists, definition patterns, statistics, dates, author, word count, JSON-LD
   `@type`s + `sameAs`, image alt-coverage, multi-modal counts, outbound entity
   links to Wikipedia / Wikidata / LinkedIn / GitHub / YouTube / Reddit / HN.
3. **Apply the per-platform scoring rubric** to those signals — this is the
   LLM's judgment, grounded in script output.
4. **Mark off-page items as `requires external verification`** rather than
   guessing: Wikipedia article existence, Bing index coverage, Knowledge Panel,
   authoritative backlinks, IndexNow registration.
5. **Emit per-platform scores plus a prioritized action plan.**

> ## ✅ REQUIRED FIRST STEP — ALWAYS CALL THE TOOL
>
> When the user asks about AI Overviews / ChatGPT / Perplexity / Gemini /
> Copilot optimization for a page, your **first action** is `run_skill_script`
> with `dir_name: geo-platform-optimizer` and
> `scripts/platform_signals.py`. The script groups on-page signals by which
> platform values them. Score from that, never from a page you imagine.
>
> If the tool is genuinely missing, say so **after** attempting the call.
>
> ## ⛔ NO FABRICATION
>
> The script prints **platform-grouped on-page signals to stdout** —
> `run_skill_script` returns it to you.
>
> - Question-heading counts, table/list counts, FAQ-section presence, word
>   count, image alt-coverage, JSON-LD types, sameAs links, outbound
>   entity-link counts to wikipedia/wikidata/linkedin/github/youtube/reddit:
>   ALL come from the script. Never invent any of them.
> - Per-platform scores are your judgment, grounded in the script's signals.
> - **Off-page signals the script cannot verify** — does a Wikipedia article
>   actually exist about this brand, is the site indexed by Bing, is there a
>   Knowledge Panel, are there authoritative backlinks, is IndexNow
>   registered — must be marked `requires external verification` with a
>   recommendation, not guessed. A fabricated "yes, Wikipedia exists" is
>   worse than an honest "not auto-verifiable — check `en.wikipedia.org`".

## Invocation — exact argv shapes

> **Do NOT add `read_outputs` to this call.** The script prints its full
> extract to stdout — that stdout is your input. The `*-ANALYSIS`/`*-SCORE`
> deliverable is what *you* author afterward, so it does not exist at run time;
> listing it in `read_outputs` returns an empty result, which looks like a
> failed run and triggers needless re-runs. Send only `dir_name`/`script`/`argv`.

```json
{ "dir_name": "geo-platform-optimizer", "script": "scripts/platform_signals.py", "argv": ["https://example.com/blog/my-post"] }
```

The URL is positional. (`--url` exists as a backstop alias.)

## What the script measures vs. what needs external verification

| Signal area | Script measures | LLM marks N/A or external |
|---|---|---|
| On-page structure (Q-headings, FAQ, tables, lists, dates, author, JSON-LD, sameAs) | ✓ | — |
| Multi-modal (images/alt coverage, video/iframe/audio counts, YouTube embeds) | ✓ | — |
| Outbound entity grounding (Wikipedia/Wikidata/LinkedIn/GitHub/YouTube/Reddit/HN anchors on the page) | ✓ | — |
| Wikipedia article actually exists about the brand | — | needs Wikipedia search |
| Wikidata entity with N properties | — | needs Wikidata search |
| Google Knowledge Panel | — | needs Google query |
| Bing Webmaster Tools / Bing index coverage | — | needs BWT login + `site:` query |
| Authoritative backlinks (.edu/.gov/press) | — | needs backlink tool |
| Top-10 ranking for queries | — | needs SERP data |
| Reddit / HN / forum brand mentions | — | needs search |
| IndexNow registration | — | check `/.well-known/` and the API |
| Page-load speed | — | needs PageSpeed Insights |

## Platform rubrics (LLM applies these to the script's signals)

### Google AI Overviews (AIO) — 100 pts

Core: 92% of citations come from top-10 organic, but with strong preference
for clean structure and direct answers.

| Criterion | Pts | Source |
|---|---|---|
| Question-based H2/H3 headings | 10 | script (Q-heading count) |
| Direct-answer candidates after Q-headings | 15 | script samples + LLM judgment on length/clarity |
| Tables present for comparison data | 10 | script |
| Ordered + unordered lists for processes/features | 10 | script |
| FAQ section with ≥5 questions | 10 | script (FAQ detected + count of `?`-headings) |
| Definition patterns ("X is …", "X refers to …") | 5 | script |
| Statistics with attribution | 10 | script + LLM (does the citation actually attribute?) |
| Publication + modified date visible | 5 | script |
| Author byline with credentials | 5 | script (name) + LLM (credentials in body text) |
| Clean H1>H2>H3 hierarchy | 5 | script |
| **Off-page:** ranks in top 10 for target queries | 15 | requires external — mark N/A or request SERP data |

### ChatGPT Web Search — 100 pts

Core: uses Bing's index. Heavy emphasis on entity grounding (Wikipedia
47.9%, Reddit 11.3%) and comprehensive sources.

| Criterion | Pts | Source |
|---|---|---|
| Comprehensive content (≥2000 words) | 10 | script (word count) |
| Outbound entity grounding (sameAs / Wikipedia / Wikidata / LinkedIn links on page) | 15 | script |
| Author byline + credentials | 5 | script + LLM |
| JSON-LD Organization or Person with sameAs | 10 | script |
| **Off-page:** Wikipedia article exists | 20 | requires verification |
| **Off-page:** Wikidata entity ≥5 properties | 10 | requires verification |
| **Off-page:** Bing index coverage | 10 | requires `site:` on Bing |
| **Off-page:** Reddit brand mentions | 10 | requires search |
| **Off-page:** Authoritative backlinks | 10 | requires backlink tool |

### Perplexity AI — 100 pts

Core: Reddit (46.7% of citations) + community + freshness. Cites 5-15 sources
per answer.

| Criterion | Pts | Source |
|---|---|---|
| Content freshness (modified ≤6 months) | 10 | script (dateModified) |
| Original-research markers ("our research", "we surveyed", primary data) | 15 | LLM (body text) |
| Quotable, standalone paragraphs | 10 | LLM (body text) |
| Multi-source claim validation (citations / `<sup>` / external refs) | 10 | script (external link count) |
| Discussion-friendly content cues (opinion, contrarian, original data) | 10 | LLM |
| **Off-page:** Active Reddit presence in relevant subreddits | 20 | requires Reddit search |
| **Off-page:** Forum / HN / Stack Overflow / Quora mentions | 10 | requires search |
| **Off-page:** YouTube video content | 10 | check for YouTube embeds on page + channel verification |
| **Off-page:** Wikipedia / Wikidata presence | 5 | requires verification |

### Google Gemini — 100 pts

Core: Google's index + heavy YouTube weighting + Knowledge Graph + Schema.org.

| Criterion | Pts | Source |
|---|---|---|
| Schema.org structured data (count + types) | 15 | script (JSON-LD types) |
| Image alt-text coverage | 10 | script (% images with alt) |
| YouTube embeds / multi-modal content on page | 10 | script (iframe to youtube.com) |
| E-E-A-T signals (author / about / editorial) on page | 10 | script (author) + LLM |
| Multi-modal richness (text + images + video) | 5 | script (counts) |
| **Off-page:** Google Knowledge Panel exists | 15 | requires Google query |
| **Off-page:** Google Business Profile complete | 10 | requires GBP login |
| **Off-page:** YouTube channel exists with relevant content | 20 | requires YouTube check |
| **Off-page:** Google Merchant Center (if e-commerce) | 5 | requires GMC |

### Bing Copilot — 100 pts

Core: Bing's index. IndexNow for near-instant indexing. Microsoft ecosystem
(LinkedIn / GitHub) weighting.

| Criterion | Pts | Source |
|---|---|---|
| Meta description present + 120-160 chars | 10 | script |
| LinkedIn / GitHub outbound links on page | 10 | script |
| Exact-match keywords in title/H1 (per target topic) | 10 | LLM (user's target topic + script's title/H1) |
| Page-clarity signals (structured headings, dates, schema) | 10 | script |
| **Off-page:** Bing Webmaster Tools verified + sitemap | 15 | requires BWT |
| **Off-page:** IndexNow registered | 15 | requires `/.well-known/` + API ping verification |
| **Off-page:** Bing index coverage | 10 | requires `site:` on Bing |
| **Off-page:** LinkedIn company page complete | 10 | requires LinkedIn check |
| **Off-page:** Page load < 2s | 10 | requires PSI / Lighthouse |

## Combined GEO Score

After scoring each platform: **Combined GEO Score = average of the 5 platform
scores**. If you marked off-page items as `requires external verification`,
present a partial breakdown ("on-page-measurable: X/Y; off-page: needs
verification") rather than a fabricated total. Be explicit about the gap.

**Status thresholds:** Strong ≥70 · Moderate 40-69 · Weak <40.

## Output

The script prints **platform-grouped signal extraction to stdout** — your
scoring input. It also writes `GEO-PLATFORM-EXTRACT.md` and `.json` to
`~/Documents/synergyAI/outputs/` (`/outputs/` in Pyodide). You produce
`GEO-PLATFORM-OPTIMIZATION.md` (the scored audit + prioritized actions).

## Pyodide notes

The script routes the page fetch through `/gpt/backend/api/v1/fetch-url`
under Pyodide and uses `urllib` natively. `fetches_urls: false` because the
script self-fetches.

## Limitations

- **One page per call.** Sitewide entity-grounding (across multiple pages)
  needs a full audit.
- **No off-page verification.** Wikipedia / Wikidata / Bing / Reddit / YouTube
  channel checks need separate external queries. Be honest about this rather
  than guessing.
- **No SERP / backlink data.** Top-10 ranking and authoritative backlinks need
  external tooling.
- **Topic-specific keyword matching** requires the user to supply the target
  topic; the script gives the title/H1 text for the LLM to compare against.
