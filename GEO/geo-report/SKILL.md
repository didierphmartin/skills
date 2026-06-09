---
name: geo-report
description: "Aggregate every GEO audit in ~/Documents/synergyAI/outputs/ into a single client-ready GEO Readiness Report. Use for: 'GEO report', 'client report', 'consolidate audits', 'final GEO report', 'GEO deliverable', 'put it all together', 'GEO score summary', 'composite GEO score', 'audit summary for client'. Pipeline: gather all GEO-*.md / GEO-*-ANALYSIS.md / hand-written summaries from the outputs folder -> extract every numeric score marker -> hand the LLM the file inventory plus per-file content (truncated if huge) plus the score-marker rollup. The LLM then computes the composite GEO Readiness Score (Platform 25% + Content 25% + Technical 20% + Schema 15% + Brand 15%) and writes the full 12-section client report following the template in this SKILL — executive summary, GEO dashboard, AI crawler status, brand authority, citability, technical health, schema, llms.txt status, prioritized action plan, competitor comparison if available, appendix."
dependencies: []
fetches_urls: false
---

# geo-report

Aggregate every audit run so far into a **single, client-ready** GEO Readiness
Report. The skill produces the deliverable a business owner or marketing
leader receives — technical findings translated into business impact with
prioritized actions.

> ## ✅ REQUIRED FIRST STEP — ALWAYS CALL THE TOOL
>
> When the user asks for a GEO report / client report / final report /
> consolidated audit, your **first action** is `run_skill_script` with
> `dir_name: geo-report` and `scripts/gather_audits.py`. The script
> enumerates every GEO audit file in `~/Documents/synergyAI/outputs/`,
> extracts the numeric scores, and returns the file inventory + content for
> you to synthesize.
>
> If the tool is genuinely missing, say so **after** attempting the call.
>
> ## ⛔ NO FABRICATION
>
> The script prints the **file inventory + per-file content + extracted
> score rollup to stdout** — `run_skill_script` returns it to you.
>
> - Every numeric score, every per-platform finding, every crawler status,
>   every "Wikipedia present" / "schema missing" claim must come from a file
>   the script returned. Never invent a score for an audit the user did not
>   run.
> - If the script reports that the user has only run `geo-crawlers` and
>   `geo-citability`, say so — tell them which audits are still missing
>   (e.g. "run `geo-technical` and `geo-content` for a complete report") and
>   write a **partial** report covering only the dimensions you have data
>   for, rather than fabricating the missing ones.
> - The composite GEO Readiness Score is computed **from the scores in the
>   files** using the component weights below. If a component is missing,
>   say "[N/A — audit not run]" in the breakdown rather than guessing.

## Invocation — exact argv shapes

```json
// Gather every report in outputs/
{ "dir_name": "geo-report", "script": "scripts/gather_audits.py", "argv": [] }
```

```json
// Filter by domain/substring (matches against filenames AND file content)
{ "argv": ["stripe"] }
```

```json
// Specific files
{ "argv": ["GEO-CRAWLER-ACCESS.md", "GEO-CITABILITY-EXTRACT.md", "GEO-CONTENT-ANALYSIS.md"] }
```

```json
// --list shows files without reading them (quick inventory)
{ "argv": ["--list"] }
```

## What audits the report consolidates

The script reads any of these report files written by the geo-* skills (their
filenames are slightly different per skill — both raw `*-EXTRACT.md` and the
LLM-written `*-ANALYSIS.md` / `*-REPORT.md` / `*-SCORE.md` versions get picked
up):

| Source skill | Typical filename(s) | Provides |
|---|---|---|
| `geo-crawlers` | `GEO-CRAWLER-ACCESS.md` | AI Visibility Score, per-crawler status, Content-Signal |
| `geo-citability` | `GEO-CITABILITY-EXTRACT.md` + LLM `GEO-CITABILITY-SCORE.md` | Citability score, top/bottom blocks, rewrites |
| `geo-content` | `GEO-CONTENT-EXTRACT.md` + LLM `GEO-CONTENT-ANALYSIS.md` | E-E-A-T scores, content metrics |
| `geo-technical` | `GEO-TECHNICAL-EXTRACT.md` + LLM `GEO-TECHNICAL-AUDIT.md` | SSR verdict, canonical, viewport, URL structure |
| `geo-platform-optimizer` | `GEO-PLATFORM-EXTRACT.md` + LLM `GEO-PLATFORM-OPTIMIZATION.md` | Per-platform scores (AIO / ChatGPT / Perplexity / Gemini / Copilot) |
| `geo-llmstxt` | `GEO-LLMSTXT-EXTRACT.md` + LLM `GEO-LLMSTXT-ANALYSIS.md` | llms.txt status |
| `geo-schema` | `GEO-SCHEMA-EXTRACT.md` + LLM `GEO-SCHEMA-REPORT.md` | Schema coverage, sameAs, deprecated types |

The LLM-written `*-ANALYSIS.md` / `*-REPORT.md` / `*-SCORE.md` files (your
own previous work) are preferred over the raw `*-EXTRACT.md` files when both
exist, because they already carry the scored conclusions.

## Composite GEO Readiness Score

```
GEO Score = (Platform × 0.25) + (Content × 0.25) + (Technical × 0.20)
          + (Schema × 0.15) + (Brand × 0.15)
```

Round to nearest integer, cap at 100.

| Component | Weight | Source |
|---|---|---|
| AI Platform Readiness | 25% | `geo-platform-optimizer` combined score, OR mean of 5 platform scores |
| Content Quality & E-E-A-T | 25% | `geo-content` final E-E-A-T score, OR `geo-citability` if content not audited |
| Technical Foundation | 20% | `geo-technical` measurable subtotal (note CWV/Speed need PSI; score the on-page-measurable portion) |
| Schema & Structured Data | 15% | `geo-schema` rubric score |
| Brand Authority & Entity Presence | 15% | from `geo-platform-optimizer`'s entity-grounding signals + `geo-schema`'s sameAs coverage |

If a component file is missing, mark that line `[N/A — audit not run]` and
compute the composite over the components that DO exist (rescale weights to
sum to 1 over the present components). Tell the client which audits are
pending.

## Score interpretation (client-facing)

| Range | Label | Description |
|---|---|---|
| 85-100 | Excellent | Well-positioned for AI search. Focus on maintaining the advantage. |
| 70-84 | Good | Solid foundation with clear opportunities. |
| 55-69 | Moderate | Gaps competitors may be exploiting; structured plan needed. |
| 40-54 | Below Average | Significant barriers; brand risks invisibility in AI answers. |
| 0-39 | Needs Attention | Critical issues; competitors capturing AI search traffic. |

## Report structure — produce `GEO-CLIENT-REPORT.md`

Follow this 12-section structure faithfully. Each section's source notes
which audit data feeds it.

**Section 1 — Executive Summary** (one paragraph, 4-6 sentences). What was
analyzed; overall GEO score with tier; single most impactful finding; top 3
recommendations in one sentence; one-sentence business impact estimate.

**Section 2 — GEO Readiness Score.** The big number, then the component
breakdown table (Platform / Content / Technical / Schema / Brand). Show
weights and weighted score. Mark missing components `[N/A]` if applicable.

**Section 3 — AI Visibility Dashboard.** Per-platform table from
`geo-platform-optimizer`: AIO / ChatGPT / Perplexity / Gemini / Copilot
score + key gap + priority action. One paragraph explanation under the
table.

**Section 4 — AI Crawler Access.** Table of the 14 crawlers from
`geo-crawlers`: crawler / platform / status / impact / recommendation. Add
the "Blocking AI crawlers is like closing your store" framing.

**Section 5 — Brand Authority.** Table from `geo-platform-optimizer`'s
entity-grounding signals + `geo-schema`'s sameAs analysis: Wikipedia /
Wikidata / LinkedIn / YouTube / Reddit / Knowledge Panel / Crunchbase /
GitHub — each as Present/Missing with citation-impact context (e.g. "47.9%
of ChatGPT citations are Wikipedia").

**Section 6 — Citability Analysis.** Top 5 and bottom 5 blocks from
`geo-citability`: URL/section, why citable or not, specific rewrite. Frame
the bottom 5 as "highest-ROI content investment".

**Section 7 — Technical Health.** Status table for CWV / SSR / Mobile /
Security / Page Speed / IndexNow. If SSR is missing from `geo-technical`,
call it out as the highest-impact single fix. For CWV/Speed, note "needs
PageSpeed Insights run" rather than fabricating.

**Section 8 — Schema & Structured Data.** From `geo-schema`: Organization /
Article+Author / sameAs / business-type schema / WebSite+SearchAction /
BreadcrumbList — each Present + Valid status. If schemas are missing, point
to the `schema-generate` skill for production.

**Section 9 — llms.txt Status.** From `geo-llmstxt`: file present? Concise
or extended? Spec violations? Recommendation.

**Section 10 — Prioritized Action Plan.** Three timeline tables:

- **Quick Wins (this week)** — <4 hours per item. Allow crawlers, add
  dates/bylines, fix metas, sameAs additions, llms.txt creation.
- **Medium-Term (this month)** — 1-5 days per item. Restructure top pages
  to question-headings + direct answers, comprehensive Schema markup,
  author pages, CWV optimization, BWT + IndexNow.
- **Strategic (this quarter)** — weeks/months. Wikipedia/Wikidata presence,
  Reddit engagement, YouTube content, SSR migration, topical authority,
  original research program.

Each row: # · Action · Impact (High/Med) · Effort (hours/days/weeks) ·
Platforms affected.

After the tables, an estimated-impact paragraph: Quick Wins delta (+X to +Y
GEO points), full-plan target score, dollar/month range. **Use conservative
estimates.** State assumptions. Never guarantee specific results.

**Section 11 — Competitor Comparison.** Only if competitor audits are also
in `outputs/` (the script reports which domains it found). Side-by-side
table; "Where you lead" + "Where you trail" lists.

**Section 12 — Appendix.** Methodology (pages analyzed, platforms
assessed); data sources; glossary (GEO / AIO / E-E-A-T / SSR / CWV / LCP /
INP / CLS / JSON-LD / sameAs / IndexNow / llms.txt / YMYL / SERP / Topical
Authority).

## Tone & formatting

- **Professional but accessible** — written for a business owner, not a
  developer.
- **Confident and direct** — state findings as conclusions.
- **Action-oriented** — every finding connects to a specific action.
- **Business-impact focused** — translate technical issues into outcomes.
- Avoid: jargon without explanation, hedging, passive voice, excessive
  caveats.
- Use: "Your site does X." / "We recommend Y." / "This impacts Z."
- All URLs absolute. Tables for data, bullets for recommendations.

## Output

The script prints the **gathered audit inventory + per-file content + score
rollup to stdout** — your synthesis input. You write the consolidated
client report to `GEO-CLIENT-REPORT.md` in `~/Documents/synergyAI/outputs/`
(`/outputs/` in Pyodide) — typically 3,000-6,000 words, ready to send.

## Pyodide notes

`dependencies: []` and `fetches_urls: false` — the script does only
filesystem I/O on the outputs folder.

## Limitations

- **No live re-audit.** This skill consolidates *existing* audit data; if a
  component audit is missing, the report flags it but does not re-run it.
- **Composite score is a weighted average of available components.** If
  several audits are missing, the composite is less reliable — note this
  explicitly in the executive summary.
- **Dollar estimates are conservative defaults.** The user should supply
  current traffic numbers and conversion data for a precise estimate; the
  script does not access any analytics.
