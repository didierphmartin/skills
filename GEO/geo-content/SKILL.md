---
name: geo-content
description: "Score a page's E-E-A-T (Experience, Expertise, Authoritativeness, Trustworthiness) and content quality for AI citability. Use for: 'E-E-A-T audit', 'EEAT score', 'content quality check', 'is my content trustworthy', 'why isn't my content cited by AI', 'check author credentials', 'evaluate content depth', 'AI content quality audit', 'content trust signals'. Pipeline: fetch the page -> extract main content -> compute exact word/sentence/paragraph metrics -> detect heading hierarchy with skipped-level flags -> extract author/byline/date signals from meta, JSON-LD, and byline patterns -> detect trust-page links (privacy/terms/contact) in footer/nav -> analyze internal vs external links and non-descriptive anchors -> count generic-AI-phrasing tells -> list JSON-LD @types. The LLM applies the 4-pillar E-E-A-T rubric (25 pts each) plus a topical-authority modifier to produce a 0-100 score with specific findings and rewrites."
dependencies: [beautifulsoup4]
fetches_urls: false
---

# geo-content

Evaluate a web page's **E-E-A-T** (Experience, Expertise, Authoritativeness,
Trustworthiness) and content quality — the framework AI search engines use to
decide what deserves to be cited. Per Google's December 2025 Quality Rater
Guidelines, E-E-A-T applies to **all competitive queries** now, not just YMYL.

> ## ✅ REQUIRED FIRST STEP — ALWAYS CALL THE TOOL
>
> When the user asks for an E-E-A-T / content quality / trust audit of a page,
> your **first action** is `run_skill_script` with `dir_name: geo-content` and
> `scripts/content_signals.py`. The script extracts every measurable signal —
> author, dates, trust links, readability metrics, heading hierarchy,
> non-descriptive anchors, generic-phrasing tells. Score from those, never from
> a page you imagine.
>
> If the tool is genuinely missing from your tool list, say so **after**
> attempting the call.
>
> ## ⛔ NO FABRICATION
>
> The script prints the page's **signal extraction to stdout** —
> `run_skill_script` returns it to you.
>
> - Author names, dates, link counts, HTTPS status, word/sentence/paragraph
>   metrics, heading lists, JSON-LD types, generic-phrasing counts: ALL come
>   from the script. Never invent any of them.
> - The four **E-E-A-T scores are your judgment** — apply the rubric below to
>   the script's extracted signals plus the body-text samples it includes.
> - If the script reports the page is empty or JS-rendered, say so; do not
>   score an empty extraction.

## Invocation — exact argv shapes

> **Do NOT add `read_outputs` to this call.** The script prints its full
> extract to stdout — that stdout is your input. The `*-ANALYSIS`/`*-SCORE`
> deliverable is what *you* author afterward, so it does not exist at run time;
> listing it in `read_outputs` returns an empty result, which looks like a
> failed run and triggers needless re-runs. Send only `dir_name`/`script`/`argv`.

```json
{ "dir_name": "geo-content", "script": "scripts/content_signals.py", "argv": ["https://example.com/blog/my-post"] }
```

The URL is positional. (`--url` exists as a backstop alias.)

## Division of labor

| Step | Who | What |
|---|---|---|
| 1. Fetch + extract | **script** | Fetch page; strip nav/header/footer; segment content |
| 2. Measure | **script** | Word/sentence/paragraph metrics, heading hierarchy, link analysis |
| 3. Detect signals | **script** | Author, dates, trust links, JSON-LD, generic phrasing |
| 4. Score E-E-A-T | **you (LLM)** | Apply the 4-pillar rubric (25 pts each) to the extracted signals |
| 5. Recommend | **you (LLM)** | Concrete findings + improvement actions |

## E-E-A-T Framework (100 points total)

### Experience — 25 pts
First-hand knowledge and direct involvement.

| Signal | Pts | Score |
|---|---|---|
| First-person accounts ("I tested…", "We implemented…") | 5 | 5 specific / 3 generic / 0 absent |
| Original research or data | 5 | 5 original / 3 references original / 0 none |
| Case studies with specific results | 4 | 4 detailed w/ numbers / 2 general / 0 none |
| Screenshots/photos/evidence of direct use | 3 | 3 authentic / 1 stock / 0 none |
| Specific examples from personal experience | 4 | 4 specific+unique / 2 somewhat / 0 generic |
| Process demonstrations (not just outcomes) | 4 | 4 step-by-step / 2 partial / 0 none |

Flag as weak: pure summary of other sources, generic "it depends" advice, no
direct usage/testing mentioned, hedging ("reportedly", "supposedly").

### Expertise — 25 pts
Demonstrated knowledge depth.

| Signal | Pts | Score |
|---|---|---|
| Visible author credentials (bio, degrees, certs) | 5 | 5 full / 3 basic / 0 no author |
| Technical depth appropriate to topic | 5 | 5 thorough / 3 adequate / 0 superficial |
| Methodology explanation | 4 | 4 clear / 2 some / 0 none |
| Data-backed claims (stats, research citations) | 4 | 4 well-sourced / 2 some / 0 unsupported |
| Industry-specific terminology used correctly | 3 | 3 accurate / 1 basic / 0 errors |
| Dedicated author page | 4 | 4 detailed / 2 brief / 0 none |

Flag: claims without evidence, surface coverage of complex topics, misused
jargon, no visible author with relevant credentials.

### Authoritativeness — 25 pts
Recognition by others.

| Signal | Pts | Score |
|---|---|---|
| Inbound citations from authoritative sources | 5 | 5 major / 3 some / 0 none |
| Author quoted/cited in press/media | 4 | 4 media / 2 industry / 0 none |
| Industry awards/recognition mentioned | 3 | 3 relevant / 1 tangential / 0 none |
| Speaker credentials (conferences) | 3 | 3 listed / 0 none |
| Published in peer-reviewed or respected outlets | 4 | 4 tier-1 / 2 industry / 0 none |
| Comprehensive topic coverage (topical authority) | 3 | 3 thorough / 1 some / 0 isolated |
| Wikipedia or authoritative encyclopedic mention | 3 | 3 Wikipedia / 2 other / 0 none |

Flag: single-topic site with no depth, no external validation, self-proclaimed
"expert" without evidence.

### Trustworthiness — 25 pts
Reliability and transparency. **Most signals here are extracted by the script.**

| Signal | Pts | Script extracts? |
|---|---|---|
| Contact info visible (address/phone/email) | 4 | partial — contact-page link |
| Privacy policy linked | 2 | ✓ |
| Terms of service linked | 1 | ✓ |
| HTTPS valid | 2 | ✓ |
| Editorial standards / corrections policy | 3 | — |
| Transparent business model / conflict disclosure | 3 | — |
| Real reviews/testimonials | 3 | — |
| Accurate claims (no misinformation detected) | 4 | — |
| Clear affiliate/sponsorship disclosures | 3 | — |

Score deterministic items from the script; judgment items from the body text
sample it includes.

## Content Quality Metrics (deterministic — from script)

**Word-count floors (not targets):**

| Page type | Min | Ideal | Notes |
|---|---|---|---|
| Homepage | 500 | 500-1,500 | Clear value prop, not wall of text |
| Blog post | 1,500 | 1,500-3,000 | Thorough but focused |
| Pillar / ultimate guide | 2,000 | 2,500-5,000 | Comprehensive coverage |
| Product page | 300 | 500-1,500 | Descriptions, specs, use cases |
| Service page | 500 | 800-2,000 | What/how/why/for-whom |
| About page | 300 | 500-1,000 | Story + credentials |
| FAQ | 500 | 1,000-2,500 | Thorough answers, not one-liners |

**Readability:**
- Avg sentence length 15-20 words is ideal (script reports actual).
- Avg paragraph length 2-4 sentences.
- One H1 per page; no skipped heading levels (script flags both).
- Internal links: 3-5 to related pages, descriptive anchors (script counts
  "click here" / "here" / "read more" non-descriptive anchors).

## AI-content quality tells (the script counts these)

Common low-quality-AI-content opener patterns the script regex-matches:
"In today's fast-paced world…", "It's important to note…", "At the end of the
day…", "delve into", "navigate the complex landscape", "unlock the power of",
"in the ever-evolving", "in this article we will explore", etc. The script
reports a count + samples — a high count signals shallow AI content; the LLM
weighs this against the body text it also receives.

## Topical authority modifier (LLM judgment)

| Level | Description | Score impact |
|---|---|---|
| Authority | 20+ pages, strong clustering | +10 |
| Developing | 10-20 pages, some clustering | +5 |
| Emerging | 5-10 pages, limited clustering | 0 |
| Thin | <5 pages, no clustering | -5 |

The script analyzes ONE page — say "requires sitewide analysis (pair with
`geo-audit` / `geo-compare`)" and default the modifier to 0 unless the user
provides sitewide context.

## Scoring procedure

1. From the script's signal extraction, score each E-E-A-T pillar (0-25).
2. Subtotal /100.
3. Apply topical-authority modifier (default 0, range -5 to +10).
4. Final score capped at 100.

**Interpretation:** 85-100 exceptional · 70-84 good · 55-69 average · 40-54
below average · 0-39 poor.

## Output format

Produce `GEO-CONTENT-ANALYSIS.md`:

```markdown
# GEO Content Quality & E-E-A-T Analysis — [Page]

**URL:** [URL] · **Date:** [Date]
**Content Score: [X]/100**

## E-E-A-T Breakdown
| Dimension | Score | Key Finding |
|---|---|---|
| Experience | X/25 | [one-line] |
| Expertise | X/25 | [one-line] |
| Authoritativeness | X/25 | [one-line] |
| Trustworthiness | X/25 | [one-line] |
| Subtotal | X/100 | |
| Topical Authority Modifier | [+/-X] | [reason or "requires sitewide audit"] |

## Page Metrics (from script)
| Metric | Value | Verdict |
|---|---|---|
| Word count | N | [vs floor for page type] |
| Avg sentence length | X words | [ideal 15-20] |
| Avg paragraph length | X sentences | [ideal 2-4] |
| H1 count | N | [should be 1] |
| Skipped heading levels | [list] | [pass/fail] |
| Internal / external links | N / M | |
| Non-descriptive anchors | K | |
| Author detected | yes/no — [name] | |
| Published / modified | [dates] | |
| JSON-LD @types | [list] | |
| Generic-phrasing tells | K matches | [samples] |

## Detailed findings
### Experience [LLM analysis using body text]
### Expertise
### Authoritativeness
### Trustworthiness

## Improvement Recommendations
### Quick Wins  ### Author / E-E-A-T  ### Content gaps
```

## Output

The script prints the **signal extraction to stdout** — that is your scoring
input. It also writes `GEO-CONTENT-EXTRACT.md` and `.json` to
`~/Documents/synergyAI/outputs/` (`/outputs/` in Pyodide). You produce
`GEO-CONTENT-ANALYSIS.md` (the scored report) from your scoring.

## Pyodide notes

The script routes the page fetch through `/gpt/backend/api/v1/fetch-url` under
Pyodide and uses `urllib` under native CPython. `fetches_urls: false` because
the script self-fetches.

## Limitations

- **One page per call.** Topical authority needs a sitewide audit
  (`geo-audit`/`geo-compare`).
- **JavaScript-rendered content is invisible.** SPAs without SSR extract empty
  — the script reports this rather than scoring 0.
- **External authority signals** (backlinks, press mentions) require off-page
  data the script does not collect — score from on-page evidence and note gaps.
