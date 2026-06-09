---
name: geo-proposal
description: "Generate a client-ready GEO service proposal from existing audit data — pricing tiers, ROI projection, engagement timeline, terms. Use for: 'GEO proposal', 'sales proposal', 'service proposal', 'proposta', 'preventivo', 'offerta', 'pitch', 'quote for GEO work', 'price quote', 'send proposal to prospect'. Pipeline: gather all GEO-*.md / GEO-*-ANALYSIS.md from ~/Documents/synergyAI/outputs/ -> extract composite GEO score and critical findings -> let the LLM auto-recommend a tier (Premium ≤40, Standard 41-60, Basic 61-75) -> produce a full proposal with executive summary, audit findings summary, three service packages (Basic / Standard / Premium with pricing and inclusions), ROI projection table, 6-month engagement timeline, terms & conditions, and next steps. Reads the same audit pool as geo-report; writes a different deliverable (sales document, not status report)."
dependencies: []
fetches_urls: false
---

# geo-proposal

Generate a fully customized, client-ready GEO **service proposal** that:
1. Pulls findings directly from the prospect's existing GEO audits.
2. Translates technical gaps into business pain points.
3. Presents three pricing tiers with clear inclusions.
4. Auto-recommends the right tier based on the GEO score.
5. Includes a realistic 6-month ROI projection.

Companion to `geo-report` (which produces the status deliverable). This skill
produces the **sales document**.

> ## ✅ REQUIRED FIRST STEP — ALWAYS CALL THE TOOL
>
> When the user asks for a GEO proposal / pitch / quote / preventivo /
> offerta, your **first action** is `run_skill_script` with `dir_name:
> geo-proposal` and `scripts/gather_audits.py`. The script returns the
> inventory of existing audit reports in `~/Documents/synergyAI/outputs/`
> with extracted scores so you can ground the proposal in real data.
>
> If the tool is genuinely missing, say so **after** attempting the call.
>
> ## ⛔ NO FABRICATION
>
> The script prints the **file inventory + per-file content + extracted
> score rollup to stdout** — `run_skill_script` returns it to you.
>
> - Every audit finding, score, and critical issue in the proposal must
>   trace back to a file the script returned. Never invent a "current GEO
>   score" for a prospect whose audit you have not actually run.
> - If the script reports **no audits for this prospect**, do NOT write a
>   speculative proposal. Tell the user: "no audit found for `<domain>` —
>   run `geo-crawlers https://<domain>` (and ideally `geo-citability`,
>   `geo-technical`, `geo-content`) first, then I can write a grounded
>   proposal."
> - **ROI estimates use conservative defaults.** Do not promise specific
>   ranking outcomes. The terms section already states "We guarantee effort
>   and methodology, not specific ranking outcomes" — keep that line.
> - Ask the user for **client name**, **agency name**, **currency**, and
>   **base prices** if those are not obvious from prior context — never
>   guess these.

## Invocation — exact argv shapes

```json
// All available audits (you'll filter mentally to the prospect's domain)
{ "dir_name": "geo-proposal", "script": "scripts/gather_audits.py", "argv": [] }
```

```json
// Filter by prospect domain
{ "argv": ["electron-srl"] }
```

```json
// Specific audit files
{ "argv": ["GEO-CRAWLER-ACCESS-electron-srl.md", "GEO-CITABILITY-ANALYSIS-electron-srl.md"] }
```

## Auto-tier recommendation logic

Based on the composite GEO Readiness Score (from `geo-report`'s formula —
Platform 25% + Content 25% + Technical 20% + Schema 15% + Brand 15%):

| Score | Recommended tier | Reason |
|---|---|---|
| 0-40 | **Premium** | Critical issues require intensive monthly work |
| 41-60 | **Standard** | Structured monthly optimization needed |
| 61-75 | **Basic** | Solid base — maintenance + targeted improvements |
| 76+ | **Basic** or quarterly retainer | Mostly maintenance; spot-check check-ins |

If the user has already specified a tier (e.g. "Standard package proposal"),
honor that and skip the auto-recommend.

## Proposal structure — produce `GEO-PROPOSAL.md`

Follow this structure faithfully. Confirm placeholders (client name, agency
name, currency, prices) with the user if not provided.

**Header.** Title (`# GEO Optimization Proposal`, `## [COMPANY] — AI Search
Visibility`). Prepared by / Prepared for / Date / Valid until (Date + 30
days) / Reference code (`GEO-PROP-YYMMDD-DOMAIN`).

**Executive Summary.** 2-3 paragraphs covering: who the prospect is + industry
+ geography (ask user if not obvious); their GEO Readiness Score with tier
label; "the AI search shift" framing (527% YoY AI-referred traffic growth,
4.4× conversion); top 3 critical issues from the audit; the recommended
tier and price.

**The Opportunity.** Two tables:
- Market-data table (AI growth metrics): AI-referred traffic +527% YoY,
  AI conversion 4.4×, ChatGPT 900M WAU, Google AI Overviews 1.5B users,
  Gartner -50% traditional search by 2028, only 23% of marketers investing
  in GEO today.
- Your Current Position table: `[Company]` vs Industry Avg vs Top
  Performers across GEO Score / AI crawlers allowed / brand mentions /
  schema coverage / llms.txt — fill from the script's extracted data.

**Audit Findings Summary.** 6-row score breakdown table (Platform / Content /
Technical / Schema / Brand / Composite). For each `🔴 Critical issue`, four
lines: What we found / Business impact / Our fix / Timeline.

**Our Solution: Three packages.**

```
BASIC — [PRICE]/month — for score 61-75
- Quarterly full GEO audit (4×/year) + quarterly client report
- Schema.org implementation (Organization + key pages)
- AI crawler access optimization (robots.txt)
- llms.txt creation + maintenance
- Email support (48-hour)
Estimated +10-20 points in 6 months. Minimum 6 months.

STANDARD — [PRICE]/month ⭐ — for score 40-60
Everything in Basic, plus:
- Monthly full GEO audit + delta report (geo-compare)
- Monthly 60-min strategy call
- Content citability optimization (≤10 pages/month)
- Brand authority building (Wikipedia, Wikidata, LinkedIn)
- Platform-specific optimization (AIO / ChatGPT / Perplexity / Gemini / Copilot)
- E-E-A-T improvements (author pages, credentials, freshness)
- Slack channel (24-hour)
Estimated +25-40 points in 6 months. Minimum 6 months.

PREMIUM — [PRICE]/month — for score 0-40 or competitive industries
Everything in Standard, plus:
- Bi-weekly strategy calls
- Technical SEO implementation support (CWV / SSR / speed)
- Full content strategy + production (4 optimized articles/month)
- Active brand building (Reddit, YouTube, industry citations)
- Competitor monitoring + response
- Dedicated account manager
- Priority support (4-hour)
Estimated +40-60 points in 6 months. Minimum 12 months.
```

Mark the recommended tier with ⭐ in the heading. Default prices: Basic
€2,500 / Standard €5,000 / Premium €9,500 — **confirm these with the user
before sending**.

**ROI Projection.** Four-row table: scenario / 6-month score / AI traffic
increase / additional value per month. Use the auto-tier as the highlighted
row. Stated assumptions block (current monthly organic visitors estimate,
AI search projection 25-40% of organic by end of 2026, 4.4× conversion).
Payback period for the recommended tier.

**Engagement Timeline.** Four sections — Month 1 Foundation / Month 2-3
Optimization / Month 4-6 Authority Building / Month 6 Review — with
expected cumulative score improvements per phase.

**Why Us.** Five bullet points: GEO specialists / transparent monthly
reporting / no lock-in beyond minimum / proven 11-dimension methodology /
fast quick-win results within 30 days.

**Investment Summary.** Three-row table (Basic / Standard / Premium) with
Monthly / 6-Month / 12-Month columns. "All prices exclude VAT. Payment
terms: monthly, due within 15 days of invoice."

**Next Steps.** Numbered: review → 30-min call (calendar link placeholder)
→ sign service agreement → kick-off call.

**Terms & Conditions.** Minimum commitment / cancellation 30-day notice /
confidentiality / **"We guarantee effort and methodology, not specific
ranking outcomes"** (keep this line verbatim) / monthly reporting by the
5th / GA + Search Console read access if available.

**Footer.** "*This proposal was prepared using GEO-SEO analysis tools and
reflects findings from the audit of `[DOMAIN]` conducted on `[DATE]`. All
scores and recommendations are based on current industry best practices for
Generative Engine Optimization.*"

## Tone & formatting

- **Confident, professional, business-focused** — written for a decision
  maker (CEO / CMO / founder), not a technical lead.
- **Findings as conclusions**, not possibilities.
- **Specific numbers from the audit** — avoid hand-wavy phrases like "many
  improvements possible". State the score, the specific gap, the specific fix.
- **Conservative ROI** — present a range, state assumptions, never guarantee.
- Tables for data, bullets for inclusions, bold for key terms and prices.
- All URLs absolute. One blank line between sections.

## After the proposal

The user typically wants the proposal as **PDF** for sending. After you
write `GEO-PROPOSAL.md`, recommend the **`geo-report-pdf`** skill to convert
it (when that skill is available). Until then, the markdown is presentable
as-is and can be printed to PDF from any viewer.

## Output

The script prints the **gathered audit inventory + per-file content + score
rollup to stdout** — your proposal-writing input. You write the proposal to
`GEO-PROPOSAL.md` in `~/Documents/synergyAI/outputs/` (`/outputs/` in
Pyodide).

## Pyodide notes

`dependencies: []` and `fetches_urls: false` — filesystem I/O only on the
outputs folder. Identical script footprint to geo-report (each skill is
self-contained per SynergyAI spec).

## Limitations

- **Needs an audit first.** Without audit data the proposal would be
  speculative — refuse rather than guess.
- **Prices are defaults.** Confirm the agency's actual rates before sending.
- **No live market-data refresh.** The "527% YoY", "1.5B Gemini reach",
  "Gartner -50%" numbers are baked into the template; if the user wants
  current figures, they should provide them and you should swap them in.
- **No CRM integration.** Updating prospect records is out of scope; the
  proposal lands as a Markdown file the user shares manually.
